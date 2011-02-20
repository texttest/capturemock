
""" Traffic classes for all kinds of Python calls """

import traffic, sys, types, inspect, re
from recordfilehandler import RecordFileHandler


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
    def getInstance(cls, instanceName):
        return cls.allInstances.get(instanceName, sys.modules.get(instanceName))

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
    def getExceptionResponse(self, exc_info):
        exc_value = exc_info[1]
        return PythonResponseTraffic(self.getExceptionText(exc_value))

    def getExceptionText(self, exc_value):
        return "raise " + exc_value.__class__.__module__ + "." + exc_value.__class__.__name__ + "(" + repr(str(exc_value)) + ")"


class PythonImportTraffic(PythonTraffic):
    def __init__(self, inText):
        self.moduleName = inText
        text = "import " + self.moduleName
        super(PythonImportTraffic, self).__init__(text)

    def isMarkedForReplay(self, replayItems):
        return self.moduleName in replayItems


class ReprObject:
    def __init__(self, arg):
        self.arg = arg
        
    def __repr__(self):
        return self.arg    

                  
class PythonModuleTraffic(PythonTraffic):
    def __init__(self, text, rcHandler):
        super(PythonModuleTraffic, self).__init__(text)
        self.interceptModules = set(rcHandler.getIntercepts("python"))
        self.alterations = {}
        for alterStr in rcHandler.getList("alterations", [ self.getTextMarker(), "python" ]):
            toFind = rcHandler.get("match_pattern", [ alterStr ])
            toReplace = rcHandler.get("replacement", [ alterStr ])
            if toFind and toReplace:
                self.alterations[re.compile(toFind)] = toReplace
        
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

    def getTextMarker(self):
        return self.text

    def isMarkedForReplay(self, replayItems):
        textMarker = self.getTextMarker()
        return any((item == textMarker or textMarker.startswith(item + ".") for item in replayItems))

    def belongsToInterceptedModule(self, moduleName):
        if moduleName in self.interceptModules:
            return True
        elif "." in moduleName:
            return self.belongsToInterceptedModule(moduleName.rsplit(".", 1)[0])
        else:
            return False

    def isBasicType(self, obj):
        return obj is None or obj is NotImplemented or type(obj) in (bool, float, int, long, str, unicode, list, dict, tuple)

    def getPossibleCompositeAttribute(self, instance, attrName):
        attrParts = attrName.split(".", 1)
        firstAttr = getattr(instance, attrParts[0])
        if len(attrParts) == 1:
            return firstAttr
        else:
            return self.getPossibleCompositeAttribute(firstAttr, attrParts[1])

    def evaluate(self, argStr):
        class UnknownInstanceWrapper:
            def __init__(self, name):
                self.name = name
        class NameFinder:
            def __getitem__(self, name):
                return PythonInstanceWrapper.getInstance(name) or UnknownInstanceWrapper(name)
        try:
            return eval(argStr)
        except NameError:
            return eval(argStr, globals(), NameFinder())

    def getResultText(self, result):
        text = repr(self.transformStructure(result, self.insertReprObjects))
        for regex, repl in self.alterations.items():
            text = regex.sub(repl, text)
        return text

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
        if not self.isBasicType(result) and self.belongsToInterceptedModule(self.getModuleName(result)):
            return self.getWrapper(result)
        else:
            return result

    def insertProxy(self, result, proxy):
        if isinstance(result, PythonInstanceWrapper):
            return proxy.captureMockCreateProxy(result.name, result.target, result.classDesc)
        else:
            return result

    def getWrapper(self, instance):
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
            if self.belongsToInterceptedModule(baseClass.__module__):
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
    def __init__(self, fullAttrName, rcHandler):
        # Should record these at most once, and only then if they return something in their own right
        # rather than a function etc
        self.foundInCache = fullAttrName in self.cachedAttributes
        self.cachedAttributes.add(fullAttrName)
        super(PythonAttributeTraffic, self).__init__(fullAttrName, rcHandler)

    def enquiryOnly(self, responses=[]):
        return len(responses) == 0 or self.foundInCache

    def shouldCache(self, obj):
        return type(obj) not in (types.FunctionType, types.GeneratorType, types.MethodType, types.BuiltinFunctionType,
                                 types.ClassType, types.TypeType, types.ModuleType) and \
                                 not hasattr(obj, "__call__")

    def forwardToDestination(self):
        instance = PythonInstanceWrapper.getInstance(self.modOrObjName)
        try:
            attr = self.getPossibleCompositeAttribute(instance, self.attrName)
        except:
            if self.attrName == "__all__":
                # Need to provide something here, the application has probably called 'from module import *'
                attr = filter(lambda x: not x.startswith("__"), dir(instance))
            else:
                return [ self.getExceptionResponse() ]
        if self.shouldCache(attr):
            resultText = self.getResultText(attr)
            return [ PythonResponseTraffic(resultText) ]
        else:
            # Makes things more readable if we delay evaluating this until the function is called
            # It's rare in Python to cache functions/classes before calling: it's normal to cache other things
            return []

        
class PythonSetAttributeTraffic(PythonModuleTraffic):
    def __init__(self, rcHandler, proxyName, attrName, value):
        text = proxyName + "." + attrName + " = " + repr(self.insertReprObjects(value))
        super(PythonSetAttributeTraffic, self).__init__(text, rcHandler)

    def forwardToDestination(self):
        instance = PythonInstanceWrapper.getInstance(self.modOrObjName)
        value = self.evaluate(self.valueStr)
        setattr(instance.instance, self.attrName, value)
        return []


class PythonFunctionCallTraffic(PythonModuleTraffic):
    def __init__(self, functionName, rcHandler, *args, **kw):
        argsForRecord = self.transformStructure(list(args), self.insertReprObjects)
        keywForRecord = self.transformStructure(kw, self.insertReprObjects)
        for key in sorted(keywForRecord.keys()):
            value = keywForRecord[key]
            recordArg = ReprObject(key + "=" + repr(value))
            argsForRecord.append(recordArg)
        text = functionName + "(" + repr(argsForRecord)[1:-1] + ")"
        self.functionName = functionName
        super(PythonFunctionCallTraffic, self).__init__(text, rcHandler)
            
    def getArgForRecord(self, arg):
        class ArgWrapper:
            def __init__(self, arg):
                self.arg = arg
            def __repr__(self):
                if hasattr(self.arg, "instanceName"):
                    return self.arg.instanceName
                elif isinstance(self.arg, list):
                    return repr([ ArgWrapper(subarg) for subarg in self.arg ])
                elif isinstance(self.arg, dict):
                    newDict = {}
                    for key, val in self.arg.items():
                        newDict[key] = ArgWrapper(val)
                    return repr(newDict)
        return repr(ArgWrapper(arg))

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
            if self.belongsToInterceptedModule(moduleName):
                # We own the exception object also, handle it like an ordinary instance
                wrapper = self.getWrapper(exc_value)
                responseText = "raise " + repr(wrapper)
                PythonResponseTraffic(responseText).record(captureMockRecordHandler)
                raise self.insertProxy(wrapper, captureMockProxy)
            else:
                responseText = self.getExceptionText(exc_value)
                PythonResponseTraffic(responseText).record(captureMockRecordHandler)
                raise
            
    def getArgInstance(self, arg):
        if isinstance(arg, PythonInstanceWrapper):
            return arg.instance
        elif isinstance(arg, list):
            return map(self.getArgInstance, arg)
        else:
            return arg

    def callFunction(self, instance):
        if self.attrName == "__repr__" and isinstance(instance, PythonInstanceWrapper): # Has to be special case as we use it internally
            return repr(instance.instance)
        else:
            func = self.getPossibleCompositeAttribute(instance, self.attrName)
            return func(*self.args, **self.keyw)
    
    def getResult(self):
        instance = PythonInstanceWrapper.getInstance(self.modOrObjName)
        try:
            result = self.callFunction(instance)
            return self.getResultText(result)
        except:
            exc_value = sys.exc_info()[1]
            moduleName = self.getModuleName(exc_value)
            if self.belongsToInterceptedModule(moduleName):
                # We own the exception object also, handle it like an ordinary instance
                wrapper = self.getWrapper(exc_value, moduleName)
                return "raise " + repr(wrapper)
            else:
                return self.getExceptionText(exc_value)

    def forwardToDestination(self):
        result = self.getResult()
        if result != "None":
            return [ PythonResponseTraffic(result, self.responseFile) ]
        else:
            return []


class PythonResponseTraffic(traffic.BaseTraffic):
    typeId = "RET"
    direction = "->"

class PythonTrafficHandler:
    def __init__(self, replayInfo, recordFile, rcHandler, callStackChecker):
        self.replayInfo = replayInfo
        self.recordFileHandler = RecordFileHandler(recordFile)
        self.callStackChecker = callStackChecker
        self.rcHandler = rcHandler
        PythonInstanceWrapper.allInstances = {} # reset, in case of previous tests

    def importModule(self, name, proxy, loadModule):
        traffic = PythonImportTraffic(name)
        traffic.record(self.recordFileHandler)
        if self.replayInfo.isActiveFor(traffic):
            return self.processReplay(traffic, proxy)
        else:
            try:
                return loadModule(name)
            except:
                response = traffic.getExceptionResponse(sys.exc_info())
                response.record(self.recordFileHandler)
                raise

    def processReplay(self, traffic, proxy):
        for responseText in self.getReplayResponses(traffic):
            PythonResponseTraffic(responseText).record(self.recordFileHandler)
            return proxy.captureMockEvaluate(responseText)

    def record(self, traffic, responses):
        for response in responses:
            response.record(self.recordFileHandler)

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
            traffic = PythonAttributeTraffic(fullAttrName, self.rcHandler)
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
            return proxy.captureMockCreateProxy(fullAttrName, newTarget)

    def getAndRecordRealAttribute(self, traffic, proxyTarget, attrName, proxy, fullAttrName):
        try:
            realAttr = self.getRealAttribute(proxyTarget, attrName)
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
            return proxy.captureMockCreateProxy(fullAttrName, realAttr)

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
            traffic = PythonFunctionCallTraffic(captureMockProxyName, self.rcHandler, *args, **kw)
            traffic.record(self.recordFileHandler)
            if self.replayInfo.isActiveFor(traffic):
                return self.processReplay(traffic, captureMockProxy)
            else:
                realRet = traffic.callRealFunction(captureMockFunction, self.recordFileHandler,
                                                   captureMockProxy, *args, **kw)
                return self.transformResponse(traffic, realRet, captureMockProxy)

    # Parameter names chosen to avoid potential clashes with args and kw which come from the app
    def callConstructor(self, captureMockClassName, captureMockRealClass, captureMockProxy,
                        *args, **kw):
        traffic = PythonFunctionCallTraffic(captureMockClassName, self.rcHandler, *args, **kw)
        traffic.record(self.recordFileHandler)
        if self.replayInfo.isActiveFor(traffic):
            responses = self.getReplayResponses(traffic)
            if len(responses):
                self.recordResponse(responses[0])
                def Instance(classDesc, instanceName):
                    return instanceName
                return eval(responses[0]), None
            else:
                raise CaptureMockReplayError, "Could not match sufficiently well to construct object of type '" + className + "'"
        else:
            realObj = traffic.callRealFunction(captureMockRealClass, self.recordFileHandler,
                                               captureMockProxy, *args, **kw)
            wrapper = traffic.getWrapper(realObj)
            self.recordResponse(repr(wrapper))
            return wrapper.name, realObj

    def recordSetAttribute(self, *args):
        if not self.callStackChecker.callerExcluded(stackDistance=3):
            traffic = PythonSetAttributeTraffic(self.rcHandler, *args)
            traffic.record(self.recordFileHandler)
            
            
def getTrafficClasses(incoming):
    if incoming:
        return [ PythonFunctionCallTraffic, PythonAttributeTraffic,
                 PythonSetAttributeTraffic, PythonImportTraffic ]
    else:
        return [ PythonResponseTraffic ] 

