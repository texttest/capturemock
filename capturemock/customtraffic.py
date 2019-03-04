
""" Traffic whose format and real behaviour is defined in a custom client """

from capturemock import traffic

class CustomTraffic(traffic.Traffic):
    direction = "<-"
    typeId = "CAL"
    socketId = "SUT_CUSTOM"
    def __init__(self, inText, *args):
        parts = inText.split(":SUT_SEP:")
        self.responseText = parts[1] if len(parts) > 1 else ""
        traffic.Traffic.__init__(self, parts[0], *args)
        self.text = self.applyAlterations(self.text)
        
    def forwardToDestination(self):
        if self.responseText:
            responseText = self.applyAlterations(self.responseText)
            return [ CustomResponseTraffic(responseText, self.responseFile) ]
        else:
            return []

class CustomResponseTraffic(traffic.Traffic):
    typeId = "RET"
    direction = "->"
    def write(self, message):
        # Don't send newline which we added for readability only
        traffic.Traffic.write(self, message.rstrip())
    

def getTrafficClasses(incoming):
    if incoming:    
        return [ CustomTraffic ]
    else:
        return [ CustomResponseTraffic ]
