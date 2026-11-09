""" Traffic classes, request handler and server for CaptureMock's HTTP protocol.
Kept separate so the http.* modules are only imported when server_protocol is http. """

import os, sys, socket, threading, re, json
from locale import getpreferredencoding
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from urllib.parse import urlparse, urlunparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from capturemock import encodingutils
from capturemock.fileedittraffic import FileEditTraffic
from capturemock.clientservertraffic import ClientSocketTraffic, ServerTraffic, ServerStateTraffic


class HTTPClientTraffic(ClientSocketTraffic):
    headerStr = "--HEA:"
    fileContentsStr = "<File Contents for %s>"
    # Copied from https://developer.mozilla.org/en-US/docs/Glossary/Forbidden_header_name
    defaultIgnoreHeaders = [ "accept-charset", "accept-encoding", "access-control-request-headers", "access-control-request-method",
                             "connection", "content-length", "cookie", "date", "dnt", "expect", "host", "keep-alive", "origin",
                             "permissions-policy", "referer", "te", "trailer", "upgrade", "user-agent", "via" ]
    defaultValues = {"content-type": "application/x-www-form-urlencoded"}
    repeatCache = {}
    def __init__(self, text=None, responseFile=None, rcHandler=None, method="GET", path="/", headers={}, handler=None, **kw):
        self.handler = handler
        ignoreFromConfig = [ h.lower() for h in rcHandler.getList("ignore_http_headers", [ "general" ]) ]
        self.ignoreHeaders = self.defaultIgnoreHeaders + ignoreFromConfig
        self.headers = rcHandler.getSection("default_http_headers")
        if responseFile is not None: # record
            self.method = method
            self.path = path
            self.payload = text
            self.headers.update(headers)    
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
        self.allCanModify = rcHandler.getboolean("assume_all_can_modify", [ "general" ], False)
        
    def hasRepeatsInReplay(self):
        return self.checkRepeats
    
    def canModifyServer(self):
        return self.allCanModify or not self.text.startswith("GET")
        
    def getHeaderText(self, headers):
        text = ""
        for header, value in sorted(headers, key=lambda item: item[0].lower()):
            low_header = header.lower()
            if low_header not in self.ignoreHeaders and self.defaultValues.get(low_header) != value.lower() and \
                not low_header.startswith("sec-") and not low_header.startswith("proxy-") and \
                not low_header.startswith("x-forwarded"):
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
        headerDict = dict(headers)
        disposition = headerDict.get("Content-Disposition", "")
        if disposition.startswith("attachment;"):
            return self.parseVariable(disposition, "filename")
        
        if headerDict.get("Content-Type") == "application/zip" and self.path.endswith(".zip"):
            return self.path.rsplit("/", 1)[-1]
                
    def decodeResponsePayload(self, payload, headers):
        attachmentFn = self.getAttachmentFileName(headers)
        if attachmentFn:
            fnUsed = self.writeEditFile(attachmentFn, payload)
            return self.fileContentsStr % fnUsed + self.getHeaderText(headers), payload
        else:
            body = encodingutils.decodeBytes(payload)
            headerText = self.getHeaderText(headers)
            if len(self.alterations) == 0:
                return body + headerText, payload
            
            newBody = self.applyAlterations(body)
            if newBody == body:
                return body + headerText, payload
                
            newPayload = encodingutils.encodeString(newBody)
            return newBody + headerText, newPayload
                
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
            if (line or currFileWritten) and currFileName and not hitBoundary and not line.startswith(b"Content-"):
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
        except OSError as e:
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
                if hdr.lower() != 'access-control-allow-origin' and (hdr.lower() != "transfer-encoding" or value.lower() != "chunked"):
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
            except OSError:
                # The service that sent the original request is no longer listening for answers
                # This is not necessarily a problem - we don't want to raise exceptions here
                pass


class HTTPServerStateTraffic(ServerStateTraffic):
    def __init__(self, dest, rcHandler):
        ServerStateTraffic.__init__(self, "POST /capturemock/setServerLocation <address>", dest, None, rcHandler)


class HTTPTrafficHandler(BaseHTTPRequestHandler):
    dispatcher = None
    redirects = {}
    requestCount = 0
    redirectLock = threading.Lock()
    def read_data(self):
        # Lifted from https://stackoverflow.com/questions/60895110/how-to-handle-chunked-encoding-in-python-basehttprequesthandler
        # Weird that there is no builtin way to handle this
        if 'Content-Length' in self.headers:
            content_length = int(self.headers.get('Content-Length')) # <--- Gets the size of data
            return self.rfile.read(content_length)
        elif "chunked" in self.headers.get("Transfer-Encoding", ""):
            data = b""
            while True:
                line = self.rfile.readline().strip()
                chunk_length = int(line, 16)
                if chunk_length != 0:
                    data += self.rfile.read(chunk_length)

                # Each chunk is followed by an additional empty newline
                # that we have to consume.
                self.rfile.readline()

                # Finally, a chunk size of 0 is an end indication
                if chunk_length == 0:
                    return data
    
    def log_message(self, format, *args):
        self.dispatcher.diag.debug(format % args)
        
    def get_local_path(self):
        urldata = urlparse(self.path)
        urldata = urldata._replace(scheme="", netloc="")
        return urlunparse(urldata)
    
    def find_redirect_target_id(self):
        cookie_header = self.headers.get('Cookie')
        if cookie_header:
            cookie = SimpleCookie(cookie_header)
            morsel = cookie.get("capturemock_proxy_target")
            if morsel:
                return morsel.value
        
    def find_redirect_target(self, targetData):
        server = self.find_redirect_server(targetData)
        if server is not None:
            return server + self.find_redirect_path(targetData)

    def find_redirect_path(self, targetData):
        replace = targetData.get("replace")
        if not replace:
            return self.path
        
        path = self.path
        for regexp, repl in replace.items():
            path = re.sub(regexp, repl.replace("$", "\\"), path)
        return path
            
    def find_redirect_server(self, targetData):
        matcher = targetData.get("matcher")
        if len(matcher) == 1:
            return list(matcher.values())[0]

        targetId = self.find_redirect_target_id()
        if targetId:
            return matcher.get(targetId)
        
    def get_redirect_target_data(self):
        for givenPath, targetData in self.redirects.items():
            if self.path.startswith(givenPath):
                return targetData
    
    def try_redirect(self):
        targetData = self.get_redirect_target_data()
        if targetData:
            server = self.find_redirect_server(targetData)
            if server is not None:
                redirect_path = self.find_redirect_path(targetData)
                target = server + redirect_path
                # Authorization headers get remove by default so cannot just redirect. 
                # Need to send them as a new default
                auth = self.headers.get("Authorization")
                if auth:
                    fn = redirect_path.split("?")[0].replace("/", "_") + ".rc"
                    if not os.path.isfile(fn):
                        url = server + "/capturemock/addAlterations"
                        with open(fn, "w") as f:
                            f.write("[default_http_headers]\n")
                            f.write("Authorization = " + auth + "\n")
                        urlopen(url, data=os.path.abspath(fn).encode()).read()
                        self.log_message("Forwarded auth to target %s, written file %s", target, fn)

                self.send_response(307)
                self.log_message("Redirecting! %s -> %s", self.path, target)
                self.send_header('Location', target)
            else:
                # with redirect by auth, assume we are redirect-only, return 404 without match
                print("FAILED to redirect!", file=sys.stderr)
                print(self.path, file=sys.stderr)
                print("target_id=", self.find_redirect_target_id(), file=sys.stderr)
                self.send_response(404)
            self.end_headers()
            return True
        return False

    def do_GET(self):
        if self.path == "/capturemock/shutdownServer":
            self.send_response(200)
            self.end_headers()
            self.dispatcher.server.setShutdownFlag()
        else:
            if self.try_redirect():
                return
            
            HTTPTrafficHandler.requestCount += 1
            traffic = HTTPClientTraffic(responseFile=self.wfile, method="GET", path=self.get_local_path(), headers=self.headers, 
                                                            rcHandler=self.dispatcher.rcHandler, handler=self)
            self.dispatcher.process(traffic, self.requestCount)

    def do_POST(self):
        rawbytes = self.read_data()        
        if self.path.startswith("/capturemock/sendPathRedirect/"):
            redirectKey = "/".join(self.path.split("/")[3:])
            target = rawbytes.decode(getpreferredencoding())
            mapping = json.loads(target)
            self.log_message("got path redirect %s -> %s", redirectKey, mapping)
            with self.redirectLock:
                if redirectKey in self.redirects and "matcher" in mapping:
                    self.redirects[redirectKey]["matcher"].update(mapping.get("matcher"))
                else:
                    self.redirects[redirectKey] = mapping
            self.send_response(200)
            self.end_headers()
            return
        
        if self.try_redirect():
            return
        
        if self.path == "/capturemock/addAlterations":
            rcFile = rawbytes.decode(getpreferredencoding())
            self.dispatcher.rcHandler.addFile(rcFile)
            self.send_response(200)
            self.end_headers()
            return
        
        HTTPTrafficHandler.requestCount += 1
        if self.path == "/capturemock/setServerLocation":
            text = rawbytes.decode(getpreferredencoding())
            traffic = HTTPServerStateTraffic(text, rcHandler=self.dispatcher.rcHandler)
            self.send_response(200)
            self.end_headers()
        else:
            traffic = HTTPClientTraffic(rawbytes, self.wfile, method="POST", path=self.get_local_path(), headers=self.headers, 
                                                            rcHandler=self.dispatcher.rcHandler, handler=self)
        self.dispatcher.process(traffic, self.requestCount)
        
    def do_method_with_payload(self, method):
        # Must always read the request, even if redirecting
        rawbytes = self.read_data()
        if self.try_redirect():
            return
        HTTPTrafficHandler.requestCount += 1
        traffic = HTTPClientTraffic(rawbytes, self.wfile, method=method, path=self.get_local_path(), headers=self.headers, 
                                                        rcHandler=self.dispatcher.rcHandler, handler=self)
        self.dispatcher.process(traffic, self.requestCount)

    def do_PATCH(self):
        self.do_method_with_payload("PATCH")

    def do_PUT(self):
        self.do_method_with_payload("PUT")
        
    def do_DELETE(self):
        if self.try_redirect():
            return
        HTTPTrafficHandler.requestCount += 1
        traffic = HTTPClientTraffic(responseFile=self.wfile, method="DELETE", path=self.get_local_path(), headers=self.headers, 
                                                        rcHandler=self.dispatcher.rcHandler, handler=self)
        self.dispatcher.process(traffic, self.requestCount)
    
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header("Access-Control-Allow-Headers", '*')
        self.send_header('Access-Control-Allow-Methods', '*')
        self.end_headers()
        
        
        

class HTTPTrafficServer(HTTPServer):
    @classmethod
    def createServer(cls, address, port, dispatcher):
        HTTPTrafficHandler.dispatcher = dispatcher
        return cls((address, port))
    
    def __init__(self, address):
        # Default value of 5 isn't very much...
        # There doesn't seem to be any disadvantage of allowing a longer queue, so we will increase it by a lot...
        self.request_queue_size = 500
        HTTPServer.__init__(self, address, HTTPTrafficHandler)
    
    def run(self):
        self.serve_forever()

    def getAddress(self):
        host, port = self.socket.getsockname()
        # hardcode http? Seems to be what you get...
        return "http://" + host + ":" + str(port)

    def setShutdownFlag(self):
        # Not supposed to know about this variable, implementation detail of SocketServer
        # But seems like the only way to shutdown the server from within a request
        # Otherwise we have to start using ForkingMixin, ThreadingMixin etc which seems like overkill just to access a variable
        self._BaseServer__shutdown_request = True

    @staticmethod
    def getTrafficClasses(incoming):
        if incoming:
            classes = [ HTTPServerStateTraffic, HTTPClientTraffic ]
        else:
            classes = [ HTTPServerTraffic, HTTPClientTraffic ]
            try:
                from capturemock.amqptraffic import AMQPResponseTraffic
                classes.append(AMQPResponseTraffic)
            except ImportError:
                # If we haven't got the files installed, reasonable to assume there won't be any AMQP traffic
                pass
        return classes
