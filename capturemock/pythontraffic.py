
""" Traffic classes for all kinds of Python calls """

import traffic, sys, types, inspect
from recordfilehandler import RecordFileHandler
from config import CaptureMockReplayError

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

    def __repr__(self):
        return "Instance(" + repr(self.classDesc) + ", " + repr(self.name) + ")"

    def getNewInstanceName(self):
        className = self.classDesc.split("(")[0].lower()
        num = 1
        while self.allInstances.has_key(className + str(num)):
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
        text = "raise " + exc_value.__class__.__module__ + "." + exc_value.__class__.__name__ + "(" + repr(str(exc_value)) + ")"
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
        elif isinstance(arg, float):
            # Stick to 2 dp for recording floating point values
            return ReprObject(str(round(arg, 2)))
        else:
            return ReprObject(self.fixMultilineStrings(arg))

    def fixMultilineStrings(self, arg):
        out = repr(arg)
        # Replace linebreaks but don't mangle e.g. Windows paths
        # This won't work if both exist in the same string - fixing that requires
        # using a regex and I couldn't make it work [gjb 100922]
        if "\\n" in out and "\\\\n" not in out: 
            pos = out.find("'", 0, 2)
            if pos != -1:
                return out[:pos] + "''" + out[pos:].replace("\\n", "\n") + "''"
            else:
                pos = out.find('"', 0, 2)
                return out[:pos] + '""' + out[pos:].replace("\\n", "\n") + '""'
        else:
            return out

    def isMarkedForReplay(self, replayItems):
        textMarker = self.getTextMarker()
        return any((item == textMarker or textMarker.startswith(item + ".") for item in replayItems))

    def getIntercept(self, modOrAttr):
        if modOrAttr in self.interceptModules:
            return modOrAttr
        elif "." in modOrAttr:
            return self.getIntercept(modOrAttr.rsplit(".", 1)[0])

    def isBasicType(self, obj):
        return obj is None or obj is NotImplemented or type(obj) in (bool, float, int, long, str, unicode, list, dict, tuple)

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

    def getWrapper(self, instance):
        # hasattr fails if the intercepted instance defines __getattr__, when it always returns True
        if "captureMockTarget" in dir(instance):
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
                if module != "__builtin__":
                    name = module + "." + name
                classes.append(name)
                break
        return classes


class PythonAttributeTraffic(PythonModuleTraffic):
    cachedAttributes = set()
    cachedInstances = {}
    @classmethod
    def resetCaches(cls):
        cls.cachedAttributes = set()
        cls.cachedInstances = {}
        
    def __init__(self, fullAttrName, *args):
        # Should record these at most once, and only then if they return something in their own right
        # rather than a function etc
        self.foundInCache = fullAttrName in self.cachedAttributes
        self.cachedAttributes.add(fullAttrName)
        super(PythonAttributeTraffic, self).__init__(fullAttrName, *args)

    def shouldCache(self, obj):
        return type(obj) not in (types.FunctionType, types.GeneratorType, types.MethodType, types.BuiltinFunctionType,
                                 types.ClassType, types.TypeType, types.ModuleType) and \
                                 not hasattr(obj, "__call__")

    def getWrapper(self, instance):
        return self.cachedInstances.setdefault(self.text, PythonModuleTraffic.getWrapper(self, instance))

        
class PythonSetAttributeTraffic(PythonModuleTraffic):
    def __init__(self, rcHandler, interceptModules, proxyName, attrName, value):
        text = proxyName + "." + attrName + " = " + repr(self.insertReprObjects(value))
        super(PythonSetAttributeTraffic, self).__init__(text, rcHandler, interceptModules)


class PythonFunctionCallTraffic(PythonModuleTraffic):
    cachedFunctions = set()
    def __init__(self, functionName, rcHandler, interceptModules, *args, **kw):
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

    def stripProxy(self, arg):
        if hasattr(arg, "captureMockTarget"):
            return arg.captureMockTarget
        else:
            return arg

    # Naming to avoid clashes as args and kw come from the application
    def callRealFunction(self, captureMockFunction, captureMockRecordHandler, captureMockProxy, *args, **kw):
        realArgs = self.transformStructure(args, self.stripProxy)
        realKw = self.transformStructure(kw, self.stripProxy)
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
                return self.callStackChecker.callNoInterception(loadModule, name)
            except:
                response = traffic.getExceptionResponse(sys.exc_info())
                response.record(self.recordFileHandler)
                raise

    def processReplay(self, traffic, proxy, record=True):
        for responseText in self.getReplayResponses(traffic):
            if record:
                PythonResponseTraffic(responseText).record(self.recordFileHandler)
            return proxy.captureMockEvaluate(responseText)

    def getReplayResponses(self, traffic, **kw):
        return [ text for _, text in self.replayInfo.readReplayResponses(traffic, [ PythonResponseTraffic ], **kw) ]

    def getRealAttribute(self, target, attrName):
        if attrName == "__all__":
            # Need to provide something here, the application has probably called 'from module import *'
            return filter(lambda x: not x.startswith("__"), dir(target))
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
            if not traffic.foundInCache:
                traffic.record(self.recordFileHandler)
                self.recordResponse(responses[0])
            return proxy.captureMockEvaluate(responses[0])
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
            realAttr = self.callStackChecker.callNoInterception(self.getRealAttribute, proxyTarget, attrName)
        except:
            responseTraffic = traffic.getExceptionResponse(sys.exc_info())
            if not traffic.foundInCache:
                traffic.record(self.recordFileHandler)
                responseTraffic.record(self.recordFileHandler)
            raise
                
        if traffic.shouldCache(realAttr):
            if traffic.foundInCache:
                return traffic.transformResponse(realAttr, proxy)[1]
            else:
                traffic.record(self.recordFileHandler)
                if attrName == "__path__":
                    # don't want to record the real absolute path, which is just hard to filter
                    # and won't work for lookup anyway
                    self.recordResponse("['Fake value just to mark that it exists']")
                    return realAttr
                else:
                    return self.transformResponse(traffic, realAttr, proxy)
        else:
            if type(realAttr) in (types.ClassType, types.TypeType):
                classDesc = traffic.getClassDescription(realAttr)
                return proxy.captureMockCreateClassProxy(fullAttrName, realAttr, classDesc)
            else:
                return self.createInstanceProxy(proxy, fullAttrName, realAttr)

    def createInstanceProxy(self, proxy, fullAttrName, realAttr):
        if isinstance(proxy, type):
            return proxy.__class__.captureMockCreateInstanceProxy(proxy, fullAttrName, realAttr)
        else:
            return proxy.captureMockCreateInstanceProxy(fullAttrName, realAttr)
        
    def recordResponse(self, responseText):
        if responseText != "None":
            PythonResponseTraffic(responseText).record(self.recordFileHandler)

    def transformResponse(self, traffic, *args):
        responseText, transformedResponse = traffic.transformResponse(*args)
        self.recordResponse(responseText)
        return transformedResponse
    
    # Parameter names chosen to avoid potential clashes with args and kw which come from the app
    def callFunction(self, captureMockProxyName, captureMockProxy, captureMockFunction, *args, **kw):
        if self.callStackChecker.callerExcluded(stackDistance=3):
            return captureMockFunction(*args, **kw)
        else:
            traffic = PythonFunctionCallTraffic(captureMockProxyName, self.rcHandler,
                                                self.interceptModules, *args, **kw)
            if traffic.shouldRecord:
                traffic.record(self.recordFileHandler)
            if self.replayInfo.isActiveFor(traffic):
                return self.processReplay(traffic, captureMockProxy, traffic.shouldRecord)
            else:
                return self.callStackChecker.callNoInterception(self.callRealFunction, traffic,
                                                                captureMockFunction, captureMockProxy, *args, **kw)

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
                                            self.interceptModules, *args, **kw)
        if self.callStackChecker.callerExcluded(stackDistance=3):
            realObj = captureMockRealClass(*args, **kw)
            wrapper = traffic.getWrapper(realObj)
            return wrapper.name, realObj

        traffic.record(self.recordFileHandler)
        if self.replayInfo.isActiveFor(traffic):
            responses = self.getReplayResponses(traffic)
            if len(responses):
                self.recordResponse(responses[0])
                def Instance(classDesc, instanceName):
                    return instanceName
                return eval(responses[0]), None
            else:
                raise CaptureMockReplayError, "Could not match sufficiently well to construct object of type '" + captureMockClassName + "'"
        else:
            realObj = self.callStackChecker.callNoInterception(traffic.callRealFunction,
                                                               captureMockRealClass, self.recordFileHandler,
                                                               captureMockProxy, *args, **kw)
            wrapper = traffic.getWrapper(realObj)
            self.recordResponse(repr(wrapper))
            return wrapper.name, realObj

    def recordSetAttribute(self, *args):
        if not self.callStackChecker.callerExcluded(stackDistance=3):
            traffic = PythonSetAttributeTraffic(self.rcHandler, self.interceptModules, *args)
            traffic.record(self.recordFileHandler)
            
