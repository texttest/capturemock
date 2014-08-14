
import os, stat, sys, socket, threading, time, subprocess
from copy import copy
import config, recordfilehandler, cmdlineutils
import commandlinetraffic, fileedittraffic, clientservertraffic, customtraffic
from SocketServer import TCPServer, StreamRequestHandler
try:
    from xmlrpclib import Fault, ServerProxy
    from SimpleXMLRPCServer import SimpleXMLRPCServer
except ImportError: # Python 3
    from xmlrpc.client import Fault, ServerProxy
    from xmlrpc.server import SimpleXMLRPCServer
from ordereddict import OrderedDict
from replayinfo import ReplayInfo

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
    return sys.executable
            
def startServer(rcFiles, mode, replayFile, replayEditDir,
                recordFile, recordEditDir, sutDirectory, environment):
    cmdArgs = [ getPython(), __file__, "-m", str(mode) ]
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

    return subprocess.Popen(cmdArgs, env=environment.copy(), universal_newlines=True,
                            cwd=sutDirectory, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def stopServer(servAddr):
    if servAddr.startswith("http"):
        s = ServerProxy(servAddr)
        s.shutdownCaptureMockServer()
    else:
        try:
            ClassicTrafficServer.sendTerminateMessage(servAddr)
        except socket.error: # pragma: no cover - should be unreachable, just for robustness
            print("Could not send terminate message to CaptureMock server at " + servAddr + \
                  ", seemed not to be running anyway.")


class ClassicTrafficServer(TCPServer):
    def __init__(self, addrinfo, useThreads):
        TCPServer.__init__(self, addrinfo, TrafficRequestHandler)
        self.useThreads = useThreads
        self.terminate = False
        self.requestCount = 0
        
    def run(self):
        while not self.terminate:
            self.handle_request()
        # Join all remaining request threads so they don't
        # execute after Python interpreter has started to shut itself down.
        for t in threading.enumerate():
            if t.getName() == "request":
                t.join()

    @classmethod
    def sendTerminateMessage(cls, serverAddressStr):
        host, port = serverAddressStr.split(":")
        cls._sendTerminateMessage((host, int(port)))    

    @staticmethod
    def _sendTerminateMessage(serverAddress):
        sendSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sendSocket.connect(serverAddress)
        sendSocket.sendall("TERMINATE_SERVER\n")
        sendSocket.shutdown(2)
        
    @staticmethod
    def getTrafficClasses(incoming):
        if incoming:    
            return [ clientservertraffic.ServerStateTraffic, clientservertraffic.ClientSocketTraffic ]
        else:
            return [ clientservertraffic.ServerTraffic, clientservertraffic.ClientSocketTraffic ]

    def shutdown(self):
        if self.useThreads:
            # Setting terminate will only work if we do it in the main thread:
            # otherwise the main thread might be in a blocking call at the time
            # So we reset the thread flag and send a new message
            self.useThreads = False
            self._sendTerminateMessage(self.socket.getsockname())
        else:
            self.terminate = True
        
    def process_request_thread(self, request, client_address, requestCount):
        # Copied from ThreadingMixin, more or less
        # We store the order things appear in so we know what order they should go in the file
        try:
            TrafficRequestHandler(requestCount, request, client_address, self)
            self.close_request(request)
        except: # pragma : no cover - interpreter code in theory...
            self.handle_error(request, client_address)
            self.close_request(request)

    def process_request(self, request, client_address):
        self.requestCount += 1
        if self.useThreads:
            """Start a new thread to process the request."""
            t = threading.Thread(target = self.process_request_thread, name="request",
                                 args = (request, client_address, self.requestCount))
            t.start()
        else:
            self.process_request_thread(request, client_address, self.requestCount)

    def getAddress(self):
        host, port = self.socket.getsockname()
        return host + ":" + str(port)

class XmlRpcTrafficServer(SimpleXMLRPCServer):
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

    def _dispatch(self, method, params):
        try:
            self.dispatcher.diag.info("Received XMLRPC traffic " + method + repr(params))
            XmlRpcDispatchInstance.requestCount += 1
            if method == "shutdownCaptureMockServer":
                self.dispatcher.server.setShutdownFlag()
                return ""
            elif method == "setServerLocation":
                traffic = clientservertraffic.XmlRpcServerStateTraffic(params[0])
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
        
class ServerDispatcher:
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
        self.terminate = False
        self.hasAsynchronousEdits = False
        # Default value of 5 isn't very much...
        # There doesn't seem to be any disadvantage of allowing a longer queue, so we will increase it by a lot...
        self.request_queue_size = 500
        self.server = self.makeServer()
        sys.stdout.write(self.server.getAddress() + "\n") # Tell our caller, so they can tell the program being handled
        sys.stdout.flush()

    def makeServer(self):
        protocol = self.rcHandler.get("server_protocol", [ "general" ], "classic")
        ipAddress = self.getIpAddress()
        if protocol == "classic":
            TrafficRequestHandler.dispatcher = self
            return ClassicTrafficServer((ipAddress, 0), TrafficRequestHandler)
        elif protocol == "xmlrpc":
            server = XmlRpcTrafficServer((ipAddress, 0), logRequests=False)
            server.register_instance(XmlRpcDispatchInstance(self))
            return server
        
    def getIpAddress(self):
        try:
            # Doesn't always work, sometimes not available
            return socket.gethostbyname(socket.gethostname())
        except socket.error:
            # Most of the time we only need to be able to talk locally, fall back to that
            return socket.gethostbyname("localhost")
            
    def run(self):
        self.diag.debug("Starting capturemock server")
        self.server.run()
        self.diag.debug("Shut down capturemock server")
        
    def shutdown(self):
        self.diag.debug("Told to shut down!")
        self.server.shutdown()
        
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
        else:
            traffic = self.parseTraffic(text, wfile)
            self.process(traffic, reqNo)
            self.diag.debug("Finished processing incoming request")

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
        self.diag.debug("Processing traffic " + traffic.__class__.__name__)
        topLevelForEdit, fileEditData = self.addPossibleFileEdits(traffic)
        responses = self.getResponses(traffic, topLevelForEdit, fileEditData)
        traffic.record(self.recordFileHandler, reqNo)
        for response in responses:
            self.diag.debug("Response of type " + response.__class__.__name__ + " with text " + repr(response.text))
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
        for mod in [ commandlinetraffic, fileedittraffic, customtraffic, self.server ]:
            classes += mod.getTrafficClasses(incoming)
        return classes

    def getResponses(self, traffic, topLevelForEdit, fileEditData):
        if self.replayInfo.isActiveFor(traffic):
            self.diag.debug("Replay active for current command")
            replayedResponses = []
            filesMatched = []
            responseClasses = self.getTrafficClasses(incoming=False)
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
            return responseClass(text, traffic.responseFile, self.rcHandler)

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


class TrafficRequestHandler(StreamRequestHandler):
    dispatcher = None
    def __init__(self, requestNumber, *args):
        self.requestNumber = requestNumber
        StreamRequestHandler.__init__(self, *args)
        
    def handle(self):
        self.dispatcher.diag.debug("Received incoming request...")
        text = self.rfile.read()
        try:
            self.dispatcher.processText(text, self.wfile, self.requestNumber)
        except config.CaptureMockReplayError as e:
            self.wfile.write("CAPTUREMOCK MISMATCH: " + str(e))
        
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

        
        
if __name__ == "__main__":
    parser = cmdlineutils.create_option_parser()
    options = parser.parse_args()[0] # no positional arguments
    
    fileedittraffic.FileEditTraffic.configure(options)

    server = ServerDispatcher(options)
    server.run()
