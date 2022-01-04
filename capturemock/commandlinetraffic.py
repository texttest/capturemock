""" Traffic classes to do with captured command lines """

import os, logging, subprocess
import sys
from capturemock import traffic, fileedittraffic


class CommandLineTraffic(traffic.Traffic):
    typeId = "CMD"
    socketId = "SUT_COMMAND_LINE"
    direction = "<-"
    def __init__(self, inText, responseFile, rcHandler):
        self.diag = logging.getLogger("Server")
        cmdText, environText, cmdCwd, proxyPid = inText.split(":SUT_SEP:")
        argv = eval(cmdText)
        self.cmdEnviron = eval(environText)
        self.cmdCwd = cmdCwd
        self.proxyPid = proxyPid
        self.diag.debug("Received command with cwd = " + cmdCwd)
        self.fullCommand = argv[0].replace("\\", "/")
        self.commandName = os.path.basename(self.fullCommand)
        self.cmdArgs = [ self.commandName ] + argv[1:]
        self.asynchronousEdits = rcHandler.getboolean("asynchronous", self.getRcSections(), False)
        self.envVarsSet, envVarsUnset = self.filterEnvironment(self.cmdEnviron, rcHandler)
        cmdString = " ".join(map(self.quoteArg, self.cmdArgs))
        text = self.getEnvString(self.envVarsSet, envVarsUnset) + cmdString
        super(CommandLineTraffic, self).__init__(text, responseFile, rcHandler)

    def filterEnvironment(self, cmdEnviron, rcHandler):
        envVarsSet, envVarsUnset = [], []
        for var in self.getEnvironmentVariables(rcHandler):
            value = cmdEnviron.get(var)
            currValue = os.getenv(var)
            self.diag.debug("Checking environment " + var + "=" + repr(value) + " against " + repr(currValue))
            if value != currValue:
                if value is None:
                    envVarsUnset.append(var)
                else:
                    valueStr = self.getEnvValueString(var, value)
                    if valueStr is not None:
                        envVarsSet.append((var, valueStr))
        return envVarsSet, envVarsUnset

    def isMarkedForReplay(self, replayItems, *args):
        return self.commandName in replayItems

    def getRcSections(self):
        return [ self.commandName, "command line" ]

    def getEnvironmentVariables(self, rcHandler):
        return rcHandler.getList("environment", self.getRcSections())

    def hasChangedWorkingDirectory(self):
        return self.cmdCwd != os.getcwd()

    def quoteArg(self, arg):
        if " " in arg:
            return '"' + arg + '"'
        else:
            return arg

    def getEnvString(self, envVarsSet, envVarsUnset):
        recStr = ""
        if self.hasChangedWorkingDirectory():
            recStr += "cd " + self.cmdCwd.replace("\\", "/") + "; "
        if len(envVarsSet) == 0 and len(envVarsUnset) == 0:
            return recStr
        recStr += "env "
        for var in envVarsUnset:
            recStr += "--unset=" + var + " "
        for var, value in envVarsSet:
            recStr += "'" + var + "=" + value + "' "
        return recStr

    def getNewElements(self, value, oldVal):
        parts = value.split(os.pathsep)
        oldParts = oldVal.split(os.pathsep)
        newParts = set(parts).difference(set(oldParts))
        newPre, newPost = [], []
        midPoint = len(parts) / 2
        for part in newParts:
            if parts.index(part) < midPoint:
                newPre.append(part)
            else:
                newPost.append(part)
        return newPre, newPost
    
    def getEnvValueString(self, var, value):
        oldVal = os.getenv(var)
        if oldVal and oldVal != value:
            if "PATH" not in var:
                compactValue = value.replace(oldVal, "$" + var)
                self.diag.debug("Compacted value to " + repr(compactValue))
                return compactValue
        
            newPre, newPost = self.getNewElements(value, oldVal)
            if newPre or newPost:
                newValue = "$" + var
                if newPre:
                    newValue = ":".join(newPre) + ":" + newValue
                if newPost:
                    newValue += ":" + ":".join(newPost)
                return newValue
            else:
                self.diag.debug("Added text " + repr(value) + " already present, assuming not changed in essence")
            
            # Don't react if something is adding the same element to a path multiple times, for example
            # GTK+ on Windows adds a new copy of itself for every Python process started
        else:
            return value

    def findPossibleFileEdits(self):
        edits = []
        changedCwd = self.hasChangedWorkingDirectory()
        if changedCwd:
            edits.append(self.cmdCwd)
            self.diag.debug("Adding cwd " + repr(self.cmdCwd))
        for _, value in self.envVarsSet:
            for word in value.split():
                if os.pathsep not in word and os.path.isabs(word):
                    self.diag.debug("Adding environment path " + repr(word))
                    edits.append(word)
        for arg in self.cmdArgs[1:]:
            for word in self.getFileWordsFromArg(arg):
                if os.path.isabs(word):
                    self.diag.debug("Adding absolute path argument " + repr(word))
                    edits.append(word)
                elif not changedCwd:
                    fullPath = os.path.join(self.cmdCwd, word)
                    if os.path.exists(fullPath):
                        self.diag.debug("Adding relative path argument " + repr(word))
                        edits.append(fullPath)
        self.removeSubPaths(edits) # don't want to in effect mark the same file twice
        self.diag.debug("Might edit in " + repr(edits))
        return edits

    def makesAsynchronousEdits(self):
        return self.asynchronousEdits

    @staticmethod
    def removeSubPaths(paths):
        subPaths = []
        realPaths = [ os.path.realpath(path) for path in paths ]
        for index, path1 in enumerate(realPaths):
            for path2 in realPaths:
                if path1 != path2 and path1.startswith(path2) and path2 != os.path.expanduser("~"):
                    subPaths.append(paths[index])
                    break

        for path in subPaths:
            paths.remove(path)

    @staticmethod
    def getFileWordsFromArg(arg):
        if arg.startswith("-"):
            # look for something of the kind --logfile=/path
            return arg.split("=")[1:]
        else:
            # otherwise assume we could have multiple words in quotes
            return arg.split()

    def forwardToDestination(self):
        try:
            self.diag.debug("Running real command with args : " + repr(self.cmdArgs))
            proc = subprocess.Popen(self.cmdArgs, env=self.cmdEnviron, cwd=self.cmdCwd, 
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            CommandLineKillTraffic.pidMap[self.proxyPid] = proc
            output, errors = proc.communicate()
            response = self.makeResponse(output, errors, proc.returncode)
            del CommandLineKillTraffic.pidMap[self.proxyPid]
            return response
        except OSError:
            return self.makeResponse("", "ERROR: CaptureMock Server could not find command '" + self.commandName + "' in PATH\n", 1)

    def makeResponse(self, output, errors, exitCode):
        return [ StdoutTraffic(output, self.responseFile), StderrTraffic(errors, self.responseFile), \
                 SysExitTraffic(exitCode, self.responseFile) ]

    def filterReplay(self, trafficList):
        insertIndex = 0
        while len(trafficList) > insertIndex and isinstance(trafficList[insertIndex], fileedittraffic.FileEditTraffic):
            insertIndex += 1

        if len(trafficList) == insertIndex or not isinstance(trafficList[insertIndex], StdoutTraffic):
            trafficList.insert(insertIndex, StdoutTraffic("", self.responseFile))

        insertIndex += 1
        if len(trafficList) == insertIndex or not isinstance(trafficList[insertIndex], StderrTraffic):
            trafficList.insert(insertIndex, StderrTraffic("", self.responseFile))

        insertIndex += 1
        if len(trafficList) == insertIndex or not isinstance(trafficList[insertIndex], SysExitTraffic):
            trafficList.insert(insertIndex, SysExitTraffic("0", self.responseFile))

        return trafficList

class CommandLineResponseTraffic(traffic.ResponseTraffic):
    def __init__(self, text, *args):
        if text and not text.endswith("\n"):
            text += "\n"
        super(CommandLineResponseTraffic, self).__init__(text, *args)

    def forwardToDestination(self):
        self.write(self.text + "|TT_CMD_SEP|")
        return []


class StdoutTraffic(CommandLineResponseTraffic):
    typeId = "OUT"

class StderrTraffic(CommandLineResponseTraffic):
    typeId = "ERR"

class SysExitTraffic(traffic.ResponseTraffic):
    typeId = "EXC"
    def __init__(self, status, *args):
        traffic.ResponseTraffic.__init__(self, str(status), *args)
        self.exitStatus = int(status)
    def hasInfo(self):
        return self.exitStatus != 0


# Only works on UNIX
class CommandLineKillTraffic(traffic.Traffic):
    socketId = "SUT_COMMAND_KILL"
    pidMap = {}
    def __init__(self, inText, responseFile, *args):
        killStr, proxyPid = inText.split(":SUT_SEP:")
        self.killSignal = int(killStr)
        self.proc = self.pidMap.get(proxyPid)
        traffic.Traffic.__init__(self, killStr, responseFile, *args)

    def forwardToDestination(self):
        if self.proc:
            self.proc.send_signal(self.killSignal)
        return []

    def hasInfo(self):
        return False # We can get these during replay, but should ignore them

    def record(self, *args):
        pass # We replay these entirely from the return code, so that replay works on Windows

def getTrafficClasses(incoming):
    if incoming:
        return [ CommandLineTraffic, CommandLineKillTraffic ]
    else:
        return [ StderrTraffic, StdoutTraffic, SysExitTraffic ]
