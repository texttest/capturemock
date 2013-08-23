
""" Traffic classes for all kinds of Python calls """

import sys, types, inspect
from . import traffic
from .recordfilehandler import RecordFileHandler
from .config import CaptureMockReplayError

class PythonInstanceWrapper:
    allInstances = {}
    wrappersByInstance = {}
    def __init__(self, instance, classDesc):
        self.target = instance
        self.classDesc = classDesc
        self.name = self.getNewInstanceName()
        self.allInstances[self.name] = self
        self.wrappersByInstance[id(self.target)] = self

    @classmethod
    def getWrapperFor(cls, instance, *args):
        storedWrapper = cls.wrappersByInstance.get(id(instance))
        return storedWrapper or cls(instance, *args)
    
    @classmethod
    def hasWrapper(cls, instance):
        return id(instance) in cls.wrappersByInstance       

    def __repr__(self):
        return "Instance(" + repr(self.classDesc) + ", " + repr(self.name) + ")"

    def getNewInstanceName(self):
        className = self.classDesc.split("(")[0].lower()
        num = 1
        while className + str(num) in self.allInstances:
            num += 1
        return className + str(num)


class PythonTraffic(traffic.BaseTraffic):
    typeId = "PYT"
    direction = "<-"
    def getAlterationSectionNames(self):
        return [ self.getTextMarker(), "python", "general" ]

    def getTextMarker(self):
        return self.text
        
    def getExceptionResponse(self, exc_info):
        exc_value = exc_info[1]
        return PythonResponseTraffic(self.getExceptionText(exc_value))

    def getExceptionText(self, exc_value):
        modStr = exc_value.__class__.__module__
        classStr = exc_value.__class__.__name__
        if modStr != "builtins":
            classStr = modStr + "." + classStr
        text = "raise " + classStr + "(" + repr(str(exc_value)) + ")"
        return self.applyAlterations(text)


class PythonImportTraffic(PythonTraffic):
    def __init__(self, inText, *args):
        self.moduleName = inText
        text = "import " + self.moduleName
        super(PythonImportTraffic, self).__init__(text, *args)

    def isMarkedForReplay(self, replayItems):
        return self.moduleName in replayItems


class ReprObject:
    def __init__(self, arg):
        self.arg = arg
        
    def __repr__(self):
        return self.arg    

                  
class PythonModuleTraffic(PythonTraffic):
    def __init__(self, text, rcHandler, interceptModules):
        super(PythonModuleTraffic, self).__init__(text, rcHandler)
        self.interceptModules = interceptModules
        
    def getModuleName(self, obj):
        if hasattr(obj, "__module__"): # classes, functions, many instances
            return obj.__module__
        else:
            return obj.__class__.__module__ # many other instances

    def insertReprObjects(self, arg):
        if hasattr(arg, "captureMockProxyName"):
            return ReprObject(arg.captureMockProxyName)
        elif self.direction == "->" and PythonInstanceWrapper.hasWrapper(arg):
            return ReprObject(PythonInstanceWrapper.getWrapperFor(arg).name) 
        elif isinstance(arg, float):
            # Stick to 2 dp for recording floating point values
            return ReprObject(str(round(arg, 2)))
        elif self.isCallableType(arg):
            return ReprObject("Callback('" + arg.__name__ + "')")
        else:
            return ReprObject(self.fixMultilineStrings(arg))

    def isCallableType(self, obj):        
        cacheTypes = (types.FunctionType, types.GeneratorType, types.MethodType, types.BuiltinFunctionType,
                      type, types.ModuleType)
        if sys.version_info[0] == 2:
            cacheTypes += (types.ClassType,)
        return type(obj) in cacheTypes or hasattr(obj, "__call__")


    def isMarkedForReplay(self, replayItems):
        textMarker = self.getTextMarker()
        return any((item == textMarker or textMarker.startswith(item + ".") for item in replayItems))

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

    def getResultText(self, result):
        text = repr(self.transformStructure(result, self.insertReprObjects))
        return self.applyAlterations(text)

    def transformResponse(self, response, proxy):
        wrappedValue = self.transformStructure(response, self.addInstanceWrapper)
        responseText = self.getResultText(wrappedValue)
        transformedResponse = self.transformStructure(wrappedValue, self.insertProxy, proxy)
        return responseText, transformedResponse

    def transformStructure(self, result, transformMethod, *args):
        if type(result) in (list, tuple):
            return type(result)([ self.transformStructure(elem, transformMethod, *args) for elem in result ])
        elif type(result) == dict:
            newResult = {}
            for key, value in result.items():
                newResult[key] = self.transformStructure(value, transformMethod, *args)
            return newResult
        else:
            return transformMethod(result, *args)
        
    def addInstanceWrapper(self, result):
        if not self.isBasicType(result) and self.getIntercept(self.getModuleName(result)):
            return self.getWrapper(result)
        else:
            return result

    def insertProxy(self, result, proxy):
        if isinstance(result, PythonInstanceWrapper):
            return proxy.captureMockCreateInstanceProxy(result.name, result.target, result.classDesc)
        else:
            return result

    def instanceHasAttribute(self, instance, attr):
        # hasattr fails if the intercepted instance defines __getattr__, when it always returns True
        # dir() can throw exceptions if __dir__ does (instance can be anything at all, and its __dir__ might raise anything at all)
        try:
            return attr in dir(instance)
        except:
            return False

    def getWrapper(self, instance):
        # hasattr fails if the intercepted instance defines __getattr__, when it always returns True
        if self.instanceHasAttribute(instance, "captureMockTarget"):
            return self.getWrapper(instance.captureMockTarget)
        classDesc = self.getClassDescription(instance.__class__)
        return PythonInstanceWrapper.getWrapperFor(instance, classDesc)

    def getClassDescription(self, cls):
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
                module = baseClass.__module__
                if module not in [ "__builtin__", "builtins" ]:
                    name = module + "." + name
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

    def getWrapper(self, instance):
        return self.cachedInstances.setdefault(self.text, PythonModuleTraffic.getWrapper(self, instance))

        
class PythonSetAttributeTraffic(PythonModuleTraffic):
    def __init__(self, rcHandler, interceptModules, proxyName, attrName, value):
        text = proxyName + "." + attrName + " = " + repr(self.insertReprObjects(value))
        super(PythonSetAttributeTraffic, self).__init__(text, rcHandler, interceptModules)


class PythonFunctionCallTraffic(PythonModuleTraffic):
    cachedFunctions = set()
    def __init__(self, functionName, rcHandler, interceptModules, callback, *args, **kw):
        if callback:
            self.direction = "->"
        argsForRecord = self.transformStructure(list(args), self.insertReprObjects)
        keywForRecord = self.transformStructure(kw, self.insertReprObjects)
        for key in sorted(keywForRecord.keys()):
            value = keywForRecord[key]
            recordArg = ReprObject(key + "=" + repr(value))
            argsForRecord.append(recordArg)
        text = functionName + "(" + repr(argsForRecord)[1:-1] + ")"
        self.functionName = functionName
        super(PythonFunctionCallTraffic, self).__init__(text, rcHandler, interceptModules)
        checkRepeats = rcHandler.getboolean("check_repeated_calls", [ self.getIntercept(self.functionName), "python" ], True)
        if checkRepeats:
            self.shouldRecord = True
        else:
            self.shouldRecord = self.functionName not in self.cachedFunctions
            self.cachedFunctions.add(self.functionName)
            
    def getTextMarker(self):
        return self.functionName

    def switchProxies(self, arg, captureMockProxy):
        # we need our proxies present in system calls, and absent in real calls to intercepted code
        # So we remove them from normal function calls, and add them in callbacks
        if self.direction == "<-":
            if hasattr(arg, "captureMockTarget") and not arg.captureMockCallback:
                return arg.captureMockTarget
        else:
            if PythonInstanceWrapper.hasWrapper(arg):
                return self.insertProxy(PythonInstanceWrapper.getWrapperFor(arg), captureMockProxy)
        return arg 
            
    def wrapCallbacks(self, arg, captureMockProxy):
        if not hasattr(arg, "captureMockTarget") and self.isCallableType(arg):
            return captureMockProxy.captureMockCreateInstanceProxy(arg.__name__, arg, captureMockCallback=True)
        else:
            return arg

    # Naming to avoid clashes as args and kw come from the application
    def callRealFunction(self, captureMockFunction, captureMockRecordHandler, captureMockProxy, *args, **kw):
        realArgs = self.transformStructure(args, self.switchProxies, captureMockProxy)
        realKw = self.transformStructure(kw, self.switchProxies, captureMockProxy)
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
                

class PythonResponseTraffic(traffic.BaseTraffic):
    typeId = "RET"
    direction = "->"
    def __init__(self, text, rcHandler=None, callback=False):
        if callback:
            self.direction = "<-"
        super(PythonResponseTraffic, self).__init__(text, rcHandler)

class PythonTrafficHandler:
    def __init__(self, replayInfo, recordFile, rcHandler, callStackChecker, interceptModules):
        self.replayInfo = replayInfo
        self.recordFileHandler = RecordFileHandler(recordFile)
        self.callStackChecker = callStackChecker
        self.rcHandler = rcHandler
        self.interceptModules = interceptModules
        PythonInstanceWrapper.allInstances = {} # reset, in case of previous tests
        PythonAttributeTraffic.resetCaches()

    def importModule(self, name, proxy, loadModule):
        traffic = PythonImportTraffic(name, self.rcHandler)
        traffic.record(self.recordFileHandler)
        if self.replayInfo.isActiveFor(traffic):
            return self.processReplay(traffic, proxy)
        else:
            try:
                return self.callStackChecker.callNoInterception(False, loadModule, name)
            except:
                response = traffic.getExceptionResponse(sys.exc_info())
                self.record(response)
                raise
            
    def record(self, traffic):
        traffic.record(self.recordFileHandler, delay=self.callStackChecker.inCallback)

    def processReplay(self, traffic, proxy, record=True):
        lastResponse = None
        for responseClass, responseText in self.getReplayResponses(traffic):
            if record and responseClass is PythonResponseTraffic:
                self.record(responseClass(responseText))
            lastResponse = proxy.captureMockEvaluate(responseText)
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
        fullAttrName = proxyName + "." + attrName
        if self.callStackChecker.callerExcluded(stackDistance=3):
            if proxyTarget is None:
                proxyTarget = proxy.captureMockLoadRealModule()
            return self.getRealAttribute(proxyTarget, attrName)
        else:
            traffic = PythonAttributeTraffic(fullAttrName, self.rcHandler, self.interceptModules)
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

    def getAndRecordRealAttribute(self, traffic, proxyTarget, attrName, proxy, fullAttrName):
        try:
            realAttr = self.callStackChecker.callNoInterception(False, self.getRealAttribute, proxyTarget, attrName)
        except:
            responseTraffic = traffic.getExceptionResponse(sys.exc_info())
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
            if type(realAttr) is type or (sys.version_info[0] == 2 and type(realAttr) is types.ClassType):
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
            self.record(PythonResponseTraffic(responseText, callback=callback))

    def transformResponse(self, traffic, response, proxy):
        responseText, transformedResponse = traffic.transformResponse(response, proxy)
        self.recordResponse(responseText, proxy.captureMockCallback)
        return transformedResponse
    
    # Parameter names chosen to avoid potential clashes with args and kw which come from the app
    def callFunction(self, captureMockProxyName, captureMockProxy, captureMockFunction, *args, **kw):
        isCallback = captureMockProxy.captureMockCallback
        if self.callStackChecker.callerExcluded(stackDistance=3, callback=isCallback):
            return captureMockFunction(*args, **kw)
        else:
            traffic = PythonFunctionCallTraffic(captureMockProxyName, self.rcHandler,
                                                self.interceptModules, isCallback, *args, **kw)
            if traffic.shouldRecord:
                self.record(traffic)
            realArgs = traffic.transformStructure(args, traffic.wrapCallbacks, captureMockProxy)
            realKw = traffic.transformStructure(kw, traffic.wrapCallbacks, captureMockProxy)
            if not isCallback and self.replayInfo.isActiveFor(traffic):
                return self.processReplay(traffic, captureMockProxy, traffic.shouldRecord)
            else:
                return self.callStackChecker.callNoInterception(isCallback, self.callRealFunction, traffic,
                                                                captureMockFunction, captureMockProxy, *realArgs, **realKw)

    def callRealFunction(self, captureMockTraffic, captureMockFunction, captureMockProxy, *args, **kw):
        realRet = captureMockTraffic.callRealFunction(captureMockFunction, self.recordFileHandler,
                                                      captureMockProxy, *args, **kw)
        if captureMockTraffic.shouldRecord:
            return self.transformResponse(captureMockTraffic, realRet, captureMockProxy)
        else:
            return captureMockTraffic.transformResponse(realRet, captureMockProxy)[1]

    # Parameter names chosen to avoid potential clashes with args and kw which come from the app
    def callConstructor(self, captureMockClassName, captureMockRealClass, captureMockProxy,
                        *args, **kw):
        traffic = PythonFunctionCallTraffic(captureMockClassName, self.rcHandler,
                                            self.interceptModules, False, *args, **kw)
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
                def Instance(classDesc, instanceName):
                    return instanceName
                return eval(firstText), None
            else:
                raise CaptureMockReplayError("Could not match sufficiently well to construct object of type '" + captureMockClassName + "'")
        else:
            realObj = self.callStackChecker.callNoInterception(False, traffic.callRealFunction,
                                                               captureMockRealClass, self.recordFileHandler,
                                                               captureMockProxy, *args, **kw)
            wrapper = traffic.getWrapper(realObj)
            self.recordResponse(repr(wrapper))
            return wrapper.name, realObj

    def recordSetAttribute(self, *args):
        if not self.callStackChecker.callerExcluded(stackDistance=3):
            traffic = PythonSetAttributeTraffic(self.rcHandler, self.interceptModules, *args)
            self.record(traffic)
            
