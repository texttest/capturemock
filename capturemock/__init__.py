
from capturepython import interceptPython, resetIntercepts
from capturecommand import interceptCommand
from config import CaptureMockReplayError

import os, sys, shutil, socket, config, cmdlineutils, filecmp, subprocess, tempfile

class CaptureMockManager:
    fileContents = "import capturemock; capturemock.interceptCommand()\n"
    def __init__(self):
        self.serverProcess = None
        self.serverAddress = None
        
    def startServer(self, mode, recordFile, replayFile=None,
                    recordEditDir=None, replayEditDir=None, rcFiles=[], interceptDir=None,
                    sutDirectory=os.getcwd(), environment=os.environ):
        if config.isActive(mode, replayFile):
            # Environment which the server should get
            environment["CAPTUREMOCK_MODE"] = str(mode)
            rcHandler = config.RcFileHandler(rcFiles)
            commands = rcHandler.getIntercepts("command line")
            from server import startServer
            for var in [ "CAPTUREMOCK_PROCESS_START", "CAPTUREMOCK_SERVER" ]:
                if var in environment:
                    del environment[var]
            self.serverProcess = startServer(rcFiles, mode, replayFile, replayEditDir,
                                             recordFile, recordEditDir, sutDirectory,
                                             environment)
            self.serverAddress = self.serverProcess.stdout.readline().strip()
            # And environment it shouldn't get...
            environment["CAPTUREMOCK_SERVER"] = self.serverAddress
            if self.makePathIntercepts(commands, interceptDir, replayFile, mode):
                environment["PATH"] = interceptDir + os.pathsep + environment.get("PATH", "")
            return True
        else:
            return False
        
    def makeWindowsIntercept(self, interceptName):
        file = open(interceptName + ".py", "w")    
        file.write("#!python.exe\nimport site\n")
        file.write(self.fileContents)
        file.close()
        sourceFile = os.path.join(os.path.dirname(__file__), "python_script.exe")
        destFile = interceptName + ".exe"
        shutil.copy(sourceFile, destFile)

    def makePosixIntercept(self, interceptName):
        file = open(interceptName, "w")
        file.write("#!" + sys.executable + "\n")
        file.write(self.fileContents)
        file.close()
        os.chmod(interceptName, 0775) # make executable 

    def makePathIntercept(self, cmd, interceptDir):
        if not os.path.isdir(interceptDir):
            os.makedirs(interceptDir)
        interceptName = os.path.join(interceptDir, cmd)
        if os.name == "nt":
            self.makeWindowsIntercept(interceptName)
        else:
            self.makePosixIntercept(interceptName)

    def makePathIntercepts(self, commands, interceptDir, replayFile, mode):
        if replayFile and mode == config.REPLAY_ONLY_MODE:
            import replayinfo
            commands = replayinfo.filterCommands(commands, replayFile)
        for command in commands:
            self.makePathIntercept(command, interceptDir)
        return len(commands) > 0

    def terminate(self):
        if self.serverProcess:
            if self.serverAddress:
                from server import stopServer
                stopServer(self.serverAddress)
            self.writeServerErrors()
            self.serverProcess = None
        
    def writeServerErrors(self):
        err = self.serverProcess.communicate()[1]
        if err:
            sys.stderr.write("Error from CaptureMock Server :\n" + err)


def setUpPython(mode, recordFile, replayFile=None,
                rcFiles=[], pythonAttrs=[], environment=os.environ):
    if config.isActive(mode, replayFile):
        # Environment which the server should get
        environment["CAPTUREMOCK_MODE"] = str(mode)
        if replayFile:
            environment["CAPTUREMOCK_REPLAY_FILE"] = replayFile
        environment["CAPTUREMOCK_RECORD_FILE"] = recordFile
        environment["CAPTUREMOCK_PROCESS_START"] = ",".join(rcFiles)
        environment["CAPTUREMOCK_PYTHON"] = ",".join(pythonAttrs)
        return True
    else:
        return False            

                            
def process_startup():
    rcFileStr = os.getenv("CAPTUREMOCK_PROCESS_START")
    if rcFileStr:
        import config
        rcFiles = rcFileStr.split(",")
        replayFile = os.getenv("CAPTUREMOCK_REPLAY_FILE")
        recordFile = os.getenv("CAPTUREMOCK_RECORD_FILE")
        mode = int(os.getenv("CAPTUREMOCK_MODE"))
        pythonAttrStr = os.getenv("CAPTUREMOCK_PYTHON")
        if pythonAttrStr:
            pythonAttrs = pythonAttrStr.split(",")
        else:
            pythonAttrs = []
        interceptPython(mode, recordFile, replayFile, rcFiles, pythonAttrs)


manager = None
def setUpServer(*args, **kw):
    global manager
    manager = CaptureMockManager()
    return manager.startServer(*args, **kw)


def terminate():
    if manager:
        manager.terminate()

def commandline():
    parser = cmdlineutils.create_option_parser()
    options, args = parser.parse_args()
    interceptDir = tempfile.mkdtemp() 
    setUpServer(options.mode, options.record, options.replay,  
                recordEditDir=options.record_file_edits, replayEditDir=options.replay_file_edits,
                rcFiles=options.rcfiles.split(","), interceptDir=interceptDir)
    subprocess.call(args)
    shutil.rmtree(interceptDir)
    terminate()

    
# For use as a decorator in coded tests
class capturemock(object):
    def __init__(self, pythonAttrs=[], mode=None, rcFiles=[]):
        if mode is not None:
            self.mode = mode
        else:
            self.mode = int(os.getenv("CAPTUREMOCK_MODE", "0"))
        self.pythonAttrs = pythonAttrs
        if not isinstance(pythonAttrs, list):
            self.pythonAttrs = [ pythonAttrs ]
        self.rcFiles = rcFiles
                
    def __call__(self, func):
        from inspect import stack
        callingFile = stack()[1][1]
        fileNameRoot = self.getFileNameRoot(func.__name__, callingFile)
        if self.mode == config.RECORD_ONLY_MODE:
            recordFile = fileNameRoot
            replayFile = None
        elif self.mode == config.REPLAY_ONLY_MODE:
            replayFile = fileNameRoot
            recordFile = tempfile.mktemp()
        else:
            replayFile = fileNameRoot
            recordFile = fileNameRoot
        def wrapped_func(*funcargs, **funckw):
            try:
                setUpPython(self.mode, recordFile, replayFile, self.rcFiles, self.pythonAttrs)
                interceptPython(self.mode, recordFile, replayFile, self.rcFiles, self.pythonAttrs)
                func(*funcargs, **funckw)
                if self.mode == config.REPLAY_ONLY_MODE:
                    self.checkMatching(recordFile, replayFile)
            finally:
                resetIntercepts()
                terminate()
        return wrapped_func

    def fileContentsEqual(self, fn1, fn2):
        bufsize = 8*1024
        # copied from filecmp.py, adding universal line ending support
        fp1 = open(fn1, "rU")
        fp2 = open(fn2, "rU")
        while True:
            b1 = fp1.read(bufsize)
            b2 = fp2.read(bufsize)
            if b1 != b2:
                return False
            if not b1:
                return True

    def checkMatching(self, recordFile, replayFile):
        if os.path.isfile(recordFile):
            if self.fileContentsEqual(recordFile, replayFile):
                os.remove(recordFile)
            else:
                # files don't match
                shutil.move(recordFile, replayFile + ".tmp")
                raise CaptureMockReplayError("Replayed calls do not match those recorded. " +
                                             "Either rerun with capturemock in record mode " +
                                             "or update the stored mock file by hand.")

    def getFileNameRoot(self, funcName, callingFile):
        dirName = os.path.join(os.path.dirname(callingFile), "capturemock")
        if not os.path.isdir(dirName):
            os.makedirs(dirName)
        return os.path.join(dirName, funcName.replace("test_", "") + ".mock")
    
