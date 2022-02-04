from .capturepython import interceptPython
from .capturecommand import interceptCommand
from .config import CaptureMockReplayError, RECORD, REPLAY, REPLAY_OLD_RECORD_NEW
from . import config, cmdlineutils
import os, sys, shutil, filecmp, subprocess, tempfile, types
from functools import wraps
from glob import glob
from collections import namedtuple
from datetime import datetime

class CaptureMockManager:
    fileContents = "import capturemock; capturemock.interceptCommand()\n"
    def __init__(self):
        self.serverProcess = None
        self.serverAddress = None

    def startServer(self,
                    mode,
                    recordFile,
                    replayFile=None,
                    recordEditDir=None,
                    replayEditDir=None,
                    rcFiles=[],
                    interceptDir=None,
                    sutDirectory=os.getcwd(),
                    environment=os.environ,
                    stderrFn=None):
        if config.isActive(mode, replayFile):
            # Environment which the server should get
            environment["CAPTUREMOCK_MODE"] = str(mode)
            environment["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rcHandler = config.RcFileHandler(rcFiles)
            commands = rcHandler.getIntercepts("command line")
            for var in [ "CAPTUREMOCK_PROCESS_START", "CAPTUREMOCK_SERVER" ]:
                if var in environment:
                    del environment[var]

            from . import server
            self.serverProcess = server.startServer(rcFiles,
                                                    mode,
                                                    replayFile,
                                                    replayEditDir,
                                                    recordFile,
                                                    recordEditDir,
                                                    sutDirectory,
                                                    environment,
                                                    stderrFn)
            self.serverAddress = self.serverProcess.stdout.readline().strip()
            self.serverProtocol = rcHandler.get("server_protocol", [ "general" ], "classic")

            # And environment it shouldn't get...
            environment["CAPTUREMOCK_SERVER"] = self.serverAddress
            if self.makePathIntercepts(commands, interceptDir, replayFile, mode):
                environment["PATH"] = interceptDir + os.pathsep + environment.get("PATH", "")
            return True
        else:
            return False

    def makeWindowsIntercept(self, interceptName):
        destFile = interceptName + ".exe"
        if sys.version_info.major == 3: # python 3, uses pyinstaller
            sourceFile = os.path.join(os.path.dirname(__file__), "capturemock_intercept.exe")
            if not os.path.isfile(sourceFile) and getattr(sys, 'frozen', False): # from exe file, likely TextTest
                sourceFile = os.path.join(os.path.dirname(sys.executable), "capturemock_intercept.exe")
        else: # python2, uses old exemaker executable
            file = open(interceptName + ".py", "w")
            file.write("#!python.exe\nimport site\n")
            file.write(self.fileContents)
            file.close()
            sourceFile = os.path.join(os.path.dirname(__file__), "python_script.exe")
        shutil.copy(sourceFile, destFile)

    def makePosixIntercept(self, interceptName):
        file = open(interceptName, "w")
        file.write("#!" + sys.executable + "\n")
        file.write(self.fileContents)
        file.close()
        os.chmod(interceptName, 0o775) # make executable

    def makePathIntercept(self, cmd, interceptDir):
        if not os.path.isdir(interceptDir):
            os.makedirs(interceptDir)
        interceptName = os.path.join(interceptDir, cmd)
        if os.name == "nt":
            self.makeWindowsIntercept(interceptName)
        else:
            self.makePosixIntercept(interceptName)

    def filterAbsolute(self, commands):
        relativeCmds = []
        for cmd in commands:
            if os.path.isabs(cmd):
                sys.stderr.write("WARNING: Ignoring requested intercept of command " + repr(cmd) + ".\n" +
                                 "CaptureMock intercepts commands via PATH and cannot do anything with absolute paths.\n")
            else:
                relativeCmds.append(cmd)
        return relativeCmds

    def makePathIntercepts(self, commands, interceptDir, replayFile, mode):
        commands = self.filterAbsolute(commands)
        if replayFile and mode == config.REPLAY:
            from . import replayinfo
            commands = replayinfo.filterCommands(commands, replayFile)
        for command in commands:
            self.makePathIntercept(command, interceptDir)
        return len(commands) > 0

    def terminate(self):
        if self.serverProcess:
            if self.serverAddress:
                from .server import stopServer
                stopServer(self.serverAddress, self.serverProtocol)
            self.writeServerErrors()
            self.serverProcess = None

    def writeServerErrors(self):
        out, err = self.serverProcess.communicate()
        if out:
            sys.stdout.write("Output from CaptureMock Server :\n" + out)
        if err:
            sys.stderr.write("Error from CaptureMock Server :\n" + err)


def setUpPython(mode, recordFile, replayFile=None,
                rcFiles=[], pythonAttrs=[], environment=os.environ):
    if config.isActive(mode, replayFile):
        # Environment which the server should get
        environment["CAPTUREMOCK_MODE"] = str(mode)
        if replayFile and mode != RECORD:
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
    parser.disable_interspersed_args()
    options, args = parser.parse_args()
    if len(args) == 0:
        return parser.print_help()
    interceptDir = tempfile.mkdtemp()
    rcFiles = []
    if options.rcfiles:
        rcFiles = options.rcfiles.split(",")
    mode = options.mode
    if mode == REPLAY and options.replay is None:
        mode = RECORD
    # Start with a fresh file
    if options.record and os.path.isfile(options.record):
        os.remove(options.record)

    setUpServer(mode, options.record, options.replay,
                recordEditDir=options.record_file_edits, replayEditDir=options.replay_file_edits,
                rcFiles=rcFiles, interceptDir=interceptDir)
    subprocess.call(args)
    if os.path.exists(interceptDir):
        shutil.rmtree(interceptDir)
    terminate()

def replay_for_server(rcFile, replayFile, recordFile=None, serverAddress=None, **kw):
    ReplayOptions = namedtuple("ReplayOptions", "mode replay record rcfiles")
    options = ReplayOptions(mode=RECORD, replay=replayFile, record=recordFile, rcfiles=rcFile)
    from .server import ServerDispatcherBase
    dispatcher = ServerDispatcherBase(options)
    if serverAddress:
        from .clientservertraffic import ClientSocketTraffic
        ClientSocketTraffic.setServerLocation(serverAddress, True)
    dispatcher.replay_all(**kw)

def add_timestamp_data(data_by_timestamp, ts, fn, currText):
    tsdict = data_by_timestamp.setdefault(ts, {})
    if fn in tsdict:
        tsdict[fn] += currText
    else:
        tsdict[fn] = currText

class PrefixContext:
    def __init__(self, parseFn=None, ext_servers=[]):
        self.parseFn = parseFn
        self.ext_servers = ext_servers
        self.fns = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.clear()

    def sort_key(self, fn):
        if any((ext_server in fn for ext_server in self.ext_servers)):
            return "zzz" + fn
        else:
            return fn

    def clear(self):
        if self.all_same_server():
            self.sort_clients(self.fns)
        self.fns.clear()

    def sort_clients(self, fns):
        if len(fns) < 2:
            return

        newFns = [ newFn for newFn, _, _ in fns.values() ]
        baseIndex = int(newFns[0][:3])
        without_prefix = [ fn[3:] for fn in newFns ]
        without_prefix.sort(key=self.sort_key)
        for fn in newFns:
            tail = fn[3:]
            index = without_prefix.index(tail) + baseIndex
            newName = str(index).zfill(3) + tail
            os.rename(fn, newName)

    def remove_non_matching(self, item, ix):
        removed = {}
        for fn, info in self.fns.items():
            if info[ix] != item and info[2] not in self.ext_servers:
                removed[fn] = info
        for fn, info in removed.items():
            del self.fns[fn]
        return removed

    def all_same_server(self):
        servers = set()
        for _, _, server in self.fns.values():
            if server not in self.ext_servers:
                servers.add(server)
        return len(servers) == 1

    def find_most_recent(self):
        for _, currClient, currServer in reversed(self.fns.values()):
            if currServer not in self.ext_servers:
                return currClient, currServer
        return None, None

    def add(self, fn, newFn):
        client, server = None, None
        if self.parseFn:
            client, server, _ = self.parseFn(fn)
            currClient, currServer = None, None
            if len(self.fns) > 0:
                currClient, currServer = self.find_most_recent()
            if currClient is not None:
                if client == currClient:
                    self.remove_non_matching(client, 1)
                elif server == currServer:
                    removed = self.remove_non_matching(server, 2)
                    self.sort_clients(removed)
                elif server not in self.ext_servers:
                    self.clear()

        self.fns[fn] = newFn, client, server

    def get(self, fn):
        info = self.fns.get(fn)
        if info is not None:
            return info[0]

class DefaultTimestamper:
    def __init__(self):
        self.index = 1

    def stamp(self):
        ts = datetime.fromordinal(self.index).isoformat()
        self.index += 1
        return ts

# utility for sorting multiple Capturemock recordings so they can be replayed in the right order
# writes to current working directory
# Anything without timestamps is assumed to come first
def add_prefix_by_timestamp(recorded_files, ignoredIndicesIn=None, sep="-", ext=None, parseFn=None, ext_servers=[]):
    ignoredIndices = ignoredIndicesIn or {}
    timestampPrefix = "--TIM:"
    data_by_timestamp = {}
    for timestamp in ignoredIndices.values():
        data_by_timestamp[timestamp] = None
    default_stamper = DefaultTimestamper()
    for fn in recorded_files:
        currText = ""
        curr_timestamp = None
        with open(fn) as f:
            for line in f:
                if line.startswith("<-"):
                    if currText:
                        ts = curr_timestamp or default_stamper.stamp()
                        add_timestamp_data(data_by_timestamp, ts, fn, currText)
                    currText = line
                    curr_timestamp = None
                elif line.startswith(timestampPrefix):
                    if curr_timestamp is None: # go with the first timestamp if there are many
                        curr_timestamp = line[len(timestampPrefix):].strip()
                else:
                    currText += line
        ts = curr_timestamp or default_stamper.stamp()
        add_timestamp_data(data_by_timestamp, ts, fn, currText)
    currIndex = 0
    with PrefixContext(parseFn, ext_servers) as currContext:
        new_files = []
        for timestamp in sorted(data_by_timestamp.keys()):
            timestamp_data = data_by_timestamp.get(timestamp)
            if timestamp_data is None:
                # simulated or implied action, do not try to combine events before and after
                currContext.clear()
                continue
            timestamp_filenames = list(timestamp_data.keys())
            for fn in timestamp_filenames:
                currText = timestamp_data.get(fn)
                newFn = currContext.get(fn)
                if newFn is None:
                    currIndex += 1
                    while currIndex in ignoredIndices:
                        currIndex += 1
                    # original file might already have a prefix, drop it if so
                    newPrefix = str(currIndex).zfill(3) + sep
                    if fn[3] == "-" and fn[2].isdigit():
                        newFn = newPrefix + fn[4:]
                    else:
                        newFn = newPrefix + fn
                    if ext:
                        stem = newFn.rsplit(".", 1)[0]
                        newFn = stem + "." + ext
                    if newFn == fn:
                        os.rename(fn, fn + ".orig")
                    new_files.append(newFn)
                    currContext.add(fn, newFn)
                with open(newFn, "a") as currFile:
                    currFile.write(currText)
    return new_files

def get_traffic_count(rpfn):
    count = 0
    with open(rpfn) as f:
        for line in f:
            if line.startswith("<-") or line.startswith("->"):
                count += 1
    return count

def find_recorded_position(rpfn, text):
    curr_text = ""
    count = 0
    with open(rpfn) as f:
        for line in f:
            if line.startswith("<-") or line.startswith("->"):
                if curr_text == text:
                    return count - 1
                count += 1
                curr_text = line
            elif not line.startswith("--TIM:"):
                curr_text += line
    

def read_first_traffic(rpfn):
    text = ""
    with open(rpfn) as f:
        for line in f:
            if line.startswith("->"):
                return text
            text += line
    return text

def find_replay_files(stem, replayed_files, fn):
    fns = {}
    replay_count = 0
    for i, rpfn in enumerate(sorted(replayed_files)):
        curr_stem = rpfn[4:].rsplit(".", 1)[0]
        if curr_stem == stem:
            if i == len(replayed_files) - 1:
                # Last file, don't care
                curr_count = 10000
            else:
                curr_count = get_traffic_count(rpfn)
                replay_count += curr_count
            fns[rpfn] = curr_count
    if len(replayed_files) == 1:
        return list(fns.items())

    record_count = get_traffic_count(fn)
    if record_count == replay_count:
        return list(fns.items())
    prev_replay_fn = None
    for i, replay_fn in enumerate(fns):
        firstTraffic = read_first_traffic(replay_fn)
        position_in_recorded = find_recorded_position(fn, firstTraffic)
        if position_in_recorded is not None and prev_replay_fn is not None:
            fns[prev_replay_fn] = position_in_recorded
        prev_replay_fn = replay_fn
    return list(fns.items())


def open_new_record_file(replay_fns, repIndex, ext):
    curr_replay_fn, curr_replay_count = replay_fns[repIndex]
    new_record_fn = curr_replay_fn.rsplit(".", 1)[0] + "." + ext
    new_record_file = open(new_record_fn, "w")
    return new_record_file, curr_replay_count

def add_prefix_by_matching_replay(recorded_files, replayed_files, ext=None):
    for fn in recorded_files:
        if fn[3] == "-" and fn[2].isdigit():
            # already prefixed, no division needed
            new_record_fn = fn.rsplit(".", 1)[0] + "." + ext
            with open(new_record_fn, "w") as fw:
                with open(fn) as f:
                    for line in f:
                        if not line.startswith("--TIM:"):
                            fw.write(line)
            continue
        stem = fn.rsplit(".", 1)[0]
        replay_fns = find_replay_files(stem, replayed_files, fn)
        recIndex = 0
        repIndex = 0
        new_record_file, curr_replay_count = open_new_record_file(replay_fns, repIndex, ext)
        with open(fn) as f:
            for line in f:
                if line.startswith("<-") or line.startswith("->"):
                    recIndex += 1
                    if recIndex > curr_replay_count and line.startswith("<-") and repIndex + 1 < len(replay_fns):
                        repIndex += 1
                        recIndex = 1
                        new_record_file.close()
                        new_record_file, curr_replay_count = open_new_record_file(replay_fns, repIndex, ext)
                if not line.startswith("--TIM:"):
                    new_record_file.write(line)
        new_record_file.close()


def capturemock(pythonAttrsOrFunc=[], *args, **kw):
    if isinstance(pythonAttrsOrFunc, types.FunctionType):
        return CaptureMockDecorator(stackDistance=2)(pythonAttrsOrFunc)
    else:
        return CaptureMockDecorator(pythonAttrsOrFunc, *args, **kw)


# For use as a decorator in coded tests
class CaptureMockDecorator(object):
    defaultMode = int(os.getenv("CAPTUREMOCK_MODE", "0"))
    defaultPythonAttrs = []
    defaultRcFiles = list(filter(os.path.isfile, [".capturemockrc"]))
    @classmethod
    def set_defaults(cls, pythonAttrs=[], mode=None, rcFiles=[]):
        if rcFiles:
            cls.defaultRcFiles = rcFiles
        cls.defaultPythonAttrs = pythonAttrs
        if mode is not None:
            cls.defaultMode = mode

    def __init__(self, pythonAttrs=[], mode=None, rcFiles=[], stackDistance=1):
        if mode is not None:
            self.mode = mode
        else:
            self.mode = self.defaultMode
        self.pythonAttrs = pythonAttrs or self.defaultPythonAttrs
        if not isinstance(self.pythonAttrs, list):
            self.pythonAttrs = [ self.pythonAttrs ]
        self.rcFiles = rcFiles or self.defaultRcFiles
        self.stackDistance = stackDistance

    def __call__(self, func):
        from inspect import stack
        callingFile = stack()[self.stackDistance][1]
        fileNameRoot = self.getFileNameRoot(func.__name__, callingFile)
        replayFile = None if self.mode == config.RECORD else fileNameRoot
        if not config.isActive(self.mode, replayFile):
            return func
        recordFile = tempfile.mktemp()
        @wraps(func)
        def wrapped_func(*funcargs, **funckw):
            interceptor = None
            try:
                setUpPython(self.mode, recordFile, replayFile, self.rcFiles, self.pythonAttrs)
                interceptor = interceptPython(self.mode, recordFile, replayFile, self.rcFiles, self.pythonAttrs)
                result = func(*funcargs, **funckw)
                if self.mode == config.REPLAY:
                    self.checkMatching(recordFile, replayFile)
                elif os.path.isfile(recordFile):
                    shutil.move(recordFile, fileNameRoot)
                return result
            finally:
                if interceptor:
                    interceptor.resetIntercepts()
                if os.path.isfile(recordFile):
                    os.remove(recordFile)
                terminate()
        return wrapped_func

    def fileContentsEqual(self, fn1, fn2):
        bufsize = 8*1024
        # copied from filecmp.py, adding universal line ending support
        with open(fn1, newline=None) as fp1, open(fn2, newline=None) as fp2:
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

set_defaults = CaptureMockDecorator.set_defaults
