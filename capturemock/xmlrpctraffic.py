import sys
from locale import getpreferredencoding
import xmlrpc.client as xmlrpclib  # name 'xmlrpclib' is also used by eval() of recorded responses
from xmlrpc.server import SimpleXMLRPCServer
from capturemock.clientservertraffic import ClientSocketTraffic, ServerTraffic, ServerStateTraffic

class XmlRpcClientTraffic(ClientSocketTraffic):
    def __init__(self, text="", responseFile=None, rcHandler=None, method=None, params=None):
        if method is not None:
            self.method = method
            self.params = params
            text = self.method + repr(params).replace(",)", ")")
        ClientSocketTraffic.__init__(self, text, None, rcHandler)
        if method is None: # replay 
            self.method, paramText = text.rstrip().split("(", 1)
            paramText = "(" + paramText
            paramText = self.applyAlterationVariables(paramText)
            if "," not in paramText and paramText != "()":
                paramText = paramText[:-1] + ",)" # make proper tuple output
            self.params = eval(paramText)

    def forwardToServer(self):
        try:
            responseObject = getattr(self.destination, self.method)(*self.params)
        except xmlrpclib.Fault as e:
            responseObject = e

        text = self.applyAlterations(self.fixMultilineStrings(responseObject))
        return [ XmlRpcServerTraffic(text=text, responseObject=responseObject) ]

    def getXmlRpcResponse(self):
        return "" # not a response in the xmlrpc sense...


class XmlRpcServerTraffic(ServerTraffic):
    def __init__(self, text="", responseFile=None, rcHandler=None, responseObject=None):
        if responseObject is not None:
            self.responseObject = responseObject
            if isinstance(responseObject, xmlrpclib.Fault):
                text = "raise xmlrpclib.Fault(" + repr(responseObject.faultCode) + ", " + repr(responseObject.faultString) + ")"
        else:
            raiseException = text.startswith("raise ")
            if raiseException:
                text = text[6:]
            self.responseObject = eval(text)
        ServerTraffic.__init__(self, text, None, rcHandler)

    def getXmlRpcResponse(self):
        if isinstance(self.responseObject, xmlrpclib.Fault):
            raise self.responseObject
        else:
            return self.responseObject


class XmlRpcServerStateTraffic(ServerStateTraffic):
    def __init__(self, dest, rcHandler):
        ServerStateTraffic.__init__(self, "setServerLocation(<address>)", xmlrpclib.ServerProxy(dest), None, rcHandler)


class XmlRpcTrafficServer(SimpleXMLRPCServer):
    @classmethod
    def createServer(cls, address, port, dispatcher):
        server = cls((address, port), logRequests=False, use_builtin_types=True)
        server.register_instance(XmlRpcDispatchInstance(dispatcher))
        return server

    def run(self):
        self.serve_forever()

    def getAddress(self):
        host, port = self.socket.getsockname()
        # hardcode http? Seems to be what you get...
        return "http://" + host + ":" + str(port)

    def setShutdownFlag(self):
        # Not supposed to know about this variable, implementation detail of SocketServer
        # But seems like the only way to shutdown the server from within a request
        # Otherwise we have to start using ForkingMixin, ThreadingMixin etc which seems like overkill just to access a variable
        self._BaseServer__shutdown_request = True

    @staticmethod
    def getTrafficClasses(incoming):
        if incoming:
            return [ XmlRpcServerStateTraffic, XmlRpcClientTraffic ]
        else:
            return [ XmlRpcServerTraffic, XmlRpcClientTraffic ]


class XmlRpcDispatchInstance:
    requestCount = 0
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher

    def convertBytes(self, param):
        return str(param, getpreferredencoding()) if isinstance(param, bytes) else param

    def _dispatch(self, method, binparams):
        params = tuple([ self.convertBytes(param) for param in binparams ])
        try:
            self.dispatcher.diag.info("Received XMLRPC traffic " + method + repr(params))
            XmlRpcDispatchInstance.requestCount += 1
            if method == "shutdownCaptureMockServer":
                self.dispatcher.server.setShutdownFlag()
                return ""
            elif method == "setServerLocation":
                traffic = XmlRpcServerStateTraffic(params[0], rcHandler=self.dispatcher.rcHandler)
            else:
                traffic = XmlRpcClientTraffic(method=method, params=params, rcHandler=self.dispatcher.rcHandler)
            responses = self.dispatcher.process(traffic, self.requestCount)
            return responses[0].getXmlRpcResponse() if responses else ""
        except xmlrpclib.Fault:
            raise
        except:
            sys.stderr.write("Exception thrown while handling XMLRPC input :\n")
            type, value, traceback = sys.exc_info()
            from traceback import format_exception
            exceptionString = "".join(format_exception(type, value, traceback))
            sys.stderr.write(exceptionString)
            return ""
