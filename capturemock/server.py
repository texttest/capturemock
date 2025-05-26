import os, stat, sys, socket, threading, time, subprocess
from copy import copy

from capturemock import config, id_mapping
from capturemock.replayinfo import ReplayInfo
from capturemock import recordfilehandler, cmdlineutils
from capturemock import commandlinetraffic, fileedittraffic, clientservertraffic, customtraffic
from locale import getpreferredencoding
from collections import OrderedDict, namedtuple

from urllib.request import urlopen
from urllib.parse import urlparse, urlunparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import TCPServer, StreamRequestHandler, UDPServer,\
    BaseRequestHandler
from xmlrpc.client import Fault, ServerProxy
from xmlrpc.server import SimpleXMLRPCServer
import re
import json
from http.cookies import SimpleCookie

def getPython():
    if os.name == "nt":
        basename = os.path.basename(sys.executable).lower()
        if "python" not in basename:
            # 'Native launcher' on Windows, such as PyUseCase
            # Look on sys.path instead. Tip from Brian Curtin on comp.lang.python
            for path in sys.path:
                full = os.path.join(path, "python.exe")
                if os.path.exists(full):
                    return full
            return "python.exe" # hope it's on the PATH, and that it's the right one. Native launcher is never right!
    return sys.executable

def getServer():
    if getattr(sys, 'frozen', False): # from exe file, likely TextTest
        # bundled with TextTest?
        exeFile = os.path.join(os.path.dirname(sys.executable), "capturemock_server.exe")
        if os.path.isfile(exeFile):
            return [ exeFile ]
    return [getPython(), __file__]

def startServer(rcFiles,
                mode,
                replayFile,
                replayEditDir,
                recordFile,
                recordEditDir,
                sutDirectory,
                environment,
                stderrFn):
    cmdArgs = getServer() + [ "-m", str(mode) ]
    if rcFiles:
        cmdArgs += [ "--rcfiles", ",".join(rcFiles) ]
    if recordFile:
        cmdArgs += [ "-r", recordFile ]
    if recordEditDir:
        cmdArgs += [ "-F", recordEditDir ]

    if replayFile and mode != config.RECORD:
        cmdArgs += [ "-p", replayFile ]
        if replayEditDir:
            cmdArgs += [ "-f", replayEditDir ]

    stderr = open(stderrFn, "w") if stderrFn else subprocess.PIPE
    return subprocess.Popen(cmdArgs,
                            env=environment.copy(),
                            universal_newlines=True,
                            cwd=sutDirectory,
                            stdout=subprocess.PIPE,
                            stderr=stderr)

def stopServer(servAddr, protocol):
    if protocol == "http":
        try:
            urlopen(servAddr + "/capturemock/shutdownServer")
        except socket.error:
            print("Could not send terminate message to CaptureMock server at " + servAddr + \
                  ", seemed not to be running anyway.", file=sys.stderr)
    elif protocol == "ftp":
        from .ftptraffic import FtpTrafficServer
        FtpTrafficServer.sendTerminateMessage(servAddr)
    elif protocol == "xmlrpc":
        s = ServerProxy(servAddr)
        s.shutdownCaptureMockServer()
    elif protocol == "amqp":
        from .amqptraffic import AMQPTrafficServer
        if servAddr and "/" in servAddr:
            AMQPTrafficServer.sendTerminateMessage(servAddr)
        else:
            print("AMQP CaptureMock server had invalid address at " + servAddr + \
                  ", probably did not start.", file=sys.stderr)
    else:
        try:
            protocol = socket.SOCK_DGRAM if protocol == "classic_udp" else socket.SOCK_STREAM
            ClassicTrafficServer.sendTerminateMessage(servAddr, protocol)
        except socket.error: # pragma: no cover - should be unreachable, just for robustness
            print("Could not send terminate message to CaptureMock server at " + servAddr + \
                  ", seemed not to be running anyway.", file=sys.stderr)


class ClassicTrafficServer:    
    def __init__(self):
        self.terminate = False
        self.requestCount = 0
        # Default value of 5 isn't very much...
        # There doesn't seem to be any disadvantage of allowing a longer queue, so we will increase it by a lot...
        self.request_queue_size = 500

    def run(self):
        while not self.terminate:
            self.handle_request()
        # Join all remaining request threads so they don't
        # execute after Python interpreter has started to shut itself down.
        for t in threading.enumerate():
            if t.name == "request":
                t.join()

    def process_request_thread(self, request, client_address, requestCount):
        # Copied from ThreadingMixin, more or less
        # We store the order things appear in so we know what order they should go in the file
        try:
            self.RequestHandlerClass(requestCount, request, client_address, self)
            self.close_request(request)
        except: # pragma : no cover - interpreter code in theory...
            self.handle_error(request, client_address)
            self.close_request(request)

    @classmethod
    def sendTerminateMessage(cls, *args):
        clientservertraffic.ClientSocketTraffic.sendTerminateMessage(*args)
        
    @staticmethod
    def getTrafficClasses(incoming):
        if incoming:
            return [ clientservertraffic.ClassicServerStateTraffic, clientservertraffic.ClientSocketTraffic ]
        else:
            return [ clientservertraffic.ServerTraffic, clientservertraffic.ClientSocketTraffic ]

    def shutdown(self):
        self.terminate = True

    def getAddress(self):
        host, port = self.socket.getsockname()
        return host + ":" + str(port)
    
class ClassicTcpTrafficServer(ClassicTrafficServer, TCPServer):
    @classmethod
    def createServer(cls, address, dispatcher):
        ClassicTcpTrafficRequestHandler.dispatcher = dispatcher
        return cls((address, 0), ClassicTcpTrafficRequestHandler, dispatcher.useThreads)

    def __init__(self, addrinfo, handlerClass, useThreads):
        ClassicTrafficServer.__init__(self)
        TCPServer.__init__(self, addrinfo, handlerClass)
        self.useThreads = useThreads

    def process_request(self, request, client_address):
        self.requestCount += 1
        if self.useThreads:
            """Start a new thread to process the request."""
            t = threading.Thread(target = self.process_request_thread, name="request",
                                 args = (request, client_address, self.requestCount))
            t.start()
        else:
            self.process_request_thread(request, client_address, self.requestCount)
            
    def shutdown(self):
        if self.useThreads:
            # Setting terminate will only work if we do it in the main thread:
            # otherwise the main thread might be in a blocking call at the time
            # So we reset the thread flag and send a new message
            self.useThreads = False
            clientservertraffic.ClientSocketTraffic._sendTerminateMessage(self.socket.getsockname())
        else:
            self.terminate = True
         
    
class ClassicUdpTrafficServer(ClassicTrafficServer, UDPServer):
    @classmethod
    def createServer(cls, address, dispatcher):
        ClassicUdpTrafficRequestHandler.dispatcher = dispatcher
        return cls((address, 0), ClassicUdpTrafficRequestHandler, dispatcher.useThreads)

    def __init__(self, addrinfo, handlerClass, *args):
        ClassicTrafficServer.__init__(self)
        UDPServer.__init__(self, addrinfo, handlerClass)
        clientservertraffic.ClientSocketTraffic.socketType = socket.SOCK_DGRAM
        
    def process_request(self, request, client_address):
        self.requestCount += 1
        self.process_request_thread(request, client_address, self.requestCount)
    

class ClassicTcpTrafficRequestHandler(StreamRequestHandler):
    dispatcher = None
    def __init__(self, requestNumber, *args):
        self.requestNumber = requestNumber
        StreamRequestHandler.__init__(self, *args)

    def handle(self):
        text = self.rfile.read().decode()
        self.handleText(text)
        
    def handleText(self, text):
        self.dispatcher.diag.debug("Received incoming TCP request...\n" + text)
        try:
            self.dispatcher.processText(text, self.wfile, self.requestNumber)
        except config.CaptureMockReplayError as e:
            self.wfile.write(("CAPTUREMOCK MISMATCH: " + str(e)).encode())
    
class UdpSocketFile:
    def __init__(self, sock, address):
        self.socket = sock
        self.address = address
        
    def write(self, data):
        self.socket.sendto(data, self.address)
        
    def close(self):
        self.socket.close()
    
class ClassicUdpTrafficRequestHandler(BaseRequestHandler):
    dispatcher = None
    def __init__(self, requestNumber, *args):
        self.requestNumber = requestNumber
        BaseRequestHandler.__init__(self, *args)

    def handle(self):
        text = self.request[0].strip().decode()
        self.handleText(text)
        
    def handleText(self, text):
        self.dispatcher.diag.debug("Received incoming UDP request...\n" + text)
        wfile = UdpSocketFile(self.request[1], self.client_address)
        try:
            self.dispatcher.processText(text, wfile, self.requestNumber)
        except config.CaptureMockReplayError as e:
            wfile.write(("CAPTUREMOCK MISMATCH: " + str(e)).encode())


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
            traffic = clientservertraffic.HTTPClientTraffic(responseFile=self.wfile, method="GET", path=self.get_local_path(), headers=self.headers, 
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
            traffic = clientservertraffic.HTTPServerStateTraffic(text, rcHandler=self.dispatcher.rcHandler)
            self.send_response(200)
            self.end_headers()
        else:
            traffic = clientservertraffic.HTTPClientTraffic(rawbytes, self.wfile, method="POST", path=self.get_local_path(), headers=self.headers, 
                                                            rcHandler=self.dispatcher.rcHandler, handler=self)
        self.dispatcher.process(traffic, self.requestCount)
        
    def do_method_with_payload(self, method):
        # Must always read the request, even if redirecting
        rawbytes = self.read_data()
        if self.try_redirect():
            return
        HTTPTrafficHandler.requestCount += 1
        traffic = clientservertraffic.HTTPClientTraffic(rawbytes, self.wfile, method=method, path=self.get_local_path(), headers=self.headers, 
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
        traffic = clientservertraffic.HTTPClientTraffic(responseFile=self.wfile, method="DELETE", path=self.get_local_path(), headers=self.headers, 
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
    def createServer(cls, address, dispatcher):
        HTTPTrafficHandler.dispatcher = dispatcher
        return cls((address, 0))
    
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
            classes = [ clientservertraffic.HTTPServerStateTraffic, clientservertraffic.HTTPClientTraffic ]
        else:
            classes = [ clientservertraffic.HTTPServerTraffic, clientservertraffic.HTTPClientTraffic ]
            try:
                from capturemock.amqptraffic import AMQPResponseTraffic
                classes.append(AMQPResponseTraffic)
            except ImportError:
                # If we haven't got the files installed, reasonable to assume there won't be any AMQP traffic
                pass
        return classes


class XmlRpcTrafficServer(SimpleXMLRPCServer):
    @classmethod
    def createServer(cls, address, dispatcher):
        server = cls((address, 0), logRequests=False, use_builtin_types=True)
        server.register_instance(XmlRpcDispatchInstance(dispatcher))
        return server

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
            return [ clientservertraffic.XmlRpcServerStateTraffic, clientservertraffic.XmlRpcClientTraffic ]
        else:
            return [ clientservertraffic.XmlRpcServerTraffic, clientservertraffic.XmlRpcClientTraffic ]


class XmlRpcDispatchInstance:
    requestCount = 0
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher

    def convertBytes(self, param):
        return str(param, getpreferredencoding()) if isinstance(param, bytes) else param

    def _dispatch(self, method, binparams):
        params = tuple([ self.convertBytes(param) for param in binparams ])
        try:
            self.dispatcher.diag.info("Received XMLRPC traffic " + method + repr(params))
            XmlRpcDispatchInstance.requestCount += 1
            if method == "shutdownCaptureMockServer":
                self.dispatcher.server.setShutdownFlag()
                return ""
            elif method == "setServerLocation":
                traffic = clientservertraffic.XmlRpcServerStateTraffic(params[0], rcHandler=self.dispatcher.rcHandler)
            else:
                traffic = clientservertraffic.XmlRpcClientTraffic(method=method, params=params, rcHandler=self.dispatcher.rcHandler)
            responses = self.dispatcher.process(traffic, self.requestCount)
            return responses[0].getXmlRpcResponse() if responses else ""
        except Fault:
            raise
        except:
            sys.stderr.write("Exception thrown while handling XMLRPC input :\n")
            type, value, traceback = sys.exc_info()
            from traceback import format_exception
            exceptionString = "".join(format_exception(type, value, traceback))
            sys.stderr.write(exceptionString)
            return ""
        
class ServerDispatcherBase:
    def __init__(self, options):
        rcFiles = options.rcfiles.split(",") if options.rcfiles else []
        self.rcHandler = config.RcFileHandler(rcFiles)
        self.diag = self.rcHandler.setUpLogging("Server")
        self.filesToIgnore = self.rcHandler.getList("ignore_edits", [ "command line" ])
        self.useThreads = self.rcHandler.getboolean("server_multithreaded", [ "general" ], True)
        self.replayInfo = ReplayInfo(options.mode, options.replay, self.rcHandler)
        self.recordFileHandler = RecordFileHandler(options.record)
        self.topLevelForEdit = [] # contains only paths explicitly given. Always present.
        self.fileEditData = OrderedDict() # contains all paths, including subpaths of the above. Empty when replaying.
        self.hasAsynchronousEdits = False
        self.serverClass = self.getServerClass()
        
    def getServerClass(self):
        protocol = self.rcHandler.get("server_protocol", [ "general" ], "classic")
        if protocol in [ "classic", "classic_tcp" ]:
            return ClassicTcpTrafficServer
        elif protocol == "classic_udp":
            return ClassicUdpTrafficServer
        elif protocol == "tcp_header":
            from capturemock.binarytcptraffic import TcpHeaderTrafficServer
            return TcpHeaderTrafficServer
        elif protocol == "ftp":
            from capturemock.ftptraffic import FtpTrafficServer
            return FtpTrafficServer
        elif protocol == "http":
            return HTTPTrafficServer
        elif protocol == "xmlrpc":
            return XmlRpcTrafficServer
        elif protocol == "amqp":
            from capturemock.amqptraffic import AMQPTrafficServer
            return AMQPTrafficServer
        
    def shutdown(self):
        pass
        
    def findFilesAndLinks(self, path):
        if not os.path.exists(path):
            return []
        if os.path.isfile(path) or os.path.islink(path):
            return [ path ]

        paths = []
        for srcroot, srcdirs, srcfiles in os.walk(path):
            for fileToIgnore in self.filesToIgnore:
                if fileToIgnore in srcdirs:
                    srcdirs.remove(fileToIgnore)
                if fileToIgnore in srcfiles:
                    srcfiles.remove(fileToIgnore)
            for srcfile in srcfiles:
                paths.append(os.path.join(srcroot, srcfile))

            for srcdir in srcdirs:
                fullSrcPath = os.path.join(srcroot, srcdir)
                if os.path.islink(fullSrcPath):
                    paths.append(fullSrcPath)
        return paths

    def getLatestModification(self, path):
        if os.path.exists(path):
            statObj = os.stat(path)
            return statObj[stat.ST_MTIME], statObj[stat.ST_SIZE]
        else:
            return None, 0

    def addPossibleFileEdits(self, traffic):
        allEdits = traffic.findPossibleFileEdits()
        topLevelForEdit = copy(self.topLevelForEdit)
        fileEditData = copy(self.fileEditData)
        for file in allEdits:
            if file in topLevelForEdit:
                topLevelForEdit.remove(file)
            # Always move them to the beginning, most recent edits are most relevant
            topLevelForEdit.insert(0, file)

            # edit times aren't interesting when doing pure replay
            if not self.replayInfo.isActiveForAll():
                for subPath in self.findFilesAndLinks(file):
                    modTime, modSize = self.getLatestModification(subPath)
                    fileEditData[subPath] = modTime, modSize
                    self.diag.debug("Adding possible sub-path edit for " + subPath + " with mod time " +
                                   time.strftime("%d%b%H:%M:%S", time.localtime(modTime)) + " and size " + str(modSize))
        return topLevelForEdit, fileEditData

    def processText(self, text, wfile, reqNo):
        self.diag.debug("Request text : " + text)
        if text.startswith("TERMINATE_SERVER"):
            self.shutdown()
            return []
        else:
            traffic = self.parseTraffic(text, wfile)
            responses = self.process(traffic, reqNo)
            self.diag.debug("Finished processing incoming request")
            return responses
        
    def parseTraffic(self, text, wfile):
        for cls in self.getTrafficClasses(incoming=True):
            prefix = cls.socketId + ":" if cls.socketId else ""
            if text.startswith(prefix):
                value = text[len(prefix):]
                return cls(value, wfile, self.rcHandler)

    def process(self, traffic, reqNo):
        if not self.replayInfo.isActiveFor(traffic):
            # If we're recording, check for file changes before we do
            # Must do this before as they may be a side effect of whatever it is we're processing
            for fileTraffic in self.getLatestFileEdits(self.topLevelForEdit, self.fileEditData):
                self._process(fileTraffic, reqNo)

        responses = self._process(traffic, reqNo)
        self.recordFileHandler.requestComplete(reqNo)
        return responses

    def _process(self, traffic, reqNo):
        self.diag.debug("Processing traffic " + traffic.__class__.__name__ + " with text " + repr(traffic.text))
        topLevelForEdit, fileEditData = self.addPossibleFileEdits(traffic)
        responses = self.getResponses(traffic, topLevelForEdit, fileEditData)
        doRecord = traffic.shouldBeRecorded(responses)
        if doRecord:
            self.diag.debug("Calling traffic record for " + str(reqNo) + " recording request " + str(self.recordFileHandler.recordingRequest))
            traffic.record(self.recordFileHandler, reqNo)
        for response in responses:
            self.diag.debug("Response of type " + response.__class__.__name__ + " with text " + repr(response.text))
            if doRecord:
                response.record(self.recordFileHandler, reqNo)
            for chainResponse in response.forwardToDestination():
                self._process(chainResponse, reqNo)
            self.diag.debug("Completed response of type " + response.__class__.__name__)
        self.hasAsynchronousEdits |= traffic.makesAsynchronousEdits()
        if self.hasAsynchronousEdits:
            # Unless we've marked it as asynchronous we start again for the next traffic.
            for f in topLevelForEdit:
                if f not in self.topLevelForEdit:
                    self.topLevelForEdit.append(f)
            self.fileEditData.update(fileEditData)
        return responses

    def getTrafficClasses(self, incoming):
        classes = []
        # clientservertraffic must be last, it's the fallback option
        for mod in [ commandlinetraffic, fileedittraffic, customtraffic, self.serverClass ]:
            classes += mod.getTrafficClasses(incoming)
        return classes

    def getResponses(self, traffic, topLevelForEdit, fileEditData):
        if self.replayInfo.isActiveFor(traffic):
            self.diag.debug("Replay active for current command")
            replayedResponses = []
            filesMatched = []
            responseClasses = self.getTrafficClasses(incoming=False)
            self.diag.debug("Trying with possible classes " + repr(responseClasses))
            for responseClass, text in self.replayInfo.readReplayResponses(traffic, responseClasses):
                responseTraffic = self.makeResponseTraffic(traffic, responseClass, text, filesMatched, topLevelForEdit)
                if responseTraffic:
                    replayedResponses.append(responseTraffic)
            return traffic.filterReplay(replayedResponses)
        else:
            trafficResponses = traffic.forwardToDestination()
            if topLevelForEdit: # Only if the traffic itself can produce file edits do we check here
                return self.getLatestFileEdits(topLevelForEdit, fileEditData) + trafficResponses
            else:
                return trafficResponses

    def getFileBeingEdited(self, givenName, fileType, filesMatched, topLevelForEdit):
        # drop the suffix which is internal to TextTest
        fileName = givenName.split(".edit_")[0]
        bestMatch, bestScore = None, -1
        for editedFile in topLevelForEdit:
            if (fileType == "directory" and os.path.isfile(editedFile)) or \
               (fileType == "file" and os.path.isdir(editedFile)):
                continue

            editedName = os.path.basename(editedFile)
            if editedName == fileName and editedFile not in filesMatched:
                filesMatched.append(editedFile)
                bestMatch = editedFile
                break
            else:
                matchScore = self.getFileMatchScore(fileName, editedName)
                self.diag.debug("Trying " + editedName + " vs " + fileName + " got score " + str(matchScore))
                if matchScore > bestScore:
                    bestMatch, bestScore = editedFile, matchScore

        if bestMatch and bestMatch.startswith("/cygdrive"): # on Windows, paths may be referred to by cygwin path, handle this
            bestMatch = bestMatch[10] + ":" + bestMatch[11:]
        return bestMatch

    def getFileMatchScore(self, givenName, actualName):
        if actualName.find(".edit_") != -1:
            return -1

        return self._getFileMatchScore(givenName, actualName, lambda x: x) + \
               self._getFileMatchScore(givenName, actualName, lambda x: -1 -x)

    def _getFileMatchScore(self, givenName, actualName, indexFunction):
        score = 0
        while len(givenName) > score and len(actualName) > score and givenName[indexFunction(score)] == actualName[indexFunction(score)]:
            score += 1
        return score

    def makeResponseTraffic(self, traffic, responseClass, text, filesMatched, topLevelForEdit):
        if responseClass is fileedittraffic.FileEditTraffic:
            fileName = text.strip()
            self.diag.debug("Looking up file edit data for " + repr(fileName)) 
            storedFile, fileType = fileedittraffic.FileEditTraffic.getFileWithType(fileName)
            if storedFile:
                self.diag.debug("Found file " + repr(storedFile) + " of type " + fileType)
                editedFile = self.getFileBeingEdited(fileName, fileType, filesMatched, topLevelForEdit)
                if editedFile:
                    self.diag.debug("Will use it to edit file at " + str(editedFile))
                    changedPaths = self.findFilesAndLinks(storedFile)
                    return fileedittraffic.FileEditTraffic(fileName, editedFile, storedFile, changedPaths, reproduce=True)
        else:
            return traffic.makeResponseTraffic(text, responseClass, self.rcHandler)

    def findRemovedPath(self, removedPath):
        # We know this path is removed, what about its parents?
        # We want to store the most concise removal.
        parent = os.path.dirname(removedPath)
        if os.path.exists(parent):
            return removedPath
        else:
            return self.findRemovedPath(parent)

    def getLatestFileEdits(self, topLevelForEdit, fileEditData):
        traffic = []
        removedPaths = []
        self.diag.debug("Getting latest file edits " + repr(topLevelForEdit))
        for file in topLevelForEdit:
            self.diag.debug("Looking for file edits under " + file)
            changedPaths = []
            newPaths = self.findFilesAndLinks(file)
            for subPath in newPaths:
                newEditInfo = self.getLatestModification(subPath)
                self.diag.debug("Found subpath " + subPath + " edit info " + repr(newEditInfo))
                if newEditInfo != fileEditData.get(subPath):
                    changedPaths.append(subPath)
                    fileEditData[subPath] = newEditInfo

            for oldPath in fileEditData.keys():
                if (oldPath == file or oldPath.startswith(file + os.sep)) and oldPath not in newPaths:
                    removedPath = self.findRemovedPath(oldPath)
                    self.diag.debug("Deletion of " + oldPath + "\n - registering " + removedPath)
                    removedPaths.append(oldPath)
                    if removedPath not in changedPaths:
                        changedPaths.append(removedPath)

            if len(changedPaths) > 0:
                traffic.append(fileedittraffic.FileEditTraffic.makeRecordedTraffic(file, changedPaths))

        for path in removedPaths:
            if path in fileEditData:
                del fileEditData[path]

        self.diag.debug("Done getting latest file edits.")
        return traffic

        

class ServerDispatcher(ServerDispatcherBase):
    def __init__(self, options):
        ServerDispatcherBase.__init__(self, options)
        self.terminate = False
        self.server = self.makeServer()
        address = self.server.getAddress()
        self.rcHandler.address = address
        sys.stdout.write(address + "\n") # Tell our caller, so they can tell the program being handled
        sys.stdout.flush()
    
    def makeServer(self):
        ipAddress = self.getIpAddress()
        return self.serverClass.createServer(ipAddress, self)

    def getIpAddress(self):
        # Code below can cause complications with VPNs etc. Default is local access only, sufficient in 99.9% of cases
        access = self.rcHandler.get("server_remote_access", [ "general" ], "false")
        if access == "false":
            return "127.0.0.1"
        elif access == "true":
            # return the IP address of the host as best we can
            try:
                # Doesn't always work, sometimes not available
                return socket.gethostbyname(socket.gethostname())
            except socket.error:
                # Most of the time we only need to be able to talk locally, fall back to that
                return socket.gethostbyname("localhost")
        else:
            # specific network interface, find the IP address of that as best we can
            # psutil is not a dependency, need to install it if you want this code to work!
            import psutil
            interface_addrs = psutil.net_if_addrs().get(access) or []
            for snicaddr in interface_addrs:
                if snicaddr.family == socket.AF_INET:
                    return snicaddr.address
            raise RuntimeError("Failed to find IP address for interface", repr(access), ", which is configured in your server_remote_access setting")

    def run(self):
        self.diag.debug("Starting capturemock server at " + self.server.getAddress())
        self.server.run()
        self.diag.debug("Shut down capturemock server")
        
    def shutdown(self):
        self.diag.debug("Told to shut down!")
        self.server.shutdown()
        
        
class ReplayOnlyDispatcher(ServerDispatcherBase):
    def __init__(self, replayFile, recordFile, rcFile):
        ReplayOptions = namedtuple("ReplayOptions", "mode replay record rcfiles")
        # don't read into ReplayInfo as normal, optimised for responses
        options = ReplayOptions(mode=config.RECORD, replay=None, record=recordFile, rcfiles=rcFile)
        ServerDispatcherBase.__init__(self, options)
        self.idFinder = id_mapping.IdFinder(self.rcHandler, "id_pattern_server")
        self.clientTrafficStrings = []
        self.replay_ids = []
        self.diag.debug("Replaying everything as client from " + replayFile)
        for trafficStr in ReplayInfo.readIntoList(replayFile):
            if trafficStr.startswith("<-"):
                self.clientTrafficStrings.append(trafficStr)
            elif self.idFinder:
                text = trafficStr.split(":", 1)[-1]
                self.diag.debug("Try to extracting IDs from response text " + text)
                currId = self.idFinder.extractIdFromText(text)
                if currId:
                    self.diag.debug("Extracting IDs from response data " + currId)
                    self.replay_ids.append(currId)
        
    def extractIdsFromResponses(self, responses):
        if not self.idFinder:
            return []
        
        ids = []
        self.diag.debug("Extracting IDs from responses")
        for traffic in responses:
            currId = self.idFinder.extractIdFromText(traffic.text)
            if currId:
                self.diag.debug("Extracting ID from traffic " + currId)
                ids.append(currId)
        return ids
            
    def add_id_mapping(self, traffic, replay_id, recorded_id):
        if self.rcHandler.add_section(replay_id):
            sectionNames = traffic.getAlterationSectionNames()
            self.rcHandler.addToList("alterations", sectionNames, replay_id)
            self.rcHandler.set(replay_id, "match_pattern", replay_id)
            self.rcHandler.set(replay_id, "replacement", recorded_id)

    def replay_all(self, **kw):
        # Pulls everything out of replay info - then we go to "record" mode
        recorded_ids = []
        alterations = {}
        for i, text in enumerate(self.clientTrafficStrings):
            self.diag.debug("Replaying traffic with text beginning " + text[:30] + "...")
            traffic = self.parseClientTraffic(text, **kw)
            responses = self.process(traffic, i + 1)
            for currId in self.extractIdsFromResponses(responses):
                if currId not in recorded_ids:
                    recorded_ids.append(currId)
                    replay_id = self.replay_ids.pop(0)
                    self.diag.debug("Adding ID mapping from " + replay_id + " to " + currId)
                    alterations[replay_id] = currId
                    self.add_id_mapping(traffic, replay_id, currId)
        self.diag.debug("Replaying all now complete")
        if alterations:
            id_mapping.make_id_alterations_rc_file(alterations)
    
    def parseClientTraffic(self, text, **kw):
        for cls in self.serverClass.getTrafficClasses(incoming=True):
            if cls.isClientClass():
                prefix = "<-" + cls.typeId + ":" if cls.typeId else ""
                if text.startswith(prefix):
                    return cls(text[6:], None, self.rcHandler, **kw)

# The basic point here is to make sure that traffic appears in the record
# file in the order in which it comes in, not in the order in which it completes (which is indeterministic and
# may be wrong next time around)
class RecordFileHandler(recordfilehandler.RecordFileHandler):
    def __init__(self, file):
        super(RecordFileHandler, self).__init__(file)
        self.recordingRequest = 1
        self.cache = {}
        self.completedRequests = []
        self.lock = threading.Lock()

    def requestComplete(self, requestNumber):
        self.lock.acquire()
        if requestNumber == self.recordingRequest:
            self.recordingRequestComplete()
        else:
            self.completedRequests.append(requestNumber)
        self.lock.release()

    def writeFromCache(self):
        text = self.cache.get(self.recordingRequest)
        if text:
            super(RecordFileHandler, self).record(text)
            del self.cache[self.recordingRequest]

    def recordingRequestComplete(self):
        self.writeFromCache()
        self.recordingRequest += 1
        if self.recordingRequest in self.completedRequests:
            self.recordingRequestComplete()

    def record(self, text, requestNumber):
        self.lock.acquire()
        if requestNumber == self.recordingRequest:
            self.writeFromCache()
            super(RecordFileHandler, self).record(text)
        else:
            self.cache.setdefault(requestNumber, "")
            self.cache[requestNumber] += text
        self.lock.release()


def main():
    parser = cmdlineutils.create_option_parser()
    options = parser.parse_args()[0] # no positional arguments

    fileedittraffic.FileEditTraffic.configure(options)

    server = ServerDispatcher(options)
    server.run()


if __name__ == "__main__":
    main()
