
""" Actual interception objects used by Python interception mechanism """
import sys, os, types

class NameFinder(dict):
    def __init__(self, moduleProxy):
        dict.__init__(self)
        self.moduleProxy = moduleProxy
        self["InstanceProxy"] = InstanceProxy
        self["Instance"] = self.makeInstance
        self.makeNewClasses = False
        self.newClassNames = []

    def defineClass(self, className, classDefStr):
        self.defineClassLocally(classDefStr)
        for newClassName in [ className ] + self.newClassNames:
            self.moduleProxy.__dict__[newClassName] = dict.__getitem__(self, newClassName)
        self.newClassNames = []
        newClass = dict.__getitem__(self, className)
        newClass.__module__ = self.moduleProxy.captureMockProxyName
        return newClass

    def makeClass(self, className):
        if "(" in className:
            classDefStr = "class " + className.replace("(", "(InstanceProxy, ") + " : pass"
        else:
            classDefStr = "class " + className + "(InstanceProxy): pass"
        actualClassName = className.split("(")[0]
        return self.defineClass(actualClassName, classDefStr)

    def makeInstance(self, className, instanceName):
        return self.moduleProxy.captureMockCreateProxy(instanceName, classDesc=className)

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


class PythonProxy:
    def __init__(self, captureMockProxyName, captureMockTrafficHandler,
                 captureMockTarget, captureMockNameFinder):
        # Will be passed into real code. Try to minimise the risk of name clashes
        self.captureMockProxyName = captureMockProxyName
        self.captureMockTrafficHandler = captureMockTrafficHandler
        self.captureMockTarget = captureMockTarget
        self.captureMockNameFinder = captureMockNameFinder

    def __getattr__(self, attrname):
        return self.captureMockTrafficHandler.getAttribute(self.captureMockProxyName,
                                                           attrname,
                                                           self,
                                                           self.captureMockTarget)
    def captureMockCreateProxy(self, proxyName, proxyTarget=None, classDesc=None):
        if classDesc:
            newClass = self.captureMockNameFinder.makeClass(classDesc)
        else:
            newClass = PythonProxy
        return newClass(captureMockProxyName=proxyName,
                        captureMockTrafficHandler=self.captureMockTrafficHandler,
                        captureMockTarget=proxyTarget,
                        captureMockNameFinder=self.captureMockNameFinder)

    def captureMockEvaluate(self, response):
        if response.startswith("raise "):
            exec response in self.captureMockNameFinder
        else:
            return eval(response, self.captureMockNameFinder)

    def __call__(self, *args, **kw):
        return self.captureMockTrafficHandler.callFunction(self.captureMockProxyName,
                                                           self,
                                                           self.captureMockTarget,
                                                           *args, **kw)

    def __setattr__(self, attrname, value):
        self.__dict__[attrname] = value
        if not attrname.startswith("captureMock"):
            if self.captureMockTarget:
                setattr(self.captureMockTarget, attrname, value)
            # Don't record internally-set module setup when importing modules from packages
            if not type(value) == types.ModuleType and not isinstance(value, ModuleProxy):
                self.captureMockTrafficHandler.recordSetAttribute(self.captureMockProxyName,
                                                                  attrname,
                                                                  value)


class ModuleProxy(PythonProxy):
    def __init__(self, name, trafficHandler, loadModule):
        PythonProxy.__init__(self, name, trafficHandler, None, NameFinder(self))
        self.captureMockTarget = trafficHandler.importModule(name, self, loadModule) 
        self.captureMockModuleLoader = loadModule

    def captureMockLoadRealModule(self):
        self.captureMockTarget = self.captureMockModuleLoader(self.captureMockProxyName)
        return self.captureMockTarget
    

class InstanceProxy(PythonProxy):
    moduleProxy = None
    captureMockTargetClass = None
    def __init__(self, *args, **kw):
        if "captureMockProxyName" in kw:
            # 'Internal' constructor, from above
            PythonProxy.__init__(self, kw.get("captureMockProxyName"), kw.get("captureMockTrafficHandler"),
                                 kw.get("captureMockTarget"), kw.get("captureMockNameFinder"))
            if self.captureMockTarget:
                self.__class__.captureMockTargetClass = self.captureMockTarget.__class__
            self.__class__.captureMockNameFinder = self.captureMockNameFinder
            self.__class__.captureMockTrafficHandler = self.captureMockTrafficHandler
        else:
            # 'External' constructor, from client code
            proxyName = self.__class__.__module__ + "." + self.__class__.__name__
            proxyName, realObj = self.captureMockTrafficHandler.callConstructor(proxyName,
                                                                                self.captureMockTargetClass,
                                                                                self,
                                                                                *args, **kw)
            PythonProxy.__init__(self, proxyName, self.captureMockTrafficHandler,
                                 realObj, self.captureMockNameFinder)

    # Used by mixins of this class and new-style classes
    def __getattribute__(self, attrname):
        if attrname.startswith("captureMock") or attrname in [ "__dict__", "__class__", "__getattr__" ]:
            return object.__getattribute__(self, attrname)
        else:
            return self.__getattr__(attrname)
        
    # In case of new-style objects, which define these in 'object'
    def __str__(self):
        return self.__getattr__("__str__")()

    def __repr__(self):
        return self.__getattr__("__repr__")()

    def __getitem__(self, *args):
        return self.__getattr__("__getitem__")(*args)
