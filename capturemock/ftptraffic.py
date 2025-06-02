
import ftplib
from pprint import pformat
from capturemock import traffic, encodingutils, clientservertraffic, fileedittraffic
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
import io, sys, os

class FTPTrafficHandler(FTPHandler):
    dispatcher = None
    requestCount = 0
    dummyFileName = "ftp_dummy.txt"
    authorizer = DummyAuthorizer()
    authorizer.add_user("capturemock", "capturemock", ".", perm='elradfmwMT')
    def pre_process_command(self, line, cmd, arg):
        if os.path.isfile(self.dummyFileName):
            os.remove(self.dummyFileName)
        internal_cmd = self.username == "capturemock" or (cmd == "USER" and arg == "capturemock")
        if not internal_cmd:
            FTPTrafficHandler.requestCount += 1
            traffic = FtpClientTraffic(cmd, arg, rcHandler=self.dispatcher.rcHandler, handler=self)
            self.dispatcher.process(traffic, self.requestCount)
        if cmd != "LIST":
            FTPHandler.pre_process_command(self, line, cmd, arg)
            
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
            dummy = os.path.join(os.path.dirname(fn), self.dummyFileName)
            FTPHandler.ftp_STOR(self, dummy, mode)

    def is_success(self, response):
        return response.startswith("2") or response.startswith("3")

    def handle_cmd_response(self, cmd, arg, response):
        if self.is_success(response):
            self.dispatcher.diag.debug("handle response " + cmd + " " + arg + " " + response)
            if cmd == "PASS":
                ftp_dir = fileedittraffic.FileEditTraffic.replayFileEditDir or fileedittraffic.FileEditTraffic.recordFileEditDir
                try:
                    # make sure directory exists, don't have race conditions with other capturemock instances
                    os.makedirs(ftp_dir)
                except FileExistsError:
                    pass
                self.authorizer.add_user(self.username, arg, ftp_dir, perm='elradfmwMT')
    

class FtpTrafficServer:
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls((address, 0), dispatcher)
    
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
            try:
                with io.BytesIO(b"") as f:
                    ftp.storbinary("STOR CAPTUREMOCK_SHUTDOWN", f)
            except EOFError:
                # We expect this, the server will shutdown and not respond to the request
                pass

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
            return [ FtpListTraffic(lines, handler=self.handler), FtpServerTraffic(self.cmd, self.arg, response, handler=self.handler)  ]
        elif self.cmd == "RETR":
            parts = []
            try:
                response = self.serverFtp.retrbinary(self.text, lambda d: parts.append(d))
                return [ FtpFileTraffic(self.arg, b"".join(parts)), FtpServerTraffic(self.cmd, self.arg, response, handler=self.handler)  ]
            except ftplib.Error as e:
                return [ FtpServerTraffic(self.cmd, self.arg, str(e), handler=self.handler) ]
        else:
            response = self.serverFtp.sendcmd(self.text)
            return [ FtpServerTraffic(self.cmd, self.arg, response, handler=self.handler) ]
                    
    @classmethod
    def isClientClass(cls):
        return True
    
    def makeResponseTraffic(self, rawText, responseClass, rcHandler):
        if responseClass is FtpServerTraffic:
            return responseClass(self.cmd, self.arg, rawText, self.handler)
        elif responseClass is FtpListTraffic:
            return responseClass(eval(rawText), self.handler)
        elif responseClass is FtpFileTraffic:
            return responseClass(rawText)
        else:
            return super(FtpClientTraffic, self).makeResponseTraffic(rawText, responseClass, rcHandler)

class FtpServerTraffic(clientservertraffic.ServerTraffic):
    def __init__(self, cmd, arg, response, handler):
        clientservertraffic.ServerTraffic.__init__(self, response, None)
        self.cmd = cmd
        self.arg = arg
        self.handler = handler
    
    def forwardToDestination(self):
        if self.handler:
            self.handler.handle_cmd_response(self.cmd, self.arg, self.text)
        return []
    
class FtpListTraffic(clientservertraffic.ServerTraffic):
    typeId = "LST"
    def __init__(self, lines, handler):
        clientservertraffic.ServerTraffic.__init__(self, pformat(lines), None)
        self.handler = handler
        self.lines = lines
    
    def forwardToDestination(self):
        if self.handler:
            self.handler.push_dtp_data("\n".join(self.lines).encode(), cmd="LIST")
        return []
    
class FtpFileTraffic(clientservertraffic.ServerTraffic):
    typeId = "FIL"
    def __init__(self, fn, data=None):
        clientservertraffic.ServerTraffic.__init__(self, fn, None)
        self.fn = os.path.basename(fn)
        self.data = data
    
    def forwardToDestination(self):
        recordDir = fileedittraffic.FileEditTraffic.recordFileEditDir
        if recordDir and self.data:
            with open(os.path.join(recordDir, self.fn), "wb") as f:
                f.write(self.data)
        return []
    