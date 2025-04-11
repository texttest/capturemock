
import ftplib
from pprint import pformat
from capturemock import traffic, encodingutils, clientservertraffic
from datetime import datetime
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
import io, sys, logging, os

class FTPTrafficHandler(FTPHandler):
    dispatcher = None
    requestCount = 0
    authorizer = DummyAuthorizer()
    authorizer.add_user("capturemock", "capturemock", ".", perm='elradfmwMT')
    def pre_process_command(self, line, cmd, arg):
        if self.username == "capturemock" or (cmd == "USER" and arg == "capturemock"):
            FTPHandler.pre_process_command(self, line, cmd, arg)
        else:
            FTPTrafficHandler.requestCount += 1
            traffic = FtpClientTraffic(cmd, arg, rcHandler=self.dispatcher.rcHandler, handler=self)
            self.dispatcher.process(traffic, self.requestCount)
            
    def ftp_STOR(self, path, mode="w"):
        fn = os.path.basename(path)
        if fn == "CAPTUREMOCK_SHUTDOWN":
            sys.exit(0)
        elif fn.startswith("SUT_SERVER="):
            servAddr = fn.split("=", 1)[-1]
            self.dispatcher.diag.debug("Got server address " + servAddr)
            host, port = servAddr.split(":")
            ftp = ftplib.FTP()
            ftp.connect(host, int(port))
            clientservertraffic.ClientSocketTraffic.setServerLocation(servAddr, clientRecord=True)
            FtpClientTraffic.serverFtp = ftp
            FTPHandler.ftp_STOR(self, fn, mode)

    
class FtpTrafficServer:
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls((address.replace("127.0.0.1", "localhost"), 0), dispatcher)
    
    def __init__(self, address, dispatcher):
        self.count = 0
        self.dispatcher = dispatcher
        FTPTrafficHandler.dispatcher = dispatcher
        self.host = address[0]
        self.server = FTPServer(address, FTPTrafficHandler)

    def getAddress(self):
        values = self.server.socket.getsockname()
        return self.host + ":" + str(values[1])
        
    def run(self):
        logging.basicConfig(filename='/var/log/pyftpd.log', level=logging.INFO)
        self.server.serve_forever()

    @staticmethod
    def getTrafficClasses(incoming):
        return [ FtpClientTraffic, FtpServerTraffic, FtpListTraffic, FtpFileTraffic ]
    
    @classmethod
    def sendTerminateMessage(cls, serverAddressStr):
        host, port = serverAddressStr.split(":")
        with ftplib.FTP() as ftp:
            ftp.connect(host, int(port))
            ftp.login("capturemock", "capturemock")
            with io.BytesIO(b"") as f:
                ftp.storbinary("STOR CAPTUREMOCK_SHUTDOWN", f)

class FtpServerStateTraffic(clientservertraffic.ServerStateTraffic):
    def __init__(self, dest, rcHandler):
        clientservertraffic.ServerStateTraffic.__init__(self, "STOR SUT_SERVER <address>", dest, None, rcHandler)


class FtpClientTraffic(clientservertraffic.ClientSocketTraffic):
    direction = "<-"
    serverFtp = None
    def __init__(self, cmd, arg, rcHandler=None, handler=None):
        self.handler = handler
        self.cmd = cmd
        self.arg = arg
        text = self.cmd
        if self.arg:
            text += " " + self.arg
        clientservertraffic.ClientSocketTraffic.__init__(self, text, None, rcHandler)

    def forwardToServer(self):
        if self.cmd == "LIST":
            lines = []
            response = self.serverFtp.retrlines(self.text, lambda l: lines.append(l))
            return [ FtpListTraffic(lines, handler=self.handler), FtpServerTraffic(response, handler=self.handler)  ]
        elif self.cmd == "RETR":
            parts = []
            response = self.serverFtp.retrbinary(self.text, lambda d: parts.append(d))
            return [ FtpFileTraffic(self.arg, b"".join(parts), handler=self.handler), FtpServerTraffic(response, handler=self.handler)  ]
        else:
            response = self.serverFtp.sendcmd(self.text)
            return [ FtpServerTraffic(response, handler=self.handler) ]
                    
    @classmethod
    def isClientClass(cls):
        return True

class FtpServerTraffic(clientservertraffic.ServerTraffic):
    def __init__(self, response, handler):
        clientservertraffic.ServerTraffic.__init__(self, response, None)
        self.handler = handler
    
    def forwardToDestination(self):
        if self.handler:
            self.handler.respond(self.text)
        return []
    
class FtpListTraffic(clientservertraffic.ServerTraffic):
    typeId = "LST"
    def __init__(self, lines, handler):
        clientservertraffic.ServerTraffic.__init__(self, pformat(lines), None)
        self.handler = handler
        self.lines = lines
    
    def forwardToDestination(self):
        if self.handler:
            self.handler.push_dtp_data("\n".join(self.lines), cmd="LIST")
        return []
    
class FtpFileTraffic(clientservertraffic.ServerTraffic):
    typeId = "LST"
    def __init__(self, fn, data, handler):
        clientservertraffic.ServerTraffic.__init__(self, fn, None)
        self.handler = handler
        self.fn = fn
        self.data = data
    
    def forwardToDestination(self):
        if self.handler:
            self.handler.push_dtp_data(self.data, cmd="RETR")
        return []
    