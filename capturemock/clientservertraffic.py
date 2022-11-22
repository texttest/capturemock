""" Traffic classes for capturing client-server interaction """

import socket, sys, os
from capturemock import traffic, encodingutils
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from capturemock.fileedittraffic import FileEditTraffic

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
    def sendTerminateMessage(cls, serverAddressStr):
        host, port = serverAddressStr.split(":")
        cls._sendTerminateMessage((host, int(port)))

    @staticmethod
    def _sendTerminateMessage(serverAddress):
        sendSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sendSocket.connect(serverAddress)
        sendSocket.sendall("TERMINATE_SERVER\n".encode())
        sendSocket.shutdown(2)
        
    def shouldBeRecorded(self, *args):
        return not self.connectionFailed and traffic.Traffic.shouldBeRecorded(self, *args)

    def forwardToServer(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(self.destination)
        except OSError as e:
            sys.stderr.write("WARNING: Could not forward to server " + str(self.destination) + "\n")
            sys.stderr.write(str(e) + "\n")
            sys.stderr.write("-- " + self.text + "\n")
            sock.close()
            self.connectionFailed = True
            return []
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
        if method is None: # replay 
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
    fileContentsStr = "<File Contents for %s>"
    defaultIgnoreHeaders = [ "Content-Length", "Host", "User-Agent", "Connection", "Referer", "Date"] # provided automatically, or not usable when recorded
    defaultValues = {"Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "identity"}
    repeatCache = {}
    def __init__(self, text=None, responseFile=None, rcHandler=None, method="GET", path="/", headers={}, handler=None, **kw):
        self.handler = handler
        self.ignoreHeaders = self.defaultIgnoreHeaders + rcHandler.getList("ignore_http_headers", [ "general" ])
        if responseFile is not None: # record
            self.method = method
            self.path = path
            self.payload = text
            self.headers = headers
            textStr = self.decodePayload(self.payload)
            mainText = self.method + " " + self.path
            if self.payload is not None:
                text = mainText + " " + textStr
            else:
                text = mainText
            text += self.getHeaderText(self.headers.items())
        ClientSocketTraffic.__init__(self, text, responseFile, rcHandler, **kw)
        if responseFile is None: # replay
            parts = self.text.split(None, 2)
            self.headers = rcHandler.getSection("http_headers_replay")            
            self.method = parts[0]
            if len(parts) > 2:
                self.path = parts[1]
                textStr = self.extractHeaders(parts[2], self.headers)
                self.payload = encodingutils.encodeString(textStr) if textStr else None
                if self.payload:
                    self.tryReplaceFileContents()
            else:
                self.path = self.extractHeaders(parts[1], self.headers)
                textStr = ""
                self.payload = None
        self.checkRepeats = rcHandler.getboolean("check_repeated_calls", [ self.method ], True)
        
    def hasRepeatsInReplay(self):
        return self.checkRepeats
        
    def getHeaderText(self, headers):
        text = ""
        for header, value in headers:
            if header not in self.ignoreHeaders and self.defaultValues.get(header) != value and \
                not header.lower().startswith("sec-") and not header.lower().startswith("x-forwarded"):
                text += "\n" + self.headerStr + header + "=" + value
        return text
    
    def parseVariable(self, line, varName):
        key = varName + "="
        start = line.find(key)
        if start != -1:
            start += len(key)
            end = line.find(";", start)
            value = line[start:end] if end != -1 else line[start:]
            if value.startswith('"') and value.endswith('"'):
                return value[1:-1]
            else:
                return value
        
    def writeEditFile(self, filename, contents):
        editdir = FileEditTraffic.recordFileEditDir
        path = os.path.join(editdir, filename)
        if os.path.isfile(path):
            oldContents = open(path, "rb").read()
            if oldContents == contents:
                return filename
        
        newFn = FileEditTraffic.getFileEditName(filename)
        if not os.path.isdir(editdir):
            os.makedirs(editdir)
        newPath = os.path.join(editdir, newFn)
        with open(newPath, "wb") as f:
            f.write(contents)
        return newFn
        
    def getBoundary(self):
        contentType = self.headers.get("Content-Type", "")
        boundaryText = self.parseVariable(contentType, "boundary")
        if boundaryText:
            return b"--" + boundaryText.encode()
        
    def getFileEditContents(self, filename):
        if FileEditTraffic.replayFileEditDir:
            filepath = os.path.join(FileEditTraffic.replayFileEditDir, filename)
            if os.path.isfile(filepath):
                return open(filepath, "rb").read()
        
    def tryReplaceFileContents(self):
        boundary = self.getBoundary()
        if boundary:
            linesep = b"\r\n"
            prefix, postfix = self.fileContentsStr.encode().split(b"%s")
            newPayload = b""
            currPayload = self.payload
            start = currPayload.find(prefix)
            while start != -1:
                end = currPayload.find(linesep, start)
                filenameBytes = currPayload[start + len(prefix):end - len(postfix)]
                filename = encodingutils.decodeBytes(filenameBytes)
                contents = self.getFileEditContents(filename)
                if contents:
                    newPayload += currPayload[:start]
                    newPayload += contents
                    currPayload = currPayload[end:]
                else:
                    print("ERROR: Cannot find file named", repr(filename), "when replaying!", file=sys.stderr)
                    break
                start = currPayload.find(prefix)
            if newPayload:
                self.payload = newPayload + currPayload
         
    def getAttachmentFileName(self, headers):
        disposition = dict(headers).get("Content-Disposition", "")
        if disposition.startswith("attachment;"):
            return self.parseVariable(disposition, "filename")
                
    def decodeResponsePayload(self, payload, headers):
        attachmentFn = self.getAttachmentFileName(headers)
        if attachmentFn:
            fnUsed = self.writeEditFile(attachmentFn, payload)
            return self.fileContentsStr % fnUsed + self.getHeaderText(headers), payload
        else:
            body = encodingutils.decodeBytes(payload)
            body = self.applyAlterations(body)
            text = body + self.getHeaderText(headers)
            newPayload = encodingutils.encodeString(body)
            return text, newPayload
                
    def decodePayload(self, payload):
        if payload is None:
            return ""
        boundary = self.getBoundary()
        if boundary is None:
            return encodingutils.decodeBytes(payload)
        
        linesep = b"\r\n"
        lines = []
        currFileName, currFileWritten, fileContents = None, False, b""
        for line in payload.split(linesep):
            hitBoundary = currFileName and line.startswith(boundary)
            if line and currFileName and not hitBoundary and not line.startswith(b"Content-"):
                if currFileWritten:
                    fileContents += linesep
                fileContents += line
                currFileWritten = True
            else:
                textLine = encodingutils.decodeBytes(line)
                if hitBoundary:
                    fileNameUsed = self.writeEditFile(currFileName, fileContents)
                    lines.append(self.fileContentsStr % fileNameUsed)
                    currFileName, currFileWritten, fileContents = None, False, b""
                elif textLine.startswith("Content-Disposition: form-data;"):
                    currFileName = self.parseVariable(textLine, "filename")
            
                lines.append(textLine)
        return "\n".join(lines)

    def extractHeaders(self, textStr, headers):
        if HTTPClientTraffic.headerStr in textStr:
            parts = textStr.split(HTTPClientTraffic.headerStr)
            for headerStr in parts[1:]:
                header, value = headerStr.strip().split("=", 1)
                headers[header] = value
            return self.stripNewline(parts[0])
        else:
            return self.stripNewline(textStr)
        
    def stripNewline(self, text):
        return text[:-1] if text.endswith("\n") else text
    
    def shouldBeRecorded(self, responses=None):
        if self.checkRepeats or responses is None:
            return True
        else:
            cached = self.repeatCache.get(self.text, [])
            responseText = "".join((resp.text for resp in responses))
            if responseText in cached:
                return False
            
            self.repeatCache.setdefault(self.text, []).append(responseText)
            return True
        
    def forwardToServer(self):
        try:
            request = Request(self.destination + self.path, data=self.payload, headers=self.headers, method=self.method)
            response = urlopen(request)
            payload = response.read()
            headers = response.getheaders()
            text, body = self.decodeResponsePayload(payload, headers)
            return [ HTTPServerTraffic(response.status, text, body, headers, self.responseFile, handler=self.handler) ]
        except HTTPError as e:
            payload = e.read()
            headers = e.getheaders()
            text, body = self.decodeResponsePayload(payload, headers)
            return [ HTTPServerTraffic(e.code, text, body, headers, self.responseFile, handler=self.handler) ]
        except URLError as e:
            sys.stderr.write("Failed to forward http traffic to server " + self.destination + " : " + str(e) + "\n")
            return []
        
    def makeResponseTraffic(self, rawText, responseClass, rcHandler):
        if responseClass is HTTPServerTraffic:
            status, text = rawText.split(" ", 1)
            text = self.applyAlterations(text)
            headerDict = {}
            bodyText = self.extractHeaders(text, headerDict)
            body = encodingutils.encodeString(bodyText)
            attachmentFn = self.getAttachmentFileName(headerDict)
            if attachmentFn:
                replaceStr = (self.fileContentsStr % attachmentFn).encode()
                body = body.replace(replaceStr, self.getFileEditContents(attachmentFn))
            headers = list(headerDict.items())
            return responseClass(int(status), text, body, headers, self.responseFile, self.handler)
        else:
            return super(HTTPClientTraffic, self).makeResponseTraffic(rawText, responseClass, rcHandler)


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
    def __init__(self, status, text, body, headers, responseFile, handler):
        self.body = body
        ServerTraffic.__init__(self, str(status) + " " + text, responseFile)
        self.status = status
        self.headers = headers
        self.handler = handler
    
    def forwardToDestination(self):
        if self.handler:
            # don't include server and date, chances are we already have them
            self.handler.send_response_only(self.status)
            for hdr, value in self.headers:
                # Might need to handle chunked transfer, for now, just ignore it and return it as one
                if hdr.lower() != "transfer-encoding" or value.lower() != "chunked":
                    self.handler.send_header(hdr, value)
            self.handler.send_header('Access-Control-Allow-Origin', '*')
            self.handler.send_header('Access-Control-Expose-Headers', '*')
            self.handler.end_headers()
        self.write(self.body)
        # Don't close the file, the HTTP server mechanism does that for us
        return []
    
    def write(self, message):
        if self.responseFile:
            try:
                self.responseFile.write(message)
                self.responseFile.flush()
                self.handler.request.shutdown(socket.SHUT_WR)
                self.handler.request.close()
            except ConnectionError:
                # The service that sent the original request is no longer listening for answers
                # This is not necessarily a problem - we don't want to raise exceptions here
                pass

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

