
""" Generic front end module to all forms of Python interception"""

import sys, os, config, logging, inspect, pythonclient

class CallStackChecker:
    def __init__(self, rcHandler):
        # Always ignore our own command-line capture module
        # TODO - ignore_callers list should be able to vary between different calls
        self.ignoreModuleCalls = set([ "capturecommand" ] + rcHandler.getList("ignore_callers", [ "python" ]))
        self.excludeLevel = 0
        self.logger = logging.getLogger("Call Stack Checker")
        self.stdlibDir = os.path.dirname(os.path.realpath(os.__file__))
        self.logger.debug("Found stdlib directory at " + self.stdlibDir)
        self.logger.debug("Ignoring calls from " + repr(self.ignoreModuleCalls))

    def callNoInterception(self, method, *args, **kw):
        self.excludeLevel += 1
        try:
            return method(*args, **kw)
        finally:
            self.excludeLevel -= 1
        
    def callerExcluded(self, stackDistance):
        if self.excludeLevel:
            # If we get called recursively, must call the real thing to avoid infinite loop...
            return True

        # Don't intercept if we've been called from within the standard library
        self.excludeLevel += 1
        if os.name == "nt":
            # Recompute this: mostly a workaround for applications that reset os.sep on Windows
            self.stdlibDir = os.path.dirname(os.path.realpath(os.__file__))

        framerecord = inspect.stack()[stackDistance]
        fileName = framerecord[1]
        dirName = self.getDirectory(fileName)
        moduleName = self.getModuleName(fileName)
        moduleNames = set([ moduleName, os.path.basename(dirName) ])
        self.logger.debug("Checking call from " + dirName + ", modules " + repr(moduleNames))
        self.excludeLevel -= 1
        return dirName == self.stdlibDir or len(moduleNames.intersection(self.ignoreModuleCalls)) > 0

    def getModuleName(self, fileName):
        given = inspect.getmodulename(fileName)
        if given == "__init__":
            return os.path.basename(os.path.dirname(fileName))
        else:
            return given

    def getDirectory(self, fileName):
        dirName, local = os.path.split(os.path.realpath(fileName))
        if local.startswith("__init__"):
            return self.getDirectory(dirName)
        else:
            return dirName

    def moduleExcluded(self, modName, mod):
        if not hasattr(mod, "__file__"):
            return False
        return os.path.realpath(mod.__file__).startswith(self.stdlibDir) or \
               modName.split(".")[0] in self.ignoreModuleCalls
               

class ImportHandler:
    def __init__(self, moduleNames, callStackChecker, trafficHandler):
        self.moduleNames = moduleNames
        self.interceptedNames = set()
        self.callStackChecker = callStackChecker
        self.trafficHandler = trafficHandler
        self.handleImportedModules()

    def handleImportedModules(self):
        for modName in self.moduleNames:
            if modName in sys.modules:
                # Fix global imports in other modules
                oldModule = sys.modules.get(modName)
                for currModName in [ modName ] + self.findSubModules(modName, oldModule):
                    loadingMods = self.modulesLoading(currModName, modName)
                    if loadingMods:
                        newModule = pythonclient.ModuleProxy(currModName, self.trafficHandler, sys.modules.get)
                        sys.modules[currModName] = newModule
                        for attrName, otherMod in loadingMods:
                            varName = otherMod.__name__ + "." + attrName
                            print "WARNING: having to reset the variable '" + varName + "'.\n" + \
                                  "This implies you are intercepting the module '" + modName + "'" + \
                                  " after importing it.\nWhile this might work, it may well be very slow and " + \
                                  "is not recommended.\n"
                            setattr(otherMod, attrName, newModule)
                    else:
                        del sys.modules[currModName]

    def findSubModules(self, modName, oldModule):
        subModNames = []
        for subModName, subMod in sys.modules.items():
            if subModName.startswith(modName + ".") and hasattr(subMod, "__file__") and \
                   subMod.__file__.startswith(os.path.dirname(oldModule.__file__)):
                subModNames.append(subModName)
        return subModNames

    def modulesLoading(self, modName, interceptModName):
        modules = []
        oldModule = sys.modules.get(modName)
        for otherName, otherMod in sys.modules.items():
            if not otherName.startswith("capturemock") and \
               not otherName.startswith(interceptModName + ".") and \
               not isinstance(otherMod, pythonclient.ModuleProxy) and \
               not self.callStackChecker.moduleExcluded(otherName, otherMod):
                modAttrName = self.findAttribute(otherMod, oldModule)
                if modAttrName:
                    modules.append((modAttrName, otherMod))
        return modules

    def findAttribute(self, module, attr):
        # Can't assume the attribute will have the same name of the module,
        # because of "import x as y" construct
        for currAttrName in dir(module):
            if getattr(module, currAttrName) is attr:
                return currAttrName

    def shouldIntercept(self, name):
        # Never try to intercept anything if it's blocked for some reason
        if self.callStackChecker.excludeLevel:
            return False
        if name in self.moduleNames:
            return True
        elif "." in name:
            for modName in self.moduleNames:
                if name.startswith(modName + "."):
                    return True
        return False
        
    def find_module(self, name, *args):
        if self.shouldIntercept(name):
            return self

    def load_module(self, name):
        if self.callStackChecker.callerExcluded(stackDistance=2):
            # return the real module, but don't put it in sys.modules so we trigger
            # a new import next time around
            return self.loadRealModule(name)
        else:
            self.interceptedNames.add(name)
            return sys.modules.setdefault(name, self.createProxy(name))

    def createProxy(self, name):
        return pythonclient.ModuleProxy(name, self.trafficHandler, self.loadRealModule)
    
    def loadRealModule(self, name):
        oldMod = None
        if name in sys.modules:
            oldMod = sys.modules.get(name)
            del sys.modules[name]
        sys.meta_path.remove(self)
        try:
            exec "import " + name + " as _realModule"
        finally:
            sys.meta_path.append(self)
            if name in sys.modules:
                if oldMod is not None:
                    sys.modules[name] = oldMod
                else:
                    del sys.modules[name]
        return _realModule

    def reset(self):
        for modName in self.interceptedNames:
            if modName in sys.modules:
                del sys.modules[modName]
        sys.meta_path.remove(self)

def interceptPython(*args, **kw):
    handler = InterceptHandler(*args, **kw)
    handler.makeIntercepts()
    return handler


# Workaround for stuff where we can't do setattr
class TransparentProxy:
    def __init__(self, obj):
        self.obj = obj
        
    def __getattr__(self, name):
        return getattr(self.obj, name)


class InterceptHandler:
    def __init__(self, mode, recordFile, replayFile, rcFiles, pythonAttrs):
        self.attributesIntercepted = []
        self.rcHandler = config.RcFileHandler(rcFiles)
        # Has too many side effects, because our log configuration file may conflict with the application's logging set up. Need to find another way.
        #self.rcHandler.setUpLogging()
        import replayinfo
        self.replayInfo = replayinfo.ReplayInfo(mode, replayFile, self.rcHandler)
        self.recordFile = recordFile
        self.allAttrNames = self.findAttributeNames(mode, pythonAttrs)

    def findAttributeNames(self, mode, pythonAttrs):
        rcAttrs = self.rcHandler.getIntercepts("python")
        if mode == config.REPLAY:
            return pythonAttrs + filter(lambda attr: attr in self.replayInfo.replayItems, rcAttrs)
        else:
            return pythonAttrs + rcAttrs

    def classifyIntercepts(self):
        fullIntercepts = []
        partialIntercepts = {}
        for attrName in self.allAttrNames:
            if "." in attrName:
                moduleName, subAttrName = self.splitByModule(attrName)
                if moduleName:
                    if subAttrName:
                        partialIntercepts.setdefault(moduleName, []).append(subAttrName)
                    else:
                        del sys.modules[attrName] # We imported the real version, get rid of it again...
                        fullIntercepts.append(attrName)
            else:
                fullIntercepts.append(attrName)
        return fullIntercepts, partialIntercepts

    def makeIntercepts(self):
        fullIntercepts, partialIntercepts = self.classifyIntercepts()
        if len(fullIntercepts) == 0 and len(partialIntercepts) == 0:
            # Don't construct PythonTrafficHandler, which will delete any existing files
            return
        callStackChecker = CallStackChecker(self.rcHandler)
        from pythontraffic import PythonTrafficHandler
        trafficHandler = PythonTrafficHandler(self.replayInfo, self.recordFile, self.rcHandler,
                                              callStackChecker, self.allAttrNames)
        if len(fullIntercepts):
            sys.meta_path = [ ImportHandler(fullIntercepts, callStackChecker, trafficHandler) ]
        for moduleName, attributes in partialIntercepts.items():
            self.interceptAttributes(moduleName, attributes, trafficHandler)

    def splitByModule(self, attrName):
        if self.canImport(attrName):
            return attrName, ""
        elif "." in attrName:
            parentName, localName = attrName.rsplit(".", 1)
            parentModule, parentAttr = self.splitByModule(parentName)
            if parentAttr:
                localName = parentAttr + "." + localName
            return parentModule, localName
        else:
            return "", "" # Cannot import any parent, so don't do anything

    def canImport(self, moduleName):
        try:
            exec "import " + moduleName
            return True
        except ImportError:
            return False

    def interceptAttributes(self, moduleName, attrNames, trafficHandler):
        for attrName in attrNames:
            self.interceptAttribute(moduleName, trafficHandler,
                                    sys.modules.get(moduleName), attrName)
            
    def interceptAttribute(self, proxyName, trafficHandler, realObj, attrName):
        parts = attrName.split(".", 1)
        currAttrName = parts[0]
        if not hasattr(realObj, currAttrName):
            return # If the real object doesn't have it, assume the fake one doesn't either...

        if len(parts) == 1:
            proxy = pythonclient.PythonProxy(proxyName, trafficHandler, realObj, sys.modules)
            self.performAttributeInterception(realObj, currAttrName, getattr(proxy, currAttrName))
        else:
            currRealAttr = getattr(realObj, currAttrName)
            newProxyName = proxyName + "." + parts[0]
            try:
                self.interceptAttribute(newProxyName, trafficHandler, currRealAttr, parts[1])
            except TypeError: # it's a builtin (assume setattr threw), so we hack around...
                realAttrProxy = TransparentProxy(currRealAttr)
                self.interceptAttribute(newProxyName, trafficHandler, realAttrProxy, parts[1])
                self.performAttributeInterception(realObj, currAttrName, realAttrProxy)

    def performAttributeInterception(self, realObj, attrName, proxy):
        origValue = getattr(realObj, attrName)
        setattr(realObj, attrName, proxy)
        self.attributesIntercepted.append((realObj, attrName, origValue))

    def resetIntercepts(self):
        for item in sys.meta_path:
            if isinstance(item, ImportHandler):
                item.reset()
        for realObj, attrName, origValue in self.attributesIntercepted:
            setattr(realObj, attrName, origValue)
