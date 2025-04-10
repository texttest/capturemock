
""" Class to handle the interface with the rc file """
try:
    from configparser import ConfigParser
except ImportError: # python3
    from ConfigParser import ConfigParser
    
import os, sys, logging.config

REPLAY = 0
RECORD = 1
REPLAY_OLD_RECORD_NEW = 2

class CaptureMockReplayError(RuntimeError):
    pass

class RcFileHandler:
    def __init__(self, rcFiles):
        self.parser = ConfigParser(strict=False)
        self.diag = None
        self.address = None
        if rcFiles:
            for rcFile in rcFiles:
                if not os.path.isfile(rcFile):
                    sys.stderr.write("WARNING: RC file at " + rcFile + " does not exist, ignoring.\n")
        else:
            rcFiles = self.getPersonalPath("config")
        self.parser.read(rcFiles)

    def addFile(self, rcFile):
        self.parser.read(rcFile)

    def getPersonalPath(self, fileName):
        return os.path.join(os.path.expanduser("~/.capturemock"), fileName)

    def getIntercepts(self, section):
        return self.getList("intercepts", [ section ])

    def get(self, *args):
        return self._get(self.parser.get, *args)
    
    def getWithAddress(self, *args):
        rawValue = self.get(*args)
        if not self.address or "${CAPTUREMOCK_" not in rawValue:
            return rawValue
        port = self.address.rsplit(":", 1)[-1]
        return rawValue.replace("${CAPTUREMOCK_SERVER}", self.address).replace("${CAPTUREMOCK_PORT}", port)

    def getboolean(self, *args):
        return self._get(self.parser.getboolean, *args)

    def getfloat(self, *args):
        return self._get(self.parser.getfloat, *args)

    def getint(self, *args):
        return self._get(self.parser.getint, *args)

    def _get(self, getMethod, setting, sections, defaultVal=None):
        for section in sections:
            if self.parser.has_section(section) and self.parser.has_option(section, setting):
                return getMethod(section, setting)
        return defaultVal

    def getList(self, setting, sections):
        result = []
        for section in sections:
            if self.parser.has_section(section) and self.parser.has_option(section, setting):
                listStr = self.parser.get(section, setting).strip()
                if listStr:
                    result += listStr.split(",")
        return result
    
    def getSection(self, section):
        if self.parser.has_section(section):
            return dict(self.parser.items(section))
        else:
            return {}

    def addToList(self, setting, sections, newItem):
        values = self.getList(setting, sections)
        values.append(newItem)
        valueStr = ",".join(values)
        self.set(sections[0], setting, valueStr)
    
    def add_section(self, section):
        if self.parser.has_section(section):
            # raises exceptions by default, but we can easily get the same mapping several times
            return False
        self.parser.add_section(section)        
        return True

    def set(self, *args):
        return self.parser.set(*args)

    def setUpLogging(self, mainLogName):
        logConfigFile = self.get("log_config_file", [ "general" ],
                                 self.getPersonalPath("logging.conf"))
        if os.path.isfile(logConfigFile):
            local_dir = os.path.dirname(os.path.abspath(logConfigFile))
            if os.name == "nt": 
                # Gets passed through eval. Windows path separators get confused with escape character...
                local_dir = local_dir.replace("\\", "\\\\")
            defaults = { "LOCAL_DIR" : local_dir }
            logging.config.fileConfig(logConfigFile, defaults)
        self.diag = logging.getLogger(mainLogName)
        return self.diag


def isActive(mode, replayFile):
    return mode != REPLAY or (replayFile is not None and os.path.isfile(replayFile))
