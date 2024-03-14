
""" Traffic classes for all kinds of Python calls """

import sys, types, inspect, re
from pprint import pformat
from threading import RLock
from . import traffic
from .recordfilehandler import RecordFileHandler
from .config import CaptureMockReplayError

class PythonWrapper(object):
    def __init__(self, name, target):
        self.name = name
        self.target = target
        self.allInstances[self.name] = self
        self.wrappersByInstance[self.getId(self.target)] = self
        self.doneFullRepr = False
        
    def addNumericPostfix(self, stem):
        num = 1
        while stem + str(num) in self.allInstances:
            num += 1
        return stem + str(num)

    @classmethod
    def getId(cls, target):
        return id(target)

    @classmethod
    def getWrapperFor(cls, instance, *args):
        storedWrapper = cls.wrappersByInstance.get(cls.getId(instance))
        return storedWrapper or cls(instance, *args)
    
    @classmethod
    def hasWrapper(cls, instance):
        return cls.getId(instance) in cls.wrappersByInstance
    
    @classmethod
    def resetCaches(cls):
        cls.allInstances = {}
        cls.wrappersByInstance = {}
        
    def __repr__(self):
        if self.doneFullRepr:
            return self.name
        
        self.doneFullRepr = True
        return self.getFullRepr()
    

class PythonInstanceWrapper(PythonWrapper):
    allInstances = {}
    wrappersByInstance = {}
    classDescriptions = {}
    def __init__(self, instance, classDesc, namingHint=None):
        self.classDesc = classDesc
        if classDesc not in self.classDescriptions:
            self.classDescriptions[classDesc.split("(")[0]] = classDesc
        name = self.getNewInstanceName(namingHint)
        super(PythonInstanceWrapper, self).__init__(name, instance)
            
    @classmethod
    def resetCaches(cls):
        super(PythonInstanceWrapper, cls).resetCaches()
        cls.classDescriptions = {}
        
    @classmethod
    def renameInstance(cls, instanceName, namingHint):
        if instanceName in cls.allInstances:
            return cls.allInstances[instanceName].rename(namingHint)  

    def shouldRename(self):
        className = self.getClassName()
        return self.name.replace(className, "").isdigit()
    
    def rename(self, namingHint):
        if self.shouldRename(): 
            del self.allInstances[self.name]
            self.name = self.getNewInstanceName(namingHint)
            self.allInstances[self.name] = self
            return self.name

    def getFullRepr(self):
        return "Instance(" + repr(self.classDesc) + ", " + repr(self.name) + ")"

    def getClassName(self):
        return self.classDesc.split("(")[0].lower()

    def getNewInstanceName(self, namingHint):
        className = self.getClassName()
        if namingHint:
            className += "_" + namingHint
            if className not in self.allInstances:
                return className
        
        return self.addNumericPostfix(className)
                
    def createProxy(self, proxy):
        return proxy.captureMockCreateInstanceProxy(self.name, self.target, self.classDesc)
    
def isBuiltin(cls):
    return cls.__module__ in [ "__builtin__", "builtins" ]
    
def getFullClassName(cls):
    classStr = cls.__name__
    if not isBuiltin(cls):
        classStr = cls.__module__  + "." + classStr
    return classStr
    
class PythonCallbackWrapper(PythonWrapper):
    allInstances = {}
    wrappersByInstance = {}
    def __init__(self, function, proxy, name):
        if name in self.allInstances:
            name = self.addNumericPostfix(name)
        target = function.captureMockTarget if hasattr(function, "captureMockTarget") else function
        super(PythonCallbackWrapper, self).__init__(name, target)
        self.proxy = proxy.captureMockCreateInstanceProxy(self.name, self.target, captureMockCallback=True)
                
    def hasExternalName(self):
        return isBuiltin(self.target) or "." in self.name

    def getFullRepr(self):
        return self.name if self.hasExternalName() else "Callback('" + self.name + "')"

    @classmethod
    def getId(cls, target):
        return id(target.__func__) if not hasattr(target, "captureMockTarget") and hasattr(target, "__func__") else id(target)
    
    def createProxy(self, proxy):
        if isBuiltin(self.target):
            return self.target
        else:  
            return self.proxy


class PythonTraffic(traffic.BaseTraffic):
    typeId = "PYT"
    direction = "<-"
    def getAlterationSectionNames(self):
        return [ self.getTextMarker(), "python", "general" ]

    def getTextMarker(self):
        return self.text
        
    def getExceptionResponse(self, exc_info, inCallback):
        exc_value = exc_info[1]
        return PythonResponseTraffic(self.getExceptionText(exc_value), inCallback=inCallback)

    def getExceptionText(self, exc_value):
        text = "raise " + getFullClassName(exc_value.__class__) + "(" + repr(str(exc_value)) + ")"
        return self.applyAlterations(text)


class PythonImportTraffic(PythonTraffic):
    def __init__(self, inText, *args):
        self.moduleName = inText
        text = "import " + self.moduleName
        super(PythonImportTraffic, self).__init__(text, *args)

    def isMarkedForReplay(self, replayItems, responses):
        return self.getDescription() in responses


class ReprObject:
    def __init__(self, arg):
        self.arg = arg
        
    def __repr__(self):
        return self.arg
    
class DictForRepr(object):
    def __init__(self, thedict):
        self.thedict = thedict

    def __getattr__(self, name):  
        return getattr(self.thedict, name)
    
    def __getitem__(self, *args):
        return self.thedict.__getitem__(*args)

    def __setitem__(self, *args):
        return self.thedict.__setitem__(*args)
    
    def __repr__(self):
        return pformat(self.thedict, width=130)

def extendDirection(direction):
    extra = "----"
    return direction + extra if direction.startswith("<") else extra + direction
                  
class PythonModuleTraffic(PythonTraffic):
    def __init__(self, text, rcHandler, interceptModules, inCallback):
        super(PythonModuleTraffic, self).__init__(text, rcHandler)
        self.interceptModules = interceptModules
        if inCallback:
            self.direction = extendDirection(self.direction)
        
    def getModuleName(self, obj):
        if hasattr(obj, "__module__"): # classes, functions, many instances
            return obj.__module__
        else:
            return self.getClass(obj).__module__ # many other instances
            
    def getClass(self, obj):
        return obj.__class__ if hasattr(obj, "__class__") else type(obj)
            
    def insertReprObjects(self, arg):
        if hasattr(arg, "captureMockProxyName"):
            return ReprObject(arg.captureMockProxyName)
        elif isinstance(arg, float):
            # Stick to 2 dp for recording floating point values
            return ReprObject(str(round(arg, 2)))
        elif isinstance(arg, dict):
            return DictForRepr(arg)
        else:
            return ReprObject(self.fixMultilineStrings(arg))

    def isCallableType(self, obj):        
        cacheTypes = (types.FunctionType, types.GeneratorType, types.MethodType, types.BuiltinFunctionType,
                      type, types.ModuleType)
        if sys.version_info[0] == 2:
            cacheTypes += (types.ClassType,)
        return type(obj) in cacheTypes or hasattr(obj, "__call__")


    def isMarkedForReplay(self, replayItems, responses):
        fullTextMarker = self.direction + self.typeId + ":" + self.getTextMarker()
        return any((item == fullTextMarker or item.startswith(fullTextMarker + "(") for item in responses))

    def getIntercept(self, modOrAttr):
        if modOrAttr in self.interceptModules:
            return modOrAttr
        elif "." in modOrAttr:
            return self.getIntercept(modOrAttr.rsplit(".", 1)[0])

    def isBasicType(self, obj):
        basicTypes = (bool, float, int, str, list, dict, tuple)
        if sys.version_info[0] == 2:
            basicTypes += (long, unicode)
        return obj is None or obj is NotImplemented or type(obj) in basicTypes
    
    def isIterator(self, obj):
        return hasattr(obj, "__iter__") and (hasattr(obj, "next") or hasattr(obj, "__next__"))

    def getResultText(self, result):
        text = repr(self.transformStructure(result, self.insertReprObjects))
        return self.applyAlterations(text)

    def transformResponse(self, response, proxy):
        wrappedValue = self.transformStructure(response, self.addInstanceWrapper, responseIsBasic=self.isBasicType(response))
        responseText = self.getResultText(wrappedValue)
        transformedResponse = self.transformStructure(wrappedValue, self.insertProxy, proxy)
        return responseText, transformedResponse

    def transformStructure(self, result, transformMethod, *args, **kw):
        if type(result) in (list, tuple):
            return type(result)([ self.transformStructure(elem, transformMethod, *args, **kw) for elem in result ])
        elif type(result) == dict:
            newResult = {}
            for key, value in result.items():
                newResult[key] = self.transformStructure(value, transformMethod, *args, **kw)
            return transformMethod(newResult, *args, **kw)
        else:
            return transformMethod(result, *args, **kw)
        
    def addInstanceWrapper(self, result, **kw):
        # We add wrappers if we aren't already a proxy, if we're a complex type, and if we're either being intercepted, or an iterator
        # Iterators returned from proxy operations do not follow repr-eval, so we need to intercept them too
        if not hasattr(result, "captureMockProxyName") and not self.isBasicType(result) and \
            (self.getIntercept(self.getModuleName(result)) or self.isIterator(result)):
            return self.getWrapper(result, **kw)
        else:
            return result

    def insertProxy(self, result, proxy):
        if isinstance(result, PythonInstanceWrapper):
            return result.createProxy(proxy)
        else:
            return result

    def instanceHasAttribute(self, instance, attr):
        # hasattr fails if the intercepted instance defines __getattr__, when it always returns True
        # dir() can throw exceptions if __dir__ does (instance can be anything at all, and its __dir__ might raise anything at all)
        try:
            return attr in dir(instance)
        except:
            return False

    def getWrapper(self, instance, namingHint=None, **kw):
        # hasattr fails if the intercepted instance defines __getattr__, when it always returns True
        if self.instanceHasAttribute(instance, "captureMockTarget"):
            return self.getWrapper(instance.captureMockTarget, namingHint=namingHint)
        classDesc = self.getClassDescription(self.getClass(instance))
        return PythonInstanceWrapper.getWrapperFor(instance, classDesc, namingHint)

    def getClassDescription(self, cls):
        if cls.__name__ in PythonInstanceWrapper.classDescriptions:
            return cls.__name__
        
        baseClasses = self.findRelevantBaseClasses(cls)
        if len(baseClasses):
            return cls.__name__ + "(" + ", ".join(baseClasses) + ")"
        else:
            return cls.__name__

    def findRelevantBaseClasses(self, cls):
        classes = []
        for baseClass in inspect.getmro(cls)[1:]:
            name = baseClass.__name__
            if self.getIntercept(baseClass.__module__):
                classes.append(name)
            else:
                name = getFullClassName(baseClass)
                # No point recording 'object' in Python3: everything is an object there
                if name != "object" or sys.version_info[0] == 2:
                    classes.append(name)
                break
        return classes


class PythonAttributeTraffic(PythonModuleTraffic):
    cachedAttributes = {}
    cachedInstances = {}
    @classmethod
    def resetCaches(cls):
        cls.cachedAttributes = {}
        cls.cachedInstances = {}
            
    def shouldUpdateCache(self, obj):
        if self.isBasicType(obj):
            if self.text in self.cachedAttributes:
                cachedObj = self.cachedAttributes.get(self.text)
                if cachedObj == obj:
                    return False
            self.cachedAttributes[self.text] = obj
            return True
        else:
            return self.text not in self.cachedInstances
        
    def shouldCache(self, obj):
        return not self.isCallableType(obj)

    def getWrapper(self, instance, namingHint=None, responseIsBasic=True):
        wrapper = PythonModuleTraffic.getWrapper(self, instance, namingHint=namingHint)
        return wrapper if responseIsBasic else self.cachedInstances.setdefault(self.text, wrapper)

        
class PythonSetAttributeTraffic(PythonModuleTraffic):
    def __init__(self, rcHandler, interceptModules, inCallback, proxyName, attrName, value):
        text = proxyName + "." + attrName + " = " + repr(self.insertReprObjects(value))
        super(PythonSetAttributeTraffic, self).__init__(text, rcHandler, interceptModules, inCallback)


class PythonFunctionCallTraffic(PythonModuleTraffic):
    cachedFunctions = set()
    def __init__(self, functionName, rcHandler, interceptModules, proxy, inCallback, *args, **kw):
        self.interceptModules = interceptModules
        if proxy.captureMockCallback:
            self.direction = "--->"
        self.functionName = functionName
        self.args = args
        self.kw = kw # Prevent naming hints being added when transforming arguments
        self.args = self.transformStructure(args, self.transformArg, proxy)
        self.kw = self.transformStructure(kw, self.transformArg, proxy)
        
        argsForRecord = self.transformStructure(list(self.args), self.insertReprObjects)
        keywForRecord = self.transformStructure(self.kw, self.insertReprObjects)
        for key in sorted(keywForRecord.keys()):
            value = keywForRecord[key]
            recordArg = ReprObject(key + "=" + repr(value))
            argsForRecord.append(recordArg)
        text = functionName + "(" + repr(argsForRecord)[1:-1] + ")"
        super(PythonFunctionCallTraffic, self).__init__(text, rcHandler, interceptModules, inCallback)
        self.text = self.applyAlterations(self.text)
        checkRepeats = rcHandler.getboolean("check_repeated_calls", [ self.getIntercept(self.functionName), "python" ], True)
        if checkRepeats:
            self.shouldRecord = True
        else:
            self.shouldRecord = self.functionName not in self.cachedFunctions
            self.cachedFunctions.add(self.functionName)
            
    def getTextMarker(self):
        return self.functionName
    
    def transformArg(self, arg, proxy):
        if proxy.captureMockCallback:
            return self.addInstanceWrapper(arg)
        elif self.isRealArgCallable(arg) and not self.getIntercept(self.getModuleName(arg)):
            return PythonCallbackWrapper.getWrapperFor(arg, proxy, self.getCallbackName(arg))
        else:
            return arg
        
    def getCallbackName(self, arg):
        if hasattr(arg, "captureMockTarget"):
            return arg.captureMockProxyName
        elif isBuiltin(arg):
            return arg.__name__
        
        callbackName = self.functionName.split(".")[0]
        hint = self.getNamingHint()
        if hint:
            callbackName += "_" + hint
        callbackName += "_callback"
        return callbackName
    
    def isRealArgCallable(self, arg):
        if hasattr(arg, "captureMockTarget"):
            return self.isCallableType(arg.captureMockTarget)
        else:
            return self.isCallableType(arg)
            
    def switchProxies(self, arg, captureMockProxy):
        # we need our proxies present in system calls, and absent in real calls to intercepted code
        # So we remove them from normal function calls, and add them in callbacks
        if "<" in self.direction:
            if hasattr(arg, "captureMockTarget"):
                if not arg.captureMockCallback:
                    return arg.captureMockTarget
            elif isinstance(arg, PythonCallbackWrapper):
                return arg.createProxy(captureMockProxy)
        else:
            if isinstance(arg, PythonInstanceWrapper):
                return self.insertProxy(arg, captureMockProxy)
        return arg 
            
    # Naming to avoid clashes as args and kw come from the application
    def callRealFunction(self, captureMockFunction, captureMockRecordHandler, captureMockProxy):
        realArgs = self.transformStructure(self.args, self.switchProxies, captureMockProxy)
        realKw = self.transformStructure(self.kw, self.switchProxies, captureMockProxy)
        try:
            return captureMockFunction(*realArgs, **realKw)
        except:
            exc_value = sys.exc_info()[1]
            moduleName = self.getModuleName(exc_value)
            if self.getIntercept(moduleName):
                # We own the exception object also, handle it like an ordinary instance
                wrapper = self.getWrapper(exc_value)
                responseText = "raise " + repr(wrapper)
                PythonResponseTraffic(responseText).record(captureMockRecordHandler)
                raise self.insertProxy(wrapper, captureMockProxy)
            else:
                responseText = self.getExceptionText(exc_value)
                PythonResponseTraffic(responseText).record(captureMockRecordHandler)
                raise
                        
    def tryRenameInstance(self, proxy, recordHandler):
        if self.functionName.count(".") == 1:
            objName, localName = self.functionName.split(".")
            if localName.startswith("set") or localName.startswith("Set"):
                hint = self.getNamingHint()
                if hint:
                    newName = PythonInstanceWrapper.renameInstance(objName, hint)
                    if newName is not None:
                        proxy.captureMockNameFinder.rename(objName, newName)
                        recordHandler.rerecord(objName, newName)
                    
    def makePythonName(self, arg):
        # Swiped from http://stackoverflow.com/questions/3303312/how-do-i-convert-a-string-to-a-valid-variable-name-in-python
        return re.sub(r'\W|^(?=\d)','_', arg.strip().lower())

    def getNamingHint(self):
        def isSuitable(arg):
            return isinstance(arg, str) and "\n" not in arg and len(arg) < 20 # Don't use long arguments
            
        for arg in self.args:
            if isSuitable(arg):
                return self.makePythonName(arg)
            
        for _, arg in sorted(self.kw.items()):
            if isSuitable(arg):
                return self.makePythonName(arg)
    
    def getWrapper(self, instance, **kw):
        return PythonModuleTraffic.getWrapper(self, instance, namingHint=self.getNamingHint())
                

class PythonResponseTraffic(traffic.BaseTraffic):
    typeId = "RET"
    direction = "->"
    def __init__(self, text, rcHandler=None, callback=False, inCallback=False):
        if callback:
            self.direction = "<---"
        elif inCallback:
            self.direction = extendDirection(self.direction)
        super(PythonResponseTraffic, self).__init__(text, rcHandler)

class PythonTrafficHandler:
    def __init__(self, replayInfo, recordFile, rcHandler, callStackChecker, interceptModules):
        self.replayInfo = replayInfo
        self.recordFileHandler = RecordFileHandler(recordFile)
        self.callStackChecker = callStackChecker
        self.rcHandler = rcHandler
        self.interceptModules = interceptModules
        self.lock = RLock()
        PythonInstanceWrapper.resetCaches() # reset, in case of previous tests
        PythonCallbackWrapper.resetCaches()
        PythonAttributeTraffic.resetCaches()

    def importModule(self, name, proxy, loadModule):
        with self.lock:
            traffic = PythonImportTraffic(name, self.rcHandler)
            if self.callStackChecker.callerExcluded(stackDistance=3):
                return loadModule(name)
            
            self.record(traffic)
            if self.replayInfo.isActiveFor(traffic):
                return self.processReplay(traffic, proxy)
            else:
                try:
                    return self.callStackChecker.callNoInterception(False, loadModule, name)
                except:
                    response = traffic.getExceptionResponse(sys.exc_info(), self.callStackChecker.inCallback)
                    self.record(response)
                    raise
            
    def record(self, traffic):
        traffic.record(self.recordFileHandler, truncationPoint="Instance('" in traffic.text)

    def evaluateForReplay(self, traffic, proxy):
        if isinstance(traffic, PythonResponseTraffic) or not "." in traffic.text:
            return proxy.captureMockEvaluate(traffic.text)
        else:
            return self.processReplay(traffic, proxy, callback=True)

    def processReplay(self, traffic, proxy, record=True, **kw):
        lastResponse = None
        for responseClass, responseText in self.getReplayResponses(traffic):
            if responseClass is PythonResponseTraffic:
                responseTraffic = responseClass(responseText, inCallback=self.callStackChecker.inCallback, **kw)
            else:
                responseTraffic = responseClass(responseText)
                responseTraffic.direction = "--->"
            if record:
                self.record(responseTraffic)
            lastResponse = self.evaluateForReplay(responseTraffic, proxy)
        return lastResponse

    def getReplayResponses(self, traffic, **kw):
        return self.replayInfo.readReplayResponses(traffic, [ PythonTraffic, PythonResponseTraffic ], **kw)

    def getRealAttribute(self, target, attrName):
        if attrName == "__all__":
            # Need to provide something here, the application has probably called 'from module import *'
            return [x for x in dir(target) if not x.startswith("__")]
        else:
            return getattr(target, attrName)

    def getAttribute(self, proxyName, attrName, proxy, proxyTarget):
        with self.lock:
            fullAttrName = proxyName + "." + attrName
            if self.callStackChecker.callerExcluded(stackDistance=3):
                if proxyTarget is None:
                    proxyTarget = proxy.captureMockLoadRealModule()
                return self.getRealAttribute(proxyTarget, attrName)
            else:
                traffic = PythonAttributeTraffic(fullAttrName, self.rcHandler, self.interceptModules, self.callStackChecker.inCallback)
                if self.replayInfo.isActiveFor(traffic):
                    return self.getAttributeFromReplay(traffic, proxyTarget, attrName, proxy, fullAttrName)
                else:
                    return self.getAndRecordRealAttribute(traffic, proxyTarget, attrName, proxy, fullAttrName)

    def getAttributeFromReplay(self, traffic, proxyTarget, attrName, proxy, fullAttrName):
        responses = self.getReplayResponses(traffic, exact=True)
        if len(responses):
            firstText = responses[0][1]
            if traffic.shouldUpdateCache(firstText):
                self.record(traffic)
                self.recordResponse(firstText)
            return proxy.captureMockEvaluate(firstText)
        else:
            newTarget = getattr(proxyTarget, attrName) if proxyTarget else None
            if newTarget is None:
                classDesc = self.getReplayClassDefinition(fullAttrName)
                if classDesc is not None:
                    return proxy.captureMockCreateClassProxy(fullAttrName, newTarget, classDesc)
            return self.createInstanceProxy(proxy, fullAttrName, newTarget)

    def getReplayClassDefinition(self, fullAttrName):
        response = self.replayInfo.findResponseToTrafficStartingWith(fullAttrName + "(")
        if response is not None and response.startswith("Instance"):
            def Instance(classDesc, instanceName):
                return classDesc
            return eval(response)
        
    def getReplayInstanceName(self, text, proxy):
        def Instance(classDesc, instanceName):
            return instanceName
        if text.startswith("raise "):
            proxy.captureMockEvaluate(text) # raise the exception
        else:
            return eval(text)

    def getAndRecordRealAttribute(self, traffic, proxyTarget, attrName, proxy, fullAttrName):
        try:
            realAttr = self.callStackChecker.callNoInterception(False, self.getRealAttribute, proxyTarget, attrName)
        except:
            responseTraffic = traffic.getExceptionResponse(sys.exc_info(), self.callStackChecker.inCallback)
            if traffic.shouldUpdateCache(responseTraffic.text):
                self.record(traffic)
                self.record(responseTraffic)
            raise
                
        if traffic.shouldCache(realAttr):
            if traffic.shouldUpdateCache(realAttr):
                self.record(traffic)
                if attrName == "__path__":
                    # don't want to record the real absolute path, which is just hard to filter
                    # and won't work for lookup anyway
                    self.recordResponse("['Fake value just to mark that it exists']")
                    return realAttr
                else:
                    return self.transformResponse(traffic, realAttr, proxy)
            else:
                return traffic.transformResponse(realAttr, proxy)[1]
        else:
            if issubclass(type(realAttr), type) or (sys.version_info[0] == 2 and type(realAttr) is types.ClassType):
                classDesc = traffic.getClassDescription(realAttr)
                return proxy.captureMockCreateClassProxy(fullAttrName, realAttr, classDesc)
            else:
                return self.createInstanceProxy(proxy, fullAttrName, realAttr)

    def createInstanceProxy(self, proxy, fullAttrName, realAttr):
        if isinstance(proxy, type):
            return proxy.__class__.captureMockCreateInstanceProxy(proxy, fullAttrName, realAttr)
        else:
            return proxy.captureMockCreateInstanceProxy(fullAttrName, realAttr)
        
    def recordResponse(self, responseText, callback=False):
        if responseText != "None":
            self.record(PythonResponseTraffic(responseText, callback=callback, inCallback=self.callStackChecker.inCallback))

    def transformResponse(self, traffic, response, proxy):
        responseText, transformedResponse = traffic.transformResponse(response, proxy)
        self.recordResponse(responseText, proxy.captureMockCallback)
        return transformedResponse
    
    # Parameter names chosen to avoid potential clashes with args and kw which come from the app
    def callFunction(self, captureMockProxyName, captureMockProxy, captureMockFunction, *args, **kw):
        with self.lock:
            isCallback = captureMockProxy.captureMockCallback
            if not self.callStackChecker.callerExcluded(stackDistance=3, callback=isCallback):
                traffic = PythonFunctionCallTraffic(captureMockProxyName, self.rcHandler,
                                                    self.interceptModules, captureMockProxy, 
                                                    self.callStackChecker.inCallback, *args, **kw)
                replayActive = self.replayInfo.isActiveFor(traffic)
                if traffic.shouldRecord and (not isCallback or not replayActive):
                    self.record(traffic)
                if not isCallback and replayActive:
                    return self.processReplay(traffic, captureMockProxy, traffic.shouldRecord)
                else:
                    traffic.tryRenameInstance(captureMockProxy, self.recordFileHandler)
                    return self.callRealFunction(traffic, captureMockFunction, captureMockProxy)
        # Excluded. Important not to hold the lock while this goes on, it might be time.sleep for example
        return captureMockFunction(*args, **kw)

    def callRealFunction(self, captureMockTraffic, captureMockFunction, captureMockProxy):
        realRet = self.callStackChecker.callNoInterception(captureMockProxy.captureMockCallback, 
                                                           captureMockTraffic.callRealFunction,
                                                           captureMockFunction, self.recordFileHandler,
                                                           captureMockProxy)
        if captureMockTraffic.shouldRecord:
            return self.transformResponse(captureMockTraffic, realRet, captureMockProxy)
        else:
            return captureMockTraffic.transformResponse(realRet, captureMockProxy)[1]

    # Parameter names chosen to avoid potential clashes with args and kw which come from the app
    def callConstructor(self, captureMockClassName, captureMockRealClass, captureMockProxy,
                        *args, **kw):
        with self.lock:
            traffic = PythonFunctionCallTraffic(captureMockClassName, self.rcHandler,
                                                self.interceptModules, captureMockProxy, 
                                                self.callStackChecker.inCallback, *args, **kw)
            if self.callStackChecker.callerExcluded(stackDistance=3):
                realObj = captureMockRealClass(*args, **kw)
                wrapper = traffic.getWrapper(realObj)
                return wrapper.name, realObj
    
            self.record(traffic)
            if self.replayInfo.isActiveFor(traffic):
                responses = self.getReplayResponses(traffic)
                if len(responses):
                    firstText = responses[0][1]
                    self.recordResponse(firstText)
                    return self.getReplayInstanceName(firstText, captureMockProxy), None
                else:
                    raise CaptureMockReplayError("Could not match sufficiently well to construct object of type '" + captureMockClassName + "'")
            else:
                realObj = self.callStackChecker.callNoInterception(False, traffic.callRealFunction,
                                                                   captureMockRealClass, self.recordFileHandler,
                                                                   captureMockProxy)
                wrapper = traffic.getWrapper(realObj)
                self.recordResponse(repr(wrapper))
                return wrapper.name, realObj

    def recordSetAttribute(self, *args):
        if not self.callStackChecker.callerExcluded(stackDistance=3):
            traffic = PythonSetAttributeTraffic(self.rcHandler, self.interceptModules, self.callStackChecker.inCallback, *args)
            self.record(traffic)
            
