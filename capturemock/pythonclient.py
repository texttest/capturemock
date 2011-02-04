
""" Python code for communicating via a socket with a CaptureMock server
 Should try to make default usage of this functionality not use a server """

import sys, os, socket

class NameFinder(dict):
    def __init__(self, moduleProxy):
        dict.__init__(self)
        self["InstanceProxy"] = InstanceProxy
        self["Instance"] = moduleProxy.makeInstance
        self.makeNewClasses = False
        self.newClassNames = []

    def defineClass(self, className, classDefStr, moduleProxy):
        self.defineClassLocally(classDefStr)
        for newClassName in [ className ] + self.newClassNames:
            setattr(moduleProxy, newClassName, dict.__getitem__(self, newClassName))
        self.newClassNames = []
        return dict.__getitem__(self, className)

    def defineClassLocally(self, classDefStr):
        try:
            exec classDefStr in self
        except NameError:
            self.makeNewClasses = True
            self.defineClassLocally(classDefStr)
            self.makeNewClasses = False
        
    def __getitem__(self, name):
        if name not in self:
            if self.makeNewClasses:
                exec "class " + name + ": pass" in self
                self.newClassNames.append(name)
                self.makeNewClasses = False
            else:
                try:
                    exec "import " + name in self
                except ImportError:
                    pass
        return dict.__getitem__(self, name)


def createSocket():
    servAddr = os.getenv("CAPTUREMOCK_SERVER")
    if servAddr:
        host, port = servAddr.split(":")
        serverAddress = (host, int(port))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(serverAddress)
        return sock



class ModuleProxy:
    def __init__(self, name, importHandler):
        self.name = name
        self.trafficServerNameFinder = NameFinder(self)
        self.importHandler = importHandler
        self.realModule = None
        self.tryImport() # trigger a remote import to make sure we're connected to something

    def handleResponse(self, response):
        if response.startswith("raise "):
            exec response in self.trafficServerNameFinder
        else:
            return eval(response, self.trafficServerNameFinder)
        
    def makeInstance(self, className, instanceName):
        if "(" in className:
            classDefStr = "class " + className.replace("(", "(InstanceProxy, ") + " : pass"
        else:
            classDefStr = "class " + className + "(InstanceProxy): pass"
        actualClassName = className.split("(")[0]
        actualClassObj = self.trafficServerNameFinder.defineClass(actualClassName, classDefStr, self)
        return actualClassObj(givenInstanceName=instanceName, moduleProxy=self)
        
    def tryImport(self):
        sock = createSocket()
        text = "SUT_PYTHON_IMPORT:" + self.name
        sock.sendall(text)
        sock.shutdown(1)
        response = sock.makefile().read()
        if response:
            self.handleResponse(response)

    def __getattr__(self, attrname):
        if self.importHandler.callStackChecker.callerExcluded():
            if self.realModule is None:
                self.realModule = self.importHandler.loadRealModule(self.name)
            return getattr(self.realModule, attrname)
        else:
            return AttributeProxy(self.name, self.handleResponse, attrname).tryEvaluate()
    

class InstanceProxy:
    moduleProxy = None
    def __init__(self, *args, **kw):
        self.name = kw.get("givenInstanceName")
        moduleProxy = kw.get("moduleProxy")
        if moduleProxy is not None:
            self.__class__.moduleProxy = moduleProxy
        if self.name is None:
            attrProxy = AttributeProxy(self.moduleProxy.name, self.moduleProxy.handleResponse,
                                       self.__class__.__name__)
            response = attrProxy.makeResponse(*args, **kw)
            def Instance(className, instanceName):
                return instanceName
            NewStyleInstance = Instance
            self.name = eval(response)

    def getRepresentationForSendToTrafficServer(self):
        return self.name

    def __getattr__(self, attrname):
        return AttributeProxy(self.name, self.moduleProxy.handleResponse, attrname).tryEvaluate()

    def __setattr__(self, attrname, value):
        self.__dict__[attrname] = value
        if attrname != "name":
            AttributeProxy(self.name, self.moduleProxy.handleResponse, attrname).setValue(value)

    # Used by mixins of this class and new-style classes
    def __getattribute__(self, attrname):
        if attrname in [ "name", "moduleProxy", "__dict__", "__class__", "__getattr__",
                         "getRepresentationForSendToTrafficServer" ]:
            return object.__getattribute__(self, attrname)
        else:
            return self.__getattr__(attrname)

    def __str__(self):
        return self.__getattr__("__str__")()

    def __repr__(self):
        return self.__getattr__("__repr__")()

    def __getitem__(self, *args):
        return self.__getattr__("__getitem__")(*args)


class AttributeProxy:
    def __init__(self, modOrObjName, handleResponse, attributeName, callStackChecker=None):
        self.modOrObjName = modOrObjName
        self.handleResponse = handleResponse
        self.attributeName = attributeName
        self.realVersion = None
        self.callStackChecker = callStackChecker
        
    def getRepresentationForSendToTrafficServer(self):
        return self.modOrObjName + "." + self.attributeName

    def tryEvaluate(self):
        sock = createSocket()
        text = "SUT_PYTHON_ATTR:" + self.modOrObjName + ":SUT_SEP:" + self.attributeName
        sock.sendall(text)
        sock.shutdown(1)
        response = sock.makefile().read()
        if response:
            return self.handleResponse(response)
        else:
            return self

    def setValue(self, value):
        sock = createSocket()
        text = "SUT_PYTHON_SETATTR:" + self.modOrObjName + ":SUT_SEP:" + self.attributeName + \
               ":SUT_SEP:" + repr(self.getArgForSend(value))
        sock.sendall(text)
        sock.shutdown(2)

    def __getattr__(self, name):
        return AttributeProxy(self.modOrObjName, self.handleResponse, self.attributeName + "." + name).tryEvaluate()

    def __call__(self, *args, **kw):
        if self.realVersion is None or self.callStackChecker is None or not self.callStackChecker.callerExcluded(): 
            response = self.makeResponse(*args, **kw)
            if response:
                return self.handleResponse(response)
        else:
            return self.realVersion(*args, **kw)

    def makeResponse(self, *args, **kw):
        sock = self.createAndSend(*args, **kw)
        sock.shutdown(1)
        return sock.makefile().read()

    def createAndSend(self, *args, **kw):
        sock = createSocket()
        text = "SUT_PYTHON_CALL:" + self.modOrObjName + ":SUT_SEP:" + self.attributeName + \
               ":SUT_SEP:" + repr(self.getArgsForSend(args)) + ":SUT_SEP:" + repr(self.getArgForSend(kw))
        sock.sendall(text)
        return sock

    def getArgForSend(self, arg):
        class ArgWrapper:
            def __init__(self, arg, handleResponse):
                self.arg = arg
                self.handleResponse = handleResponse
            def __repr__(self):
                if hasattr(self.arg, "getRepresentationForSendToTrafficServer"):
                    # We choose a long and obscure name to avoid accident clashes with something else
                    return self.arg.getRepresentationForSendToTrafficServer()
                elif isinstance(self.arg, list):
                    return repr([ ArgWrapper(subarg, self.handleResponse) for subarg in self.arg ])
                elif isinstance(self.arg, dict):
                    newDict = {}
                    for key, val in self.arg.items():
                        newDict[key] = ArgWrapper(val, self.handleResponse)
                    return repr(newDict)
                else:
                    return repr(self.arg)
        return ArgWrapper(arg, self.handleResponse)

    def getArgsForSend(self, args):
        return tuple(map(self.getArgForSend, args))

