""" Traffic classes for capturing client-server interaction """

import socket, sys, base64
from capturemock import traffic
from urllib.request import urlopen, Request
from locale import getpreferredencoding
from urllib.error import HTTPError
from pprint import pformat

try:
    import xmlrpclib
except ImportError:
    import xmlrpc.client as xmlrpclib

class ClientSocketTraffic(traffic.Traffic):
    destination = None
    direction = "<-"
    socketId = ""
    typeId = "CLI"
    def forwardToDestination(self):
        return self.forwardToServer() if self.destination is not None else []

    @classmethod
    def setServerLocation(cls, location, clientRecord=False):
        if cls.destination is None:
            cls.destination = location
            if not clientRecord:
                # If we get a server state message, switch the order around
                cls.direction = "->"
                ServerTraffic.direction = "<-"

    def forwardToServer(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(self.destination)
        sock.sendall(self.text.encode())
        try:
            sock.shutdown(socket.SHUT_WR)
            response = sock.makefile().read()
            sock.close()
            return [ ServerTraffic(response, self.responseFile) ]
        except socket.error:
            sys.stderr.write("WARNING: Server process reset the connection while TextTest's 'fake client' was trying to read a response from it!\n")
            sock.close()
            return []


class XmlRpcClientTraffic(ClientSocketTraffic):
    def __init__(self, text="", responseFile=None, rcHandler=None, method=None, params=None):
        if method is not None:
            self.method = method
            self.params = params
            text = self.method + repr(params).replace(",)", ")")
        ClientSocketTraffic.__init__(self, text, None, rcHandler)
        if method is not None: # record
            self.text = self.applyAlterations(self.text)
        else: # replay
            self.method, paramText = text.rstrip().split("(", 1)
            paramText = "(" + paramText
            paramText = self.applyAlterationVariables(paramText)
            if "," not in paramText and paramText != "()":
                paramText = paramText[:-1] + ",)" # make proper tuple output
            self.params = eval(paramText)

    def forwardToServer(self):
        try:
            responseObject = getattr(self.destination, self.method)(*self.params)
        except xmlrpclib.Fault as e:
            responseObject = e

        text = self.applyAlterations(self.fixMultilineStrings(responseObject))
        return [ XmlRpcServerTraffic(text=text, responseObject=responseObject) ]

    def getXmlRpcResponse(self):
        return "" # not a response in the xmlrpc sense...
    
class HTTPClientTraffic(ClientSocketTraffic):
    jwtStr = "\n--JWT:"
    def __init__(self, text=None, responseFile=None, rcHandler=None, method="GET", path="/", headers={}, handler=None):
        if responseFile is None: # replay
            parts = text.split(" ", 2)
            method = parts[0]
            self.path = parts[1]
            if len(parts) > 2:
                textStr = parts[2]
                if HTTPClientTraffic.jwtStr in textStr:
                    textStr = textStr.split(HTTPClientTraffic.jwtStr, 1)[0]
                self.payload = textStr.encode(getpreferredencoding())
            else:
                textStr = ""
                self.payload = None
        else:
            self.path = path
            self.payload = text
            textStr = text.decode(getpreferredencoding()) if text else ""
        self.headers = headers
        self.handler = handler
        mainText = method + " " + self.path
        if self.payload is not None:
            text = mainText + " " + textStr
        else:
            text = mainText
        auth = self.headers.get("Authorization")
        if auth:
            jwt = self.decodeJwt(auth)
            if jwt:
                text += HTTPClientTraffic.jwtStr + jwt
        ClientSocketTraffic.__init__(self, text, responseFile, rcHandler)
        self.text = self.applyAlterations(self.text)
        
    def decodeJwt(self, authStr):
        if authStr.count(".") != 2:
            return 
        
        payloadPart = authStr.split(".")[1]
        while len(payloadPart) % 4 != 0:
            payloadPart += '='
        
        payload = base64.urlsafe_b64decode(payloadPart)
        payloadStr = str(payload, "utf-8")
        return pformat(eval(payloadStr))

    def forwardToServer(self):
        try:
            request = Request(self.destination + self.path, data=self.payload, headers=self.headers)
            response = urlopen(request)
            text = str(response.read(), getpreferredencoding())
            return [ HTTPServerTraffic(response.status, text, response.getheaders(), self.responseFile, handler=self.handler) ]
        except HTTPError as e:
            return [ HTTPServerTraffic(e.code, e.reason, {}, self.responseFile, handler=self.handler) ]
        
    def makeResponseTraffic(self, text, responseClass, rcHandler):
        status, body = text.split(" ", 1)
        return responseClass(int(status), body, {}, self.responseFile, self.handler)



class ServerTraffic(traffic.Traffic):
    typeId = "SRV"
    direction = "->"


class XmlRpcServerTraffic(ServerTraffic):
    def __init__(self, text="", responseFile=None, rcHandler=None, responseObject=None):
        if responseObject is not None:
            self.responseObject = responseObject
            if isinstance(responseObject, xmlrpclib.Fault):
                text = "raise xmlrpclib.Fault(" + repr(responseObject.faultCode) + ", " + repr(responseObject.faultString) + ")"
        else:
            raiseException = text.startswith("raise ")
            if raiseException:
                text = text[6:]
            self.responseObject = eval(text)
        ServerTraffic.__init__(self, text, None, rcHandler)

    def getXmlRpcResponse(self):
        if isinstance(self.responseObject, xmlrpclib.Fault):
            raise self.responseObject
        else:
            return self.responseObject
        
class HTTPServerTraffic(ServerTraffic):
    def __init__(self, status, text, headers, responseFile, handler):
        self.body = text
        ServerTraffic.__init__(self, str(status) + " " + text, responseFile)
        self.status = status
        self.headers = headers
        self.handler = handler
    
    def forwardToDestination(self):
        self.handler.send_response(self.status)
        for hdr, value in self.headers:
            self.handler.send_header(hdr, value)
        self.handler.end_headers()
        self.write(self.body)
        # Don't close the file, the HTTP server mechanism does that for us
        return []


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
    
class HTTPServerStateTraffic(ServerStateTraffic):
    def __init__(self, dest, rcHandler):
        ServerStateTraffic.__init__(self, "POST /capturemock/setServerLocation <address>", dest, None, rcHandler)            


class XmlRpcServerStateTraffic(ServerStateTraffic):
    def __init__(self, dest, rcHandler):
        ServerStateTraffic.__init__(self, "setServerLocation(<address>)", xmlrpclib.ServerProxy(dest), None, rcHandler)
