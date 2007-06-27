
import plugins, sys, time

class ActionRunner:
    def __init__(self, optionMap):
        self.previousTestRunner = None
        self.currentTestRunner = None
        self.script = optionMap.runScript()
        self.allTests = []
        self.testQueue = []
        self.appRunners = []
        self.diag = plugins.getDiagnostics("Action Runner")
    def addSuites(self, suites):
        for suite in suites:
            print "Using", suite.app.description(includeCheckout=True)
            self.addTestActions(suite)
    def addTestActions(self, testSuite):
        self.diag.info("Processing test suite of size " + str(testSuite.size()) + " for app " + testSuite.app.name)
        appRunner = ApplicationRunner(testSuite, self.script, self.diag)
        self.appRunners.append(appRunner)
        for test in testSuite.testCaseList():
            self.diag.info("Adding test runner for test " + test.getRelPath())
            testRunner = TestRunner(test, appRunner, self.diag)
            self.testQueue.append(testRunner)
            self.allTests.append(testRunner)
    def run(self):
        while len(self.testQueue):
            self.currentTestRunner = self.testQueue[0]
            self.diag.info("Running actions for test " + self.currentTestRunner.test.getRelPath())
            runToCompletion = len(self.testQueue) == 1
            completed = self.currentTestRunner.performActions(self.previousTestRunner, runToCompletion)
            self.testQueue.pop(0)
            if not completed:
                self.diag.info("Incomplete - putting to back of queue")
                self.testQueue.append(self.currentTestRunner)
            self.previousTestRunner = self.currentTestRunner
        self.diag.info("Finishing the action runner.")
        
class ApplicationRunner:
    def __init__(self, testSuite, script, diag):
        self.testSuite = testSuite
        self.suitesSetUp = {}
        self.suitesToSetUp = {}
        self.diag = diag
        self.actionSequence = self.getActionSequence(script)
        self.setUpApplications(self.actionSequence)
    def setUpApplications(self, sequence):
        self.testSuite.setUpEnvironment()
        for action in sequence:
            self.setUpApplicationFor(action)
        self.testSuite.tearDownEnvironment()
    def setUpApplicationFor(self, action):
        self.diag.info("Performing " + str(action) + " set up on " + repr(self.testSuite.app))
        try:
            action.setUpApplication(self.testSuite.app)
        except:
            sys.stderr.write("Exception thrown performing " + str(action) + " set up on " + repr(self.testSuite.app) + " :\n")
            plugins.printException()
    def markForSetUp(self, suite):
        newActions = []
        for action in self.actionSequence:
            newActions.append(action)
        self.suitesToSetUp[suite] = newActions
    def setUpSuites(self, action, test):
        if test.parent:
            self.setUpSuites(action, test.parent)
        if test.classId() == "test-suite":
            if action in self.suitesToSetUp[test]:
                self.setUpSuite(action, test)
                self.suitesToSetUp[test].remove(action)
    def setUpSuite(self, action, suite):
        self.diag.info(str(action) + " set up " + repr(suite))
        action.setUpSuite(suite)
        if self.suitesSetUp.has_key(suite):
            self.suitesSetUp[suite].append(action)
        else:
            self.suitesSetUp[suite] = [ action ]
    def tearDownSuite(self, suite):
        self.diag.info("Try tear down " + repr(suite))
        actionsToTearDown = self.suitesSetUp.get(suite, [])
        for action in actionsToTearDown:
            self.diag.info(str(action) + " tear down " + repr(suite))
            action.tearDownSuite(suite)
        suite.tearDownEnvironment()
        self.suitesSetUp[suite] = []
    
    def getActionSequence(self, script):
        if not script:
            return self.testSuite.app.getActionSequence()
            
        actionCom = script.split(" ")[0]
        actionArgs = script.split(" ")[1:]
        actionOption = actionCom.split(".")
        if len(actionOption) != 2:
            sys.stderr.write("Plugin scripts must be of the form <module_name>.<script>\n")
            return []

                
        module, pclass = actionOption
        importCommand = "from " + module + " import " + pclass + " as _pclass"
        try:
            exec importCommand
        except:
            sys.stderr.write("Could not import script " + pclass + " from module " + module + "\n" +\
                             "Import failed, looked at " + repr(sys.path) + "\n")
            plugins.printException()
            return []

        # Assume if we succeed in importing then a python module is intended.
        try:
            if len(actionArgs) > 0:
                return [ _pclass(actionArgs) ]
            else:
                return [ _pclass() ]
        except:
            sys.stderr.write("Could not instantiate script action " + repr(actionCom) +\
                             " with arguments " + repr(actionArgs) + "\n")
            plugins.printException()
            return []

class TestRunner:
    def __init__(self, test, appRunner, diag):
        self.test = test
        self.diag = diag
        self.actionSequence = []
        self.appRunner = appRunner
        self.setActionSequence(appRunner.actionSequence)
    def setActionSequence(self, actionSequence):
        self.actionSequence = []
        # Copy the action sequence, so we can edit it and mark progress
        for action in actionSequence:
            self.actionSequence.append(action)
    def handleExceptions(self, method, *args):
        try:
            return method(*args)
        except plugins.TextTestError, e:
            self.failTest(str(sys.exc_value))
        except:
            plugins.printWarning("Caught exception while running " + repr(self.test) + " changing state to UNRUNNABLE :")
            exceptionText = plugins.printException()
            self.failTest(exceptionText)
    def failTest(self, excString):
        execHosts = self.test.state.executionHosts
        failState = plugins.Unrunnable(freeText=excString, executionHosts=execHosts)
        self.test.changeState(failState)
    def performActions(self, previousTestRunner, runToCompletion):
        tearDownSuites, setUpSuites = self.findSuitesToChange(previousTestRunner)
        for suite in tearDownSuites:
            self.handleExceptions(previousTestRunner.appRunner.tearDownSuite, suite)
        for suite in setUpSuites:
            suite.setUpEnvironment()
            self.appRunner.markForSetUp(suite)
        abandon = self.test.state.shouldAbandon()
        while len(self.actionSequence):
            action = self.actionSequence[0]
            if abandon and not action.callDuringAbandon(self.test):
                self.actionSequence.pop(0)
                continue
            self.diag.info("->Performing action " + str(action) + " on " + repr(self.test))
            self.handleExceptions(self.appRunner.setUpSuites, action, self.test)
            completed, tryOthersNow = self.performAction(action, runToCompletion)
            self.diag.info("<-End Performing action " + str(action) + self.returnString(completed, tryOthersNow))
            if completed:
                self.actionSequence.pop(0)
                if not abandon and self.test.state.shouldAbandon():
                    self.diag.info("Abandoning test...")
                    abandon = True
            if tryOthersNow:
                return 0
        self.test.actionsCompleted()
        return 1
    def returnString(self, completed, tryOthersNow):
        retString = " - "
        if completed:
            retString += "COMPLETE"
        else:
            retString += "RETRY"
        if tryOthersNow:
            retString += ", CHANGE TEST"
        else:
            retString += ", CONTINUE"
        return retString
    def performAction(self, action, runToCompletion):
        while 1:
            retValue = self.callAction(action)
            if not retValue:
                # No return value: we've finished and should proceed
                return 1, 0

            completed = not retValue & plugins.Action.RETRY
            tryOthers = retValue & plugins.Action.WAIT and not runToCompletion
            # Don't busy-wait : assume lack of completion is a sign we might keep doing this
            if not completed:
                time.sleep(0.1)
            if completed or tryOthers:
                # Don't attempt to retry the action, mark complete
                return completed, tryOthers 
    def callAction(self, action):
        self.test.setUpEnvironment()
        retValue = self.handleExceptions(self.test.callAction, action)
        self.test.tearDownEnvironment()
        return retValue
    def findSuitesToChange(self, previousTestRunner):
        tearDownSuites = []
        commonAncestor = None
        if previousTestRunner:
            commonAncestor = self.findCommonAncestor(self.test, previousTestRunner.test)
            self.diag.info("Common ancestor : " + repr(commonAncestor))
            tearDownSuites = previousTestRunner.findSuitesUpTo(commonAncestor)
        setUpSuites = self.findSuitesUpTo(commonAncestor)
        # We want to set up the earlier ones first
        setUpSuites.reverse()
        return tearDownSuites, setUpSuites
    def findCommonAncestor(self, test1, test2):
        if self.hasAncestor(test1, test2):
            self.diag.info(test1.getRelPath() + " has ancestor " + test2.getRelPath())
            return test2
        if self.hasAncestor(test2, test1):
            self.diag.info(test2.getRelPath() + " has ancestor " + test1.getRelPath())
            return test1
        if test1.parent:
            return self.findCommonAncestor(test1.parent, test2)
        else:
            self.diag.info(test1.getRelPath() + " unrelated to " + test2.getRelPath())
            return None
    def hasAncestor(self, test1, test2):
        if test1 == test2:
            return 1
        if test1.parent:
            return self.hasAncestor(test1.parent, test2)
        else:
            return 0
    def findSuitesUpTo(self, ancestor):
        suites = []
        currCheck = self.test.parent
        while currCheck != ancestor:
            suites.append(currCheck)
            currCheck = currCheck.parent
        return suites
