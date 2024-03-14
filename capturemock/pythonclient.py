""" Actual interception objects used by Python interception mechanism """
import sys, types, inspect

class NameFinder(dict):
    def __init__(self, moduleProxy):
        dict.__init__(self)
        self.moduleProxy = moduleProxy
        self["InstanceProxy"] = InstanceProxy
        self["PythonProxy"] = PythonProxy
        self["ProxyMetaClass"] = ProxyMetaClass
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
        self["Instance"] = self.makeInstance # In case we created a class called Instance...
        return newClass

    def makeMetaClass(self, realMetaClass):
        fullClassName = realMetaClass.__module__ + "." + realMetaClass.__name__
        clsName = realMetaClass.__name__ + "Proxy"
        if clsName not in self.moduleProxy.__dict__:
            classDefStr = "class " + clsName + "(" + fullClassName + ", PythonProxy) : pass"
            self.defineClass(clsName, classDefStr)
        return clsName

    def makeClass(self, className, metaClassName):
        actualClassName = className.split("(")[0]
        moduleDict = self.moduleProxy.__dict__
        if actualClassName in moduleDict:
            return moduleDict[actualClassName]
        if "(" in className:
            classDecl = className.replace("(", "(InstanceProxy, ")
        else:
            classDecl = className + "(InstanceProxy)"
        if sys.version_info[0] == 2:
            classDefStr = "class " + classDecl + " : __metaclass__ = " + metaClassName
        else:
            classDefStr = "class " + classDecl.replace(")", ", metaclass=" + metaClassName + "): pass")
        return self.defineClass(actualClassName, classDefStr)

    def makeInstance(self, className, instanceName):
        return self.moduleProxy.captureMockCreateInstanceProxy(instanceName, classDesc=className)

    def rename(self, oldName, newName):
        proxy = dict.__getitem__(self, oldName)
        proxy.captureMockProxyName = newName
        del self[oldName]
        self[newName] = proxy

    def defineClassLocally(self, classDefStr):
        try:
            exec(classDefStr, self)
        except NameError:
            self.makeNewClasses = True
            self.defineClassLocally(classDefStr)
            self.makeNewClasses = False
        except TypeError as e:
            if "MRO" in str(e):
                self.defineClassLocally(classDefStr.replace("InstanceProxy, ", ""))
            else:
                raise

    def __getitem__(self, name):
        if name not in self:
            if self.makeNewClasses:
                exec("class " + name + ": pass", self)
                self.newClassNames.append(name)
                self.makeNewClasses = False
            else:
                try:
                    exec("import " + name, self)
                except ImportError:
                    pass
        return dict.__getitem__(self, name)


class PythonProxy(object):
    def __init__(self, captureMockProxyName, captureMockTrafficHandler,
                 captureMockTarget, captureMockNameFinder, captureMockCallback=False):
        # Will be passed into real code. Try to minimise the risk of name clashes
        self.captureMockProxyName = captureMockProxyName
        self.captureMockTrafficHandler = captureMockTrafficHandler
        self.captureMockTarget = captureMockTarget
        self.captureMockNameFinder = captureMockNameFinder
        self.captureMockCallback = captureMockCallback
        if "." not in captureMockProxyName and captureMockProxyName not in self.captureMockNameFinder:
            self.captureMockNameFinder[captureMockProxyName] = self

    def __getattr__(self, attrname):
        if attrname in [ "__loader__", "__package__", "__initializing__", "__spec__", "__notes__" ]:
            raise AttributeError("'module' object has no attribute '" + attrname + "'")
        return self.captureMockTrafficHandler.getAttribute(self.captureMockProxyName,
                                                           attrname,
                                                           self,
                                                           self.captureMockTarget)

    def captureMockCreateInstanceProxy(self, proxyName, proxyTarget=None, classDesc=None, captureMockCallback=False):
        if classDesc:
            newClass = self.captureMockMakeClass(classDesc, type(proxyTarget))
        else:
            newClass = PythonProxy
        return newClass(captureMockProxyName=proxyName,
                        captureMockTrafficHandler=self.captureMockTrafficHandler,
                        captureMockTarget=proxyTarget,
                        captureMockNameFinder=self.captureMockNameFinder,
                        captureMockCallback=captureMockCallback)

    def getMetaClass(self, cls):
        metaClass = type(cls)
        classTypes = [ type ]
        if sys.version_info[0] == 2:
            classTypes.append(types.ClassType)
        return metaClass if metaClass not in classTypes else None

    def captureMockMakeClass(self, classDesc, proxyTargetClass):
        metaClassName = "ProxyMetaClass"
        if proxyTargetClass is not None:
            metaClass = self.getMetaClass(proxyTargetClass)
            if metaClass:
                metaClassName = self.captureMockNameFinder.makeMetaClass(metaClass)
        try:
            return self.captureMockNameFinder.makeClass(classDesc, metaClassName)
        except TypeError as e:
            if "metaclass conflict" in str(e):
                startIdx = classDesc.index('(') + 1
                endIdx = classDesc.index(')')
                baseclassDesc = classDesc[startIdx:endIdx]
                for baseClassStr in baseclassDesc.split(","):
                    baseClass = eval(baseClassStr, self.captureMockNameFinder)
                    metaClass = self.getMetaClass(baseClass)
                    if metaClass:
                        metaClassName = self.captureMockNameFinder.makeMetaClass(metaClass)
                        return self.captureMockNameFinder.makeClass(classDesc, metaClassName)
            raise

    def captureMockCreateClassProxy(self, proxyName, proxyTarget, classDesc):
        classProxy = self.captureMockMakeClass(classDesc, proxyTarget)
        classProxy.captureMockNameFinder = self.captureMockNameFinder
        classProxy.captureMockTrafficHandler = self.captureMockTrafficHandler
        classProxy.captureMockTarget = proxyTarget
        classProxy.captureMockClassProxyName = proxyName
        classProxy.captureMockProxyName = proxyName
        classProxy.captureMockCallback = False
        return classProxy

    def captureMockEvaluate(self, response):
        if response.startswith("raise "):
            exec(response, self.captureMockNameFinder)
        else:
            return eval(response, self.captureMockNameFinder)

    def __call__(self, *args, **kw):
        return self.captureMockTrafficHandler.callFunction(self.captureMockProxyName,
                                                           self,
                                                           self.captureMockTarget,
                                                           *args, **kw)

    def __setattr__(self, attrname, value):
        self.__dict__[attrname] = value
        if not attrname.startswith("captureMock") and attrname not in [ "__file__", "__loader__", "__package__", "__spec__" ]:
            if self.captureMockTarget is not None:
                setattr(self.captureMockTarget, attrname, value)
            # Don't record internally-set module setup when importing modules from packages
            if not type(value) == types.ModuleType and not isinstance(value, ModuleProxy):
                self.captureMockTrafficHandler.recordSetAttribute(self.captureMockProxyName,
                                                                  attrname,
                                                                  value)


class ModuleProxy(PythonProxy):
    def __init__(self, name, trafficHandler, loadModule, target=None):
        self.__file__ = __file__
        PythonProxy.__init__(self, name, trafficHandler, target, NameFinder(self))
        if self.captureMockTarget is None:
            self.captureMockTarget = trafficHandler.importModule(name, self, loadModule)
        else:
            # Partial interception. Anything this refers to in the same module should fetch the real version
            self.captureMockNameFinder[name] = self.captureMockTarget
        self.captureMockModuleLoader = loadModule

    def captureMockLoadRealModule(self):
        self.captureMockTarget = self.captureMockModuleLoader(self.captureMockProxyName)
        return self.captureMockTarget


class InstanceProxy(PythonProxy):
    moduleProxy = None
    captureMockTarget = None
    captureMockClassProxyName = None
    def __new__(cls, *args, **kw):
        # Is there an easier way? Seems horribly complicated
        passedPythonProxy = False
        for superCls in inspect.getmro(cls):
            if passedPythonProxy:
                if hasattr(superCls, "__new__"):
                    try:
                        return superCls.__new__(cls)
                    except TypeError:
                        pass
            elif superCls is PythonProxy:
                passedPythonProxy = True

    def __init__(self, *args, **kw):
        if "captureMockProxyName" in kw:
            # 'Internal' constructor, from above
            PythonProxy.__init__(self, kw.get("captureMockProxyName"), kw.get("captureMockTrafficHandler"),
                                 kw.get("captureMockTarget"), kw.get("captureMockNameFinder"))
            if self.captureMockTarget is not None:
                targetClass = self.captureMockTarget.__class__ if hasattr(self.captureMockTarget, "__class__") else type(self.captureMockTarget)
                self.__class__.captureMockTarget = targetClass
            self.__class__.captureMockNameFinder = self.captureMockNameFinder
            self.__class__.captureMockTrafficHandler = self.captureMockTrafficHandler
            self.__class__.captureMockCallback = self.captureMockCallback
        else:
            # 'External' constructor, from client code
            if self.captureMockClassProxyName is not None:
                proxyName = self.captureMockClassProxyName
            else:
                proxyName = self.__class__.__module__ + "." + self.__class__.__name__
            proxyName, realObj = self.captureMockTrafficHandler.callConstructor(proxyName,
                                                                                self.captureMockTarget,
                                                                                self,
                                                                                *args, **kw)
            PythonProxy.__init__(self, proxyName, self.captureMockTrafficHandler,
                                 realObj, self.captureMockNameFinder)
            for method in self.captureMockFindMissingMethods():
                self.captureMockAddInterceptionForDerivedOnly(method)

    def captureMockFindMissingMethods(self):
        if self.captureMockTarget is None:
            return []
        targetMethods = dir(self.captureMockTarget.__class__)
        proxyMethods = dir(super(self.__class__, self))
        proxyClassMethods = dir(InstanceProxy)
        allToRemove = targetMethods + proxyMethods + proxyClassMethods
        allToRemove.append("__metaclass__") # seems to be an exception...
        selfMethods = dir(self.__class__)
        return [m for m in selfMethods if m not in allToRemove]

    def captureMockAddInterceptionForDerivedOnly(self, methodName):
        newProxyName = self.captureMockProxyName + "." + methodName
        newTarget = object.__getattribute__(self, methodName)
        newProxy = self.captureMockCreateInstanceProxy(newProxyName, newTarget, captureMockCallback=True)
        setattr(self.captureMockTarget, methodName, newProxy)

    # Used by mixins of this class and new-style classes
    def __getattribute__(self, attrname):
        if attrname.startswith("captureMock") or \
               attrname in ["__file__",
                            "__dict__",
                            "__class__",
                            "__getattr__",
                            "__members__",
                            "__methods__",
                            "__name__",
                            "__cause__", 
                            "__context__"] or \
               self.captureMockDefinedInNonInterceptedSubclass(attrname):
            return object.__getattribute__(self, attrname)
        else:
            return self.__getattr__(attrname)

    def captureMockDefinedInNonInterceptedSubclass(self, attrname):
        if attrname not in dir(self.__class__):
            return False

        firstBaseClass = self.captureMockGetFirstInterceptedBaseClass()
        return attrname not in dir(firstBaseClass)

    def captureMockGetFirstInterceptedBaseClass(self):
        allbases = inspect.getmro(self.__class__)
        proxyPos = allbases.index(PythonProxy) # should always exist
        return allbases[proxyPos + 1] # at least "object" should be there failing anything else

    def captureMockConvertToBoolean(self, methodName):
        try:
            return self.__getattr__(methodName)()
        except AttributeError:
            try:
                return bool(self.__getattr__("__len__")())
            except AttributeError:
                return True

    # In case of new-style objects. Special methods have no means of being intercepted. See
    # http://docs.python.org/reference/datamodel.html#new-style-special-lookup
    def __str__(self):
        return self.__getattr__("__str__")()

    def __repr__(self):
        return self.__getattr__("__repr__")()

    def __bool__(self):
        return self.captureMockConvertToBoolean("__bool__")

    def __nonzero__(self):
        return self.captureMockConvertToBoolean("__nonzero__")

    def __iter__(self):
        return self.__getattr__("__iter__")()

    def __getitem__(self, *args):
        return self.__getattr__("__getitem__")(*args)

    # For iterators. Doesn't do any harm being here in general, and otherwise an intercepted
    # iterator will not be recognised. At worst this might replace one exception with another
    def __next__(self):
        return self.__getattr__("__next__")()

    def next(self):
        return self.__getattr__("next")()

    # For classes that subclass collections.MutableMapping
    def __setitem__(self, key, val):
        return self.__getattr__("__setitem__")(key, val)

    def __delitem__(self, key):
        return self.__getattr__("__delitem__")(key)

    def __len__(self):
        return self.__getattr__("__len__")()


class ProxyMetaClass(type, PythonProxy):
    pass
