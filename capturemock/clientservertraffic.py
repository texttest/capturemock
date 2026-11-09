""" Traffic classes for capturing client-server interaction """

import socket, sys
from capturemock import traffic

class ClientSocketTraffic(traffic.Traffic):
    destination = None
    broadcast = False
    broadcast_connection_timeout = 0.2
    direction = "<-"
    socketId = ""
    socketType = socket.SOCK_STREAM
    typeId = "CLI"
    @classmethod
    def isClientClass(cls):
        return True
    
    def __init__(self, text, responseFile, rcHandler=None, **kw):
        ts = self.get_timestamp(rcHandler)
        super(ClientSocketTraffic, self).__init__(text, responseFile, rcHandler=rcHandler, timestamp=ts)
        self.text = self.applyAlterations(self.text)
        self.connectionFailed = False
        
    def forwardToDestination(self):
        return self.forwardToServer() if self.destination is not None else []

    @classmethod
    def setServerLocation(cls, location, clientRecord=False):
        cls.destination = location
        if not clientRecord:
            # If we get a server state message, switch the order around
            cls.direction = "->"
            ServerTraffic.direction = "<-"
            
    @classmethod
    def sendTerminateMessage(cls, serverAddressStr, *args):
        host, port = serverAddressStr.split(":")
        cls._sendTerminateMessage((host, int(port)), *args)

    @classmethod
    def _sendTerminateMessage(cls, serverAddress, socketType=None):
        sendSocket = socket.socket(socket.AF_INET, socketType or cls.socketType)
        sendSocket.connect(serverAddress)
        sendSocket.sendall("TERMINATE_SERVER\n".encode())
        sendSocket.shutdown(2)
        
    def shouldBeRecorded(self, *args):
        return not self.connectionFailed and traffic.Traffic.shouldBeRecorded(self, *args)

    @classmethod
    def connectServerSocket(cls):
        if cls.destination:
            sock = socket.socket(socket.AF_INET, cls.socketType)
            try:
                if cls.broadcast:
                    dstHost, dstPort = cls.destination
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.bind((dstHost, 0))
                else:
                    sock.connect(cls.destination)
                return sock
            except OSError as e:
                sys.stderr.write("WARNING: Could not forward to server " + str(cls.destination) + "\n")
                sys.stderr.write(str(e) + "\n")
                sock.close()

    def readResponses(self, sock):
        if self.broadcast:
            sock.settimeout(self.broadcast_connection_timeout)
            responses = []
            try:
                while True:
                    responses.append(sock.recv(1024).decode())
            except socket.timeout:
                return responses
        else:
            return [ sock.makefile().read() ]
            
    def forwardToServer(self):
        sock = self.connectServerSocket()
        if not sock:
            sys.stderr.write("-- " + self.text + "\n")
            self.connectionFailed = True
            return []
        if self.broadcast:
            sock.sendto(self.text.encode(), ("255.255.255.255", self.destination[1]))
        else:
            sock.sendall(self.text.encode())
        try:
            sock.shutdown(socket.SHUT_WR)
            responses = self.readResponses(sock)
            sock.close()
            alteredResponses = [ self.applyAlterations(resp) for resp in responses ]
            return [ ServerTraffic(response, self.responseFile) for response in alteredResponses ]
        except socket.error:
            sys.stderr.write("WARNING: Server process reset the connection while TextTest's 'fake client' was trying to read a response from it!\n")
            sock.close()
            return []
        
    def makeResponseTraffic(self, rawText, responseClass, rcHandler):
        if responseClass is ServerTraffic:
            text = self.applyAlterations(rawText)
            return super().makeResponseTraffic(text, responseClass, rcHandler)
        else:
            return super().makeResponseTraffic(rawText, responseClass, rcHandler)


class ServerTraffic(traffic.Traffic):
    typeId = "SRV"
    direction = "->"


class ServerStateTraffic(ServerTraffic):
    def __init__(self, inText, dest, responseFile, rcHandler):
        ServerTraffic.__init__(self, inText, responseFile, rcHandler)
        self.clientRecord = rcHandler.getboolean("record_for_client", [ "general" ], False)
        if dest:
            ClientSocketTraffic.setServerLocation(dest, self.clientRecord)

    def forwardToDestination(self):
        return []
    
    def record(self, *args):
        if not self.clientRecord:
            ServerTraffic.record(self, *args)
    
class ClassicServerStateTraffic(ServerStateTraffic):
    socketId = "SUT_SERVER"
    def __init__(self, inText, *args):
        dest = None
        lastWord = inText.strip().split()[-1]
        if ":" in lastWord:
            host, port = lastWord.split(":")
            if port.isdigit():
                dest = host, int(port)
        ServerStateTraffic.__init__(self, inText, dest, *args)
