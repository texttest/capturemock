""" Traffic classes for capturing client-server interaction """

import socket, sys
from capturemock import traffic, encodingutils
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from datetime import datetime

try:
    import xmlrpclib
except ImportError:
    import xmlrpc.client as xmlrpclib

class ClientSocketTraffic(traffic.Traffic):
    destination = None
    direction = "<-"
    socketId = ""
    typeId = "CLI"
    @classmethod
    def isClientClass(cls):
        return True

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
    headerStr = "--HEA:"
    ignoreHeaders = [ "Content-Length", "Host", "User-Agent", "traceparent", "tracestate", "Connection", "Origin", "Referer"] # provided automatically, or not usable when recorded
    defaultValues = {"Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "identity"}
    def __init__(self, text=None, responseFile=None, rcHandler=None, method="GET", path="/", headers={}, handler=None):
        if responseFile is None: # replay
            parts = text.split(None, 2)
            self.headers = {}
            self.method = parts[0]
            if len(parts) > 2:
                self.path = parts[1]
                textStr = self.extractHeaders(parts[2])
                self.payload = encodingutils.encodeString(textStr) if textStr else None
            else:
                self.path = self.extractHeaders(parts[1])
                textStr = ""
                self.payload = None
        else:
            self.method = method
            self.path = path
            self.payload = text
            textStr = encodingutils.decodeBytes(text)
            self.headers = headers
        self.handler = handler
        mainText = self.method + " " + self.path
        if self.payload is not None:
            text = mainText + " " + textStr
        else:
            text = mainText
        if rcHandler and rcHandler.getboolean("record_timestamps", [ "general" ], False):
            text += "\n--TIM:" + datetime.now().isoformat()
        for header, value in self.headers.items():
            if header not in self.ignoreHeaders and self.defaultValues.get(header) != value and not header.lower().startswith("sec-"):
                text += "\n" + self.headerStr + header + "=" + value
        ClientSocketTraffic.__init__(self, text, responseFile, rcHandler)
        self.text = self.applyAlterations(self.text)

    def extractHeaders(self, textStr):
        if HTTPClientTraffic.headerStr in textStr:
            parts = textStr.split(HTTPClientTraffic.headerStr)
            for headerStr in parts[1:]:
                header, value = headerStr.strip().split("=", 1)
                self.headers[header] = value
            return self.stripNewline(parts[0])
        else:
            return self.stripNewline(textStr)
        
    def stripNewline(self, text):
        return text[:-1] if text.endswith("\n") else text
        
    def forwardToServer(self):
        try:
            request = Request(self.destination + self.path, data=self.payload, headers=self.headers, method=self.method)
            response = urlopen(request)
            text = encodingutils.decodeBytes(response.read())
            text = self.applyAlterations(text)
            return [ HTTPServerTraffic(response.status, text, response.getheaders(), self.responseFile, handler=self.handler) ]
        except HTTPError as e:
            text = encodingutils.decodeBytes(e.read())
            text = self.applyAlterations(text)
            return [ HTTPServerTraffic(e.code, text, {}, self.responseFile, handler=self.handler) ]
        
    def makeResponseTraffic(self, text, responseClass, rcHandler):
        status, body = text.split(" ", 1)
        body = self.applyAlterations(body)
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
        if self.handler:
            self.handler.send_response(self.status)
            for hdr, value in self.headers:
                # Might need to handle chunked transfer, for now, just ignore it and return it as one
                if hdr.lower() != "transfer-encoding" or value.lower() != "chunked":
                    self.handler.send_header(hdr, value)
            self.handler.send_header('Access-Control-Allow-Origin', '*')
            self.handler.end_headers()
        self.write(self.body)
        # Don't close the file, the HTTP server mechanism does that for us
        return []
    
    def write(self, message):
        if self.responseFile:
            self.responseFile.write(encodingutils.encodeString(message))

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
