
""" Defining the base traffic class which the useful traffic classes inherit """

import re

class BaseTraffic(object):
    def __init__(self, text, rcHandler=None):
        self.text = text
        self.alterations = {}
        if rcHandler:
            for alterStr in rcHandler.getList("alterations", self.getAlterationSectionNames()):
                toFind = rcHandler.get("match_pattern", [ alterStr ])
                toReplace = rcHandler.get("replacement", [ alterStr ])
                if toFind and toReplace is not None:
                    self.alterations[re.compile(toFind)] = toReplace

    def applyAlterations(self, text):
        for regex, repl in self.alterations.items():
            text = regex.sub(repl, text)
        return text

    def getAlterationSectionNames(self):
        return [ "general" ]

    def findPossibleFileEdits(self):
        return []
    
    def hasInfo(self):
        return len(self.text) > 0

    def isMarkedForReplay(self, replayItems):
        return True # Some things can't be disabled and hence can't be added on piecemeal afterwards

    def getDescription(self):
        return self.direction + self.typeId + ":" + self.text

    def makesAsynchronousEdits(self):
        return False
    
    def record(self, recordFileHandler, *args):
        if not self.hasInfo():
            return
        desc = self.getDescription()
        if not desc.endswith("\n"):
            desc += "\n"
        recordFileHandler.record(desc, *args)

    
class Traffic(BaseTraffic):
    def __init__(self, text, responseFile, *args):
        super(Traffic, self).__init__(text, *args)
        self.responseFile = responseFile
    
    def write(self, message):
        from socket import error
        if self.responseFile:
            try:
                self.responseFile.write(message)
            except error:
                # The system under test has died or is otherwise unresponsive
                # Should handle this, probably. For now, ignoring it is better than stack dumps
                pass
                
    def forwardToDestination(self):
        self.write(self.text)
        if self.responseFile:
            self.responseFile.close()
        return []

    def filterReplay(self, trafficList):
        return trafficList
    
class ResponseTraffic(Traffic):
    direction = "->"

