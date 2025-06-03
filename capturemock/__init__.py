from .capturepython import interceptPython
from .capturecommand import interceptCommand
from .fileedittraffic import FileEditTraffic
from .id_mapping import ID_ALTERATIONS_RC_FILE
from .config import CaptureMockReplayError, RECORD, REPLAY, REPLAY_OLD_RECORD_NEW
from . import config, cmdlineutils
import os, sys, shutil, filecmp, subprocess, tempfile, types
from functools import wraps
from glob import glob
from collections import namedtuple
from datetime import datetime
import bisect
from urllib.request import urlopen

version = "2.6.3"

class CaptureMockManager:
    fileContents = "import capturemock; capturemock.interceptCommand()\n"
    def __init__(self):
        self.serverProcess = None
        self.serverAddress = None


    def readServerAddress(self):
        address = self.serverProcess.stdout.readline().strip()
        while self.serverProtocol == "amqp" and address and not "/" in address:
            # pika connection setup seems to write errors to stdout sometimes, throw them away
            address = self.serverProcess.stdout.readline().strip()
        return address

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
                    stderrFn=None,
                    recordFromUrl=None):
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
            self.serverProtocol = rcHandler.get("server_protocol", [ "general" ], "classic")
            self.serverAddress = self.readServerAddress()

            # And environment it shouldn't get...
            environment["CAPTUREMOCK_SERVER"] = self.serverAddress
            if self.makePathIntercepts(commands, interceptDir, replayFile, mode):
                environment["PATH"] = interceptDir + os.pathsep + environment.get("PATH", "")
            if recordFromUrl and self.serverProtocol == "http":
                self.sendServerLocation(recordFromUrl)
            return True
        else:
            return False
        
    def sendServerLocation(self, url):
        setUrl = self.serverAddress + "/capturemock/setServerLocation"
        urlopen(setUrl, data=url.encode()).read()
        
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
        try:
            out, err = self.serverProcess.communicate(timeout=30)
            if out:
                sys.stdout.write("Output from CaptureMock Server :\n" + out)
            if err:
                sys.stderr.write("Error from CaptureMock Server :\n" + err)
        except subprocess.TimeoutExpired:
            print("CaptureMock Server did not terminate after 30 seconds, killing hard!", file=sys.stderr)
            self.serverProcess.kill()


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


def start_server_from_texttest(recordFromUrl=None, **kw):
    env = os.environ.copy()
    rcFilesStr = os.getenv("TEXTTEST_CAPTUREMOCK_RCFILES")
    setUpServer(os.getenv("TEXTTEST_CAPTUREMOCK_MODE"),
                os.getenv("TEXTTEST_CAPTUREMOCK_RECORD"),
                os.getenv("TEXTTEST_CAPTUREMOCK_REPLAY"),
                os.getenv("TEXTTEST_CAPTUREMOCK_RECORD_EDIT_DIR"),
                os.getenv("TEXTTEST_CAPTUREMOCK_REPLAY_EDIT_DIR"),
                rcFilesStr.split(",") if rcFilesStr else [],
                environment=env,
                recordFromUrl=recordFromUrl,
                **kw)
    return env


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

def texttest_is_recording():
    return int(os.getenv("TEXTTEST_CAPTUREMOCK_MODE", "0")) == RECORD or os.getenv("TEXTTEST_CAPTUREMOCK_REPLAY") is None

def replay_for_server(rcFile=None, replayFile=None, recordFile=None, serverAddress=None, replayEditDir=None, recordEditDir=None, **kw):
    if rcFile is None:
        rcFile = os.getenv("TEXTTEST_CAPTUREMOCK_RCFILES")
    if replayFile is None:
        replayFile = os.getenv("TEXTTEST_CAPTUREMOCK_REPLAY")
    if recordFile is None:
        recordFile = os.getenv("TEXTTEST_CAPTUREMOCK_RECORD")
    if replayEditDir is None:
        replayEditDir = os.getenv("TEXTTEST_CAPTUREMOCK_REPLAY_EDIT_DIR")
    if recordEditDir is None:
        recordEditDir = os.getenv("TEXTTEST_CAPTUREMOCK_RECORD_EDIT_DIR")

    FileOptions = namedtuple("FileOptions", "replay_file_edits record_file_edits")
    foptions = FileOptions(replay_file_edits=replayEditDir, record_file_edits=recordEditDir)
    FileEditTraffic.configure(foptions)
    from .server import ReplayOnlyDispatcher
    dispatcher = ReplayOnlyDispatcher(replayFile, recordFile, rcFile)
    if serverAddress:
        from .clientservertraffic import ClientSocketTraffic
        ClientSocketTraffic.setServerLocation(serverAddress, True)
    dispatcher.replay_all(**kw)
    
def add_timestamp_data(data_by_timestamp, given_ts, fn, currText, fn_timestamps):
    if currText.startswith("->"): # it's a reply, must match with best client traffic
        index = bisect.bisect(fn_timestamps, given_ts)
        best_index = index - 1 if index > 0 else 0
        ts = fn_timestamps[best_index]
    else:
        fn_timestamps.append(given_ts)
        ts = given_ts
    tsdict = data_by_timestamp.setdefault(ts, {})
    if fn in tsdict:
        tsdict[fn] += currText
    else:
        tsdict[fn] = currText

class PrefixContext:
    def __init__(self, parseFn, strictOrderClients):
        self.parseFn = parseFn
        self.strictOrderClients = strictOrderClients
        self.fns = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.clear()

    def clear(self):
        if self.all_same_server_different_clients():
            self.sort_clients(self.fns)
        self.fns.clear()

    def sort_clients(self, fns):
        if len(fns) < 2:
            return

        newFns = [ newFn for newFn, _, _ in fns.values() ]
        baseIndex = int(newFns[0][:3])
        without_prefix = [ fn[3:] for fn in newFns ]
        without_prefix.sort()
        for fn in newFns:
            tail = fn[3:]
            index = without_prefix.index(tail) + baseIndex
            newName = str(index).zfill(3) + tail
            os.rename(fn, newName)

    def remove_non_matching(self, item, ix):
        removed = {}
        for fn, info in self.fns.items():
            if info[ix] != item:
                removed[fn] = info
        for fn, info in removed.items():
            del self.fns[fn]
        return removed

    def all_same_server_different_clients(self):
        servers = set()
        clients = set()
        for _, client, server in self.fns.values():
            if client in self.strictOrderClients:
                return False
            servers.add(server)
            clients.add(client)
        return len(servers) == 1 and len(clients) > 1

    def find_most_recent(self):
        for _, currClient, currServer in reversed(self.fns.values()):
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
                if client not in self.strictOrderClients:
                    if client == currClient:
                        self.remove_non_matching(client, 1)
                    elif server == currServer:
                        removed = self.remove_non_matching(server, 2)
                        self.sort_clients(removed)
                    else:
                        self.clear()
                else:
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

def create_map_by_timestamp(recorded_files, ignoredIndices={}):
    timestampPrefix = "--TIM:"
    data_by_timestamp = {}
    for timestamp in ignoredIndices.values():
        data_by_timestamp[timestamp] = None
    
    default_stamper = DefaultTimestamper()
    for fn in recorded_files:
        currText = ""
        curr_timestamp = None
        fn_timestamps = []
        with open(fn) as f:
            for line in f:
                if line.startswith("<-") or line.startswith("->RMQ"):
                    if currText:
                        ts = curr_timestamp or default_stamper.stamp()
                        add_timestamp_data(data_by_timestamp, ts, fn, currText, fn_timestamps)
                    currText = line
                    curr_timestamp = None
                elif line.startswith(timestampPrefix):
                    if curr_timestamp is None: # go with the first timestamp if there are many
                        curr_timestamp = line[len(timestampPrefix):].strip()
                else:
                    currText += line
        
        ts = curr_timestamp or default_stamper.stamp()
        add_timestamp_data(data_by_timestamp, ts, fn, currText, fn_timestamps)
    
    return data_by_timestamp

def add_prefix_by_timestamp(recorded_files, ignoredIndicesIn=None, sep="-", ext=None, parseFn=None, strictOrderClients=None):
    ignoredIndices = ignoredIndicesIn or {}
    data_by_timestamp = create_map_by_timestamp(recorded_files, ignoredIndices)
    return add_prefixes_from_timestamp_map(data_by_timestamp, ignoredIndices, sep, ext, parseFn, strictOrderClients)

def add_prefixes_from_timestamp_map(data_by_timestamp, ignoredIndicesIn=None, sep="-", ext=None, parseFn=None, strictOrderClients=None):
    ignoredIndices = ignoredIndicesIn or {}
    currIndex = 0
    with PrefixContext(parseFn, strictOrderClients or []) as currContext:
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
            if line.startswith("<-"):
                count += 1
    return count

def find_recorded_position(rpfn, text, skip):
    curr_text = ""
    count = 0
    searching = False
    curr_skip = 0
    with open(rpfn) as f:
        for line in f:
            if searching:
                if line.startswith("<-") or line.startswith("->"):
                    if curr_text == text:
                        if curr_skip == skip:
                            return count - 1
                        else:
                            curr_skip += 1
                elif not line.startswith("--TIM:"):
                    curr_text += line
            if line.startswith("<-"):
                searching = True
                count += 1
                curr_text = line
            if line.startswith("->"):
                searching = False

def read_first_traffic(rpfn):
    text = ""
    with open(rpfn) as f:
        for line in f:
            if text and (line.startswith("->") or line.startswith("<-")):
                return text
            text += line
    return text

def get_text_count(rpfn, text):
    with open(rpfn) as f:
        return f.read().count(text)

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
    prev_first_traffic = None
    for i, replay_fn in enumerate(fns):
        firstTraffic = read_first_traffic(replay_fn)
        if prev_replay_fn is not None:
            skip = get_text_count(prev_replay_fn, firstTraffic) if firstTraffic == prev_first_traffic else 0
            position_in_recorded = find_recorded_position(fn, firstTraffic, skip)
            if position_in_recorded is not None:
                fns[prev_replay_fn] = position_in_recorded
        prev_replay_fn = replay_fn
        prev_first_traffic = firstTraffic
    return list(fns.items())


def open_new_record_file(replay_fns, repIndex, ext):
    curr_replay_fn, curr_replay_count = replay_fns[repIndex]
    new_record_fn = curr_replay_fn.rsplit(".", 1)[0] + "." + ext
    new_record_file = open(new_record_fn, "w")
    return new_record_file, curr_replay_count

def transform_to_amqp_client_replay(fn, new_fn):
    writeFile = None
    active = False
    with open(fn) as f:
        for line in f:
            if line.startswith("->RMQ"):
                if writeFile is None:
                    writeFile = open(new_fn, "w")
                writeFile.write(line.replace("->", "<-"))
                active = True
            elif line.startswith("<-") or line.startswith("->"):
                active = False
            elif active:
                writeFile.write(line)
    if writeFile is not None:
        writeFile.close()
        return True
    else:
        return False

def add_prefix_by_matching_replay(recorded_files, replayed_files, ext=None):
    for fn in recorded_files:
        hasPrefix = fn[3] == "-" and fn[2].isdigit()
        timestamp_data = create_map_by_timestamp([ fn ])
        if hasPrefix:
            # already prefixed, no division needed
            new_record_fn = fn.rsplit(".", 1)[0] + "." + ext
            with open(new_record_fn, "w") as fw:
                for ts in sorted(timestamp_data.keys()):
                    currText = timestamp_data.get(ts).get(fn)
                    fw.write(currText)
            continue
        stem = fn.rsplit(".", 1)[0]
        replay_fns = find_replay_files(stem, replayed_files, fn)
        if len(replay_fns) == 0:
            print("Failed to find replay files for", fn, file=sys.stderr)
            continue
        #print(fn)
        #from pprint import pprint
        #pprint(replay_fns)
        recIndex = 0
        repIndex = 0
        new_record_file, curr_replay_count = open_new_record_file(replay_fns, repIndex, ext)
        for ts in sorted(timestamp_data.keys()):
            currText = timestamp_data.get(ts).get(fn)
            recIndex += 1
            if recIndex > curr_replay_count and currText.startswith("<-") and repIndex + 1 < len(replay_fns):
                repIndex += 1
                recIndex = 1
                new_record_file.close()
                new_record_file, curr_replay_count = open_new_record_file(replay_fns, repIndex, ext)

            new_record_file.write(currText)
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
        dirName = os.path.join(os.path.dirname(callingFile), "mocks")
        if not os.path.isdir(dirName):
            os.makedirs(dirName)
        return os.path.join(dirName, funcName.replace("test_", "") + ".mock")

set_defaults = CaptureMockDecorator.set_defaults
