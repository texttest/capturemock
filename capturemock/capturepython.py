
""" Generic front end module to all forms of Python interception"""

import sys, os, inspect, pythonclient

class CallStackChecker:
    def __init__(self, rcHandler):
        # Always ignore our own command-line capture module
        # TODO - ignore_callers list should be able to vary between different calls
        self.ignoreModuleCalls = set([ "capturecommand" ] + rcHandler.getList("ignore_callers", [ "python" ]))
        self.inCallStackChecker = False
        self.stdlibDir = os.path.dirname(os.path.realpath(os.__file__))
        
    def callerExcluded(self):
        if self.inCallStackChecker:
            # If we get called recursively, must call the real thing to avoid infinite loop...
            return True

        # Don't intercept if we've been called from within the standard library
        self.inCallStackChecker = True
        if os.name == "nt":
            # Recompute this: mostly a workaround for applications that reset os.sep on Windows
            self.stdlibDir = os.path.dirname(os.path.realpath(os.__file__))
        framerecord = inspect.stack()[2] # parent of parent. If you extract method you need to change this number :)
        fileName = framerecord[1]
        dirName = self.getDirectory(fileName)
        moduleName = self.getModuleName(fileName)
        moduleNames = set([ moduleName, os.path.basename(dirName) ])
        self.inCallStackChecker = False
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
    def __init__(self, moduleNames, callStackChecker):
        self.moduleNames = moduleNames
        self.interceptedNames = set()
        self.callStackChecker = callStackChecker
        self.handleImportedModules()

    def handleImportedModules(self):
        for modName in self.moduleNames:
            if modName in sys.modules:
                # Fix global imports in other modules
                oldModule = sys.modules.get(modName)
                for currModName in [ modName ] + self.findSubModules(modName, oldModule):
                    loadingMods = self.modulesLoading(currModName, modName)
                    if loadingMods:
                        newModule = pythonclient.ModuleProxy(currModName, self)
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
        if self.callStackChecker.callerExcluded():
            # return the real module, but don't put it in sys.modules so we trigger
            # a new import next time around
            return self.loadRealModule(name)
        else:
            self.interceptedNames.add(name)
            return sys.modules.setdefault(name, pythonclient.ModuleProxy(name, self))

    def loadRealModule(self, name):
        currentModule = sys.modules.get(name)
        if currentModule is not None:
            del sys.modules[name]
        sys.meta_path.remove(self)
        try:
            exec "import " + name + " as _realModule"
        finally:
            sys.meta_path.append(self)
            if currentModule is not None:
                sys.modules[name] = currentModule
            else:
                del sys.modules[name]
        return _realModule

    def reset(self):
        for modName in self.interceptedNames:
            if modName in sys.modules:
                del sys.modules[modName]
        sys.meta_path.remove(self)


def interceptPython(attributeNames, rcHandler):
    handler = InterceptHandler(attributeNames)
    handler.makeIntercepts(rcHandler)

def resetIntercepts():
    for item in sys.meta_path:
        if isinstance(item, ImportHandler):
            item.reset()

# Workaround for stuff where we can't do setattr
class TransparentProxy:
    def __init__(self, obj):
        self.obj = obj
        
    def __getattr__(self, name):
        return getattr(self.obj, name)


class InterceptHandler:
    def __init__(self, attributeNames):
        self.fullIntercepts = []
        self.partialIntercepts = {}
        for attrName in attributeNames:
            if "." in attrName:
                moduleName, subAttrName = self.splitByModule(attrName)
                if moduleName:
                    if subAttrName:
                        self.partialIntercepts.setdefault(moduleName, []).append(subAttrName)
                    else:
                        del sys.modules[attrName] # We imported the real version, get rid of it again...
                        self.fullIntercepts.append(attrName)
            else:
                self.fullIntercepts.append(attrName)

    def makeIntercepts(self, rcHandler):
        callStackChecker = CallStackChecker(rcHandler)
        if len(self.fullIntercepts):
            sys.meta_path.append(ImportHandler(self.fullIntercepts, callStackChecker))
        for moduleName, attributes in self.partialIntercepts.items():
            self.interceptAttributes(moduleName, attributes, callStackChecker)

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

    def handleBasicResponse(self, response):
        if response.startswith("raise "):
            exec response in sys.modules
        else:
            return eval(response, sys.modules)
    
    def interceptAttributes(self, moduleName, attrNames, callStackChecker):
        for attrName in attrNames:
            attrProxy = pythonclient.AttributeProxy(moduleName, self.handleBasicResponse, attrName, callStackChecker)
            self.interceptAttribute(attrProxy, sys.modules.get(moduleName), attrName)
            
    def interceptAttribute(self, proxyObj, realObj, attrName):
        parts = attrName.split(".", 1)
        currAttrName = parts[0]
        if not hasattr(realObj, currAttrName):
            return # If the real object doesn't have it, assume the fake one doesn't either...

        currRealAttr = getattr(realObj, currAttrName)
        if len(parts) == 1:
            proxyObj.realVersion = currRealAttr
            setattr(realObj, currAttrName, proxyObj.tryEvaluate())
        else:
            try:
                self.interceptAttribute(proxyObj, currRealAttr, parts[1])
            except TypeError: # it's a builtin (assume setattr threw), so we hack around...
                realAttrProxy = TransparentProxy(currRealAttr)
                self.interceptAttribute(proxyObj, realAttrProxy, parts[1])
                setattr(realObj, currAttrName, realAttrProxy)


