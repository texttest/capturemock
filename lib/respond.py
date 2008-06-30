#!/usr/local/bin/python

import sys, os, plugins, subprocess
from jobprocess import killSubProcessAndChildren
from usecase import ScriptEngine

# Interface all responders must fulfil
class Responder:
    def __init__(self, *args):
        if ScriptEngine.instance:
            self.scriptEngine = ScriptEngine.instance
        else:
            self.setUpScriptEngine()
    def setUpScriptEngine(self):
        logger = plugins.getDiagnostics("Use-case log")
        self.scriptEngine = ScriptEngine(logger)
    def addSuites(self, suites):
        for suite in suites:
            self.addSuite(suite)
    # Full suite of tests, get notified of it at the start...
    def addSuite(self, suite):
        pass
    # Called when the state of the test "moves on" in its lifecycle
    def notifyLifecycleChange(self, test, state, changeDesc):
        pass
    # Called when no further actions will be performed on the test
    def notifyComplete(self, test):
        pass
    # Called when everything is finished
    def notifyAllComplete(self):
        pass
    def canBeMainThread(self):
        return True

class TextDisplayResponder(Responder):
    def notifyComplete(self, test):
        if test.state.hasFailed():
            self.describe(test)
    def describe(self, test):
        print test.getIndent() + repr(test), test.state.description()
            
class SaveState(Responder):
    def notifyComplete(self, test):
        if test.state.isComplete():
            self.performSave(test)
    def performSave(self, test):
        # overridden in subclasses
        test.saveState()
            
class InteractiveResponder(Responder):
    def __init__(self, optionMap, *args):
        Responder.__init__(self)
        self.overwriteSuccess = optionMap.has_key("n")
        self.overwriteFailure = optionMap.has_key("o")
        self.overwriteVersion = optionMap.get("o")
    def notifyComplete(self, test):
        if self.shouldSave(test):
            self.save(test, self.getOverwriteVersion(test))
        elif self.useInteractiveResponse(test):
            self.presentInteractiveDialog(test)
    def getOverwriteVersion(self, test):
        if self.overwriteVersion is None:
            return test.app.getFullVersion(forSave=1)
        else:
            return self.overwriteVersion
    def shouldSave(self, test):
        if not test.state.hasResults():
            return 0
        if self.overwriteSuccess and test.state.hasSucceeded():
            return 1
        return self.overwriteFailure and test.state.hasFailed()
    def save(self, test, version, exact=1):
        saveDesc = " "
        if version:
            saveDesc += "version " + version + " "
        if exact:
            saveDesc += "(exact) "
        if self.overwriteSuccess:
            saveDesc += "(overwriting succeeded files also)"
        self.describeSave(test, saveDesc)
        test.state.save(test, exact, version, self.overwriteSuccess)
        newState = test.state.makeNewState(test.app, "saved")
        test.changeState(newState)
    def describeSave(self, test, saveDesc):
        print test.getIndent() + "Saving " + repr(test) + saveDesc
    def describeViewOptions(self, test, options):
        print test.getIndent() + options
    def useInteractiveResponse(self, test):
        return test.state.hasFailed() and test.state.hasResults() and not self.overwriteFailure
    def presentInteractiveDialog(self, test):            
        performView = self.askUser(test, allowView=1)
        if performView:
            process = self.viewTest(test)
            self.askUser(test, allowView=0, process=process)
    def getViewCmdInfo(self, test, comparison):
        if comparison.missingResult():
            # Don't fire up GUI tools for missing results...
            return None, None
        if comparison.newResult():
            tool = test.getCompositeConfigValue("view_program", comparison.stem)
            cmdArgs = [ tool, comparison.tmpCmpFile ]
        else:
            tool = test.getCompositeConfigValue("diff_program", comparison.stem)
            cmdArgs = [ tool, comparison.stdCmpFile, comparison.tmpCmpFile ]
        return tool, cmdArgs        
    def viewTest(self, test):
        outputText = test.state.freeText
        sys.stdout.write(outputText)
        if not outputText.endswith("\n"):
            sys.stdout.write("\n")
        logFile = test.getConfigValue("log_file")
        logFileComparison, list = test.state.findComparison(logFile)
        if logFileComparison:
            tool, cmdArgs = self.getViewCmdInfo(test, logFileComparison)
            if tool:
                try:
                    proc = subprocess.Popen(cmdArgs, stdout=open(os.devnull, "w"),
                                            stderr=subprocess.STDOUT, startupinfo=plugins.getProcessStartUpInfo())
                    print "<See also " + tool + " window for details of " + logFile + ">"
                    return proc
                except OSError:
                    print "<No window created - could not find graphical difference tool '" + tool + "'>"
    def askUser(self, test, allowView, process=None):      
        versions = test.app.getSaveableVersions()
        options = ""
        for i in range(len(versions)):
            options += "Save Version " + versions[i] + "(" + str(i + 1) + "), "
        options += "Save(s) or continue(any other key)?"
        if allowView:
            options = "View details(v), " + options
        self.describeViewOptions(test, options)
        response = self.scriptEngine.readStdin()
        exactSave = response.find('+') != -1
        if response.startswith('s'):
            self.save(test, version="", exact=exactSave)
        elif allowView and response.startswith('v'):
            return 1
        else:
            for i in range(len(versions)):
                versionOption = str(i + 1)
                if response.startswith(versionOption):
                    self.save(test, versions[i], exactSave)
        if process:
            print "Terminating graphical viewer..."
            killSubProcessAndChildren(process)
        return 0
