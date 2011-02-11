
""" Traffic classes for all kinds of Python calls """

import traffic, sys, types, inspect, re
from recordfilehandler import RecordFileHandler

class PythonInstanceWrapper:
    allInstances = {}
    wrappersByInstance = {}
    def __init__(self, instance, moduleName, classDesc):
        self.instance = instance
        self.moduleName = moduleName
        self.classDesc = classDesc
        self.instanceName = self.getNewInstanceName()
        self.allInstances[self.instanceName] = self
        self.wrappersByInstance[id(self.instance)] = self

    @classmethod
    def getInstance(cls, instanceName):
        return cls.allInstances.get(instanceName, sys.modules.get(instanceName, cls.forceImport(instanceName)))

    @classmethod
    def forceImport(cls, moduleName):
        try:
            exec "import " + moduleName
            return sys.modules.get(moduleName)
        except ImportError:
            pass

    @classmethod
    def getWrapperFor(cls, instance, *args):
        storedWrapper = cls.wrappersByInstance.get(id(instance))
        return storedWrapper or cls(instance, *args)        

    def __repr__(self):
        return "Instance(" + repr(self.classDesc) + ", " + repr(self.instanceName) + ")"

    def getNewInstanceName(self):
        className = self.classDesc.split("(")[0].lower()
        num = 1
        while self.allInstances.has_key(className + str(num)):
            num += 1
        return className + str(num)

    def __getattr__(self, name):
        return getattr(self.instance, name)


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

    def getTextMarker(self):
        return self.text

    def isMarkedForReplay(self, replayItems):
        if PythonInstanceWrapper.getInstance(self.modOrObjName) is None:
            return True
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
                self.instanceName = name
        class NameFinder:
            def __getitem__(self, name):
                return PythonInstanceWrapper.getInstance(name) or UnknownInstanceWrapper(name)
        try:
            return eval(argStr)
        except NameError:
            return eval(argStr, globals(), NameFinder())

    def getResultText(self, result):
        text = repr(self.addInstanceWrappers(result))
        for regex, repl in self.alterations.items():
            text = regex.sub(repl, text)
        return text
    
    def addInstanceWrappers(self, result):
        if type(result) in (list, tuple):
            return type(result)(map(self.addInstanceWrappers, result))
        elif type(result) == dict:
            newResult = {}
            for key, value in result.items():
                newResult[key] = self.addInstanceWrappers(value)
            return newResult
        elif not self.isBasicType(result) and self.belongsToInterceptedModule(self.getModuleName(result)):
            return self.getWrapper(result, self.modOrObjName)
        else:
            return result

    def getWrapper(self, instance, moduleName):
        classDesc = self.getClassDescription(instance.__class__)
        return PythonInstanceWrapper.getWrapperFor(instance, moduleName, classDesc)

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
    socketId = "SUT_PYTHON_SETATTR"
    def __init__(self, inText, responseFile, rcHandler):
        modOrObjName, attrName, self.valueStr = inText.split(":SUT_SEP:")
        text = modOrObjName + "." + attrName + " = " + self.valueStr
        super(PythonSetAttributeTraffic, self).__init__(modOrObjName, attrName, rcHandler, text, responseFile)

    def forwardToDestination(self):
        instance = PythonInstanceWrapper.getInstance(self.modOrObjName)
        value = self.evaluate(self.valueStr)
        setattr(instance.instance, self.attrName, value)
        return []


class PythonFunctionCallTraffic(PythonModuleTraffic):
    def __init__(self, functionName, rcHandler, *args, **kw):
        self.args = args
        self.keyw = kw
        argsForRecord = []
        argStr = repr(self.args)
        keywDictStr = repr(self.keyw)
        try:
            internalArgs = self.evaluate(argStr)
            self.args = tuple(map(self.getArgInstance, internalArgs))
            argsForRecord += [ self.getArgForRecord(arg) for arg in internalArgs ]
        except:
            # Not ideal, but better than exit with exception
            # If this happens we probably can't handle the arguments anyway
            # Slightly daring text-munging of Python tuple repr, main thing is to print something vaguely representative
            argsForRecord += argStr.replace(",)", ")")[1:-1].split(", ")
        try:
            internalKw = self.evaluate(keywDictStr)
            for key, value in internalKw.items():
                self.keyw[key] = self.getArgInstance(value)
            for key in sorted(internalKw.keys()):
                value = internalKw[key]
                argsForRecord.append(key + "=" + self.getArgForRecord(value))
        except:
            # Not ideal, but better than exit with exception
            # If this happens we probably can't handle the keyword objects anyway
            # Slightly daring text-munging of Python dictionary repr, main thing is to print something vaguely representative
            argsForRecord += keywDictStr.replace("': ", "=").replace(", '", ", ")[2:-1].split(", ")
        text = functionName + "(" + ", ".join(argsForRecord) + ")"
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
                elif isinstance(self.arg, float):
                    # Stick to 2 dp for recording floating point values
                    return str(round(self.arg, 2))
                else:
                    out = repr(self.arg)
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
        return repr(ArgWrapper(arg))
            
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

    def importModule(self, name, realException):
        traffic = PythonImportTraffic(name)
        traffic.record(self.recordFileHandler)
        if self.replayInfo.isActiveFor(traffic):
            return self.processReplay(traffic)
        else:
            if realException:
                raise realException

    def processReplay(self, traffic, nameFinder=sys.modules):
        for responseText in self.getReplayResponses(traffic):
            PythonResponseTraffic(responseText).record(self.recordFileHandler)
            return self.handleResponse(responseText, nameFinder)

    def record(self, traffic, responses):
        for response in responses:
            response.record(self.recordFileHandler)

    def getReplayResponses(self, traffic, **kw):
        return [ text for _, text in self.replayInfo.readReplayResponses(traffic, [ PythonResponseTraffic ], **kw) ]

    def handleResponse(self, response, nameFinder=sys.modules):
        if response.startswith("raise "):
            exec response in nameFinder
        else:
            return eval(response, nameFinder)

    def getRealAttribute(self, realModOrObj, attrName):
        if attrName == "__all__":
            # Need to provide something here, the application has probably called 'from module import *'
            return filter(lambda x: not x.startswith("__"), dir(realModOrObj))
        else:
            return getattr(realModOrObj, attrName)

    def getAttribute(self, fullAttrName, attrName, realModOrObj, nameFinder):
        if self.callStackChecker.callerExcluded(stackDistance=3) or attrName == "__path__":
            return True, self.getRealAttribute(realModOrObj, attrName)
        else:
            traffic = PythonAttributeTraffic(fullAttrName, self.rcHandler)
            if self.replayInfo.isActiveFor(traffic):
                responses = self.getReplayResponses(traffic, exact=True)
                if len(responses):
                    traffic.record(self.recordFileHandler)
                    self.recordResponse(responses[0])
                    return True, self.handleResponse(responses[0], nameFinder)
                else:
                    return False, None
            else:
                realAttr = self.getRealAttribute(realModOrObj, attrName)
                if traffic.shouldCache(realAttr):
                    traffic.record(self.recordFileHandler)
                    self.recordResponse(traffic.getResultText(realAttr))
                    return True, realAttr
                else:
                    return False, realAttr

    def recordResponse(self, responseText):
        PythonResponseTraffic(responseText).record(self.recordFileHandler)
            
    def callFunction(self, functionName, realFunction, nameFinder, *args, **kw):
        if self.callStackChecker.callerExcluded(stackDistance=3):
            return realFunction(*args, **kw)
        else:
            traffic = PythonFunctionCallTraffic(functionName, self.rcHandler, *args, **kw)
            traffic.record(self.recordFileHandler)
            if self.replayInfo.isActiveFor(traffic):
                return self.processReplay(traffic)
            else:
                realRet = realFunction(*args, **kw)
                self.recordResponse(traffic.getResultText(realRet))
                return realRet


def getTrafficClasses(incoming):
    if incoming:
        return [ PythonFunctionCallTraffic, PythonAttributeTraffic,
                 PythonSetAttributeTraffic, PythonImportTraffic ]
    else:
        return [ PythonResponseTraffic ] 

