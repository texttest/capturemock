
""" Defining the base traffic class which the useful traffic classes inherit """

import re, os
from pprint import pformat
try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

class BaseTraffic(object):
    alterationVariables = OrderedDict()
    def __init__(self, text, rcHandler=None):
        self.text = text
        self.alterations = {}
        if rcHandler:
            self.diag = rcHandler.diag
            for alterStr in rcHandler.getList("alterations", self.getAlterationSectionNames()):
                toFind = os.path.expandvars(rcHandler.get("match_pattern", [ alterStr ]))
                toReplace = rcHandler.get("replacement", [ alterStr ])
                if toFind and toReplace is not None:
                    self.alterations[re.compile(toFind)] = toReplace

    def applyAlterations(self, text):
        return self._applyAlterations(text, self.alterations)

    def applyAlterationVariables(self, text):
        return self._applyAlterations(text, self.alterationVariables)

    def _applyAlterations(self, text, alterations):
        class AlterationReplacer:
            def __init__(rself, repl): #@NoSelf
                rself.repl = repl

            def __call__(rself, match): #@NoSelf
                if rself.repl.startswith("$"):
                    return self.storeAlterationVariable(rself.repl, match.group(0))
                else:
                    return match.expand(rself.repl)

        # Reverse it for the alteration variables, we may add newer ones as we go along, want to check those first...
        for regex, repl in reversed(list(alterations.items())):
            text = regex.sub(AlterationReplacer(repl), text)
        return text

    @staticmethod
    def findNextNameCandidate(name):
        parts = name.split("_")
        if len(parts) > 1 and parts[-1].isdigit():
            parts[-1] = str(int(parts[-1]) + 1)
            return "_".join(parts)
        else:
            return name + "_2"
        
    def storeAlterationVariable(self, repl, matched):
        replText = repl
        regex = re.compile(replText.replace("$", "\\$"))
        while regex in self.alterationVariables and self.alterationVariables[regex] != matched:
            replText = self.findNextNameCandidate(replText)
            regex = re.compile(replText.replace("$", "\\$"))
            
        self.diag.info("Adding alteration variable for " + replText + " = " + matched)
        self.alterationVariables[regex] = matched
        return replText

    def getAlterationSectionNames(self):
        return [ "general" ]

    def findPossibleFileEdits(self):
        return []
    
    def hasInfo(self):
        return len(self.text) > 0

    def isMarkedForReplay(self, *args):
        return True # Some things can't be disabled and hence can't be added on piecemeal afterwards

    def getDescription(self):
        return self.direction + self.typeId + ":" + self.text

    def makesAsynchronousEdits(self):
        return False
    
    def record(self, recordFileHandler, *args, **kw):
        if not self.hasInfo():
            return
        desc = self.getDescription()
        if not desc.endswith("\n"):
            desc += "\n"
        recordFileHandler.record(desc, *args, **kw)

    def findQuote(self, out):
        bestPos, bestQuoteChar = None, None
        for quoteChar in "'\"":
            pos = out.find(quoteChar, 0, 2)
            if pos != -1 and (bestPos is None or pos < bestPos):
                bestPos = pos
                bestQuoteChar = quoteChar
        return bestPos, bestQuoteChar

    def fixMultilineStrings(self, arg):
        out = pformat(arg, width=130)
        # Replace linebreaks but don't mangle e.g. Windows paths
        # This won't work if both exist in the same string - fixing that requires
        # using a regex and I couldn't make it work [gjb 100922]
        if "\\n" in out and "\\\\n" not in out:
            pos, quoteChar = self.findQuote(out)
            if pos is not None:
                return self.makeMultiline(out, pos, quoteChar)
        return out

    def makeMultiline(self, out, pos, quoteChar):
        endPos = out.rfind(quoteChar, -3, -1)
        return out[:pos] + quoteChar * 2 + out[pos:endPos].replace("\\n", "\n").replace("\\t", "\t") + quoteChar * 2 + out[endPos:]

    
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

