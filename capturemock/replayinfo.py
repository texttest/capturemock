""" Module to manage the information in the file and return appropriate matches """

import logging, difflib, re
from pprint import pformat
try: # Python 2.7, Python 3.x
    from collections import OrderedDict
except ImportError: # Python 2.6 and earlier
    from ordereddict import OrderedDict

from capturemock import config, id_mapping

class ReplayInfo:
    def __init__(self, mode, replayFile, rcHandler):
        self.responseMap = OrderedDict()
        self.diag = logging.getLogger("Replay")
        self.replayItems = set()
        self.replayAll = mode == config.REPLAY
        self.exactMatching = rcHandler.getboolean("use_exact_matching", [ "general" ], False)
        self.idFinder = None
        self.idMap = {}
        self.prevResponseMapKeys = set()
        if replayFile:
            self.idFinder = id_mapping.IdFinder(rcHandler, "id_pattern_client")
            trafficList = self.readIntoList(replayFile)
            self.parseTrafficList(trafficList)
            items = self.makeCommandItems(rcHandler.getIntercepts("command line")) + \
                    self.makePythonItems(rcHandler.getIntercepts("python"))
            self.replayItems = self.filterForReplay(items, trafficList)

    @staticmethod
    def filterForReplay(itemInfo, lines):
        newItems = set()
        for line in lines:
            for item, regexp in itemInfo:
                if regexp.search(line):
                    newItems.add(item)
        return newItems

    @staticmethod
    def makeCommandItems(commands):
        return [ (command, re.compile("<-CMD:([^ ]* )*" + command + "( [^ ]*)*")) for command in commands ]

    @staticmethod
    def makePythonItems(pythonAttrs):
        return [ (attr, re.compile("<-PYT:(import )?" + attr)) for attr in pythonAttrs ]

    def isActiveForAll(self):
        return len(self.responseMap) > 0 and self.replayAll

    def isActiveFor(self, traffic):
        if len(self.responseMap) == 0:
            return False
        elif self.replayAll:
            return True
        else:
            return traffic.isMarkedForReplay(self.replayItems, list(self.responseMap.keys()))

    def getTrafficLookupKey(self, trafficStr):
        # If we're matching server communications it means we're 'playing client'
        # In this case we should just send all our stuff in order and not worry about matching things.
        return "<-SRV" if trafficStr.startswith("<-SRV") else trafficStr

    def responseCompleted(self, currResponseHandlers, indentLevel, fromSUT):
        prevIndentLevel = len(currResponseHandlers) - 1
        if indentLevel < prevIndentLevel:
            return True
        elif indentLevel == prevIndentLevel:
            _, prevFromSUT = currResponseHandlers[-1]
            return fromSUT == prevFromSUT
        else:
            return False

    def parseTrafficList(self, trafficList):
        currResponseHandlers = []
        for trafficStr in trafficList:
            prefix = trafficStr.split(":")[0]
            indentLevel = int(len(prefix) / 2) - 2
            fromSUT = prefix.startswith("<-")
            while self.responseCompleted(currResponseHandlers, indentLevel, fromSUT):
                currResponseHandlers.pop()

            if currResponseHandlers and (not fromSUT or indentLevel % 2 == 1):
                responseHandler, _ = currResponseHandlers[-1]
                responseHandler.addResponse(trafficStr)
            if fromSUT or indentLevel > len(currResponseHandlers) - 1:
                currTrafficIn = self.getTrafficLookupKey(trafficStr.strip())
                responseHandler = self.responseMap.get(currTrafficIn)
                if responseHandler:
                    responseHandler.newResponse()
                    if prefix.endswith("PYT") and not "(" in trafficStr:
                        self.registerIntermediateCalls(responseHandler)
                else:
                    responseHandler = ReplayedResponseHandler()
                    self.responseMap[currTrafficIn] = responseHandler
                if indentLevel > len(currResponseHandlers) - 1:
                    currResponseHandlers.append((responseHandler, fromSUT))
                else:
                    currResponseHandlers[-1] = responseHandler, fromSUT
        self.diag.debug("Replay info " + pformat(self.responseMap))

    def registerIntermediateCalls(self, currResponseHandler):
        intermediate = []
        for trafficIn in reversed(self.responseMap):
            responseHandler = self.responseMap[trafficIn]
            if responseHandler is currResponseHandler:
                break
            if "(" in trafficIn:
                intermediate.insert(0, responseHandler)
        currResponseHandler.addIntermediate(intermediate)

    @classmethod
    def readIntoList(cls, replayFile):
        trafficList = []
        currTraffic = ""
        with open(replayFile, newline=None) as f:
            for line in f:
                prefix = line.split(":")[0]
                if len(prefix) < 10 and (prefix.startswith("<-") or prefix[-5:-3] == "->"):
                    if currTraffic:
                        trafficList.append(currTraffic.rstrip("\n"))
                    currTraffic = ""
                currTraffic += line
        if currTraffic:
            trafficList.append(currTraffic.rstrip("\n"))
        return trafficList

    def readReplayResponses(self, traffic, allClasses, exact=False):
        # We return the response matching the traffic in if we can, otherwise
        # the one that is most similar to it
        if not traffic.hasInfo():
            return []

        responseMapKey = self.getResponseMapKey(traffic, exact)
        if responseMapKey:
            replayId, recordId = self.makeIdMapping(traffic, responseMapKey)
            if traffic.canModifyServer():
                self.prevResponseMapKeys.clear()
            duplicate = not traffic.hasRepeatsInReplay() and responseMapKey in self.prevResponseMapKeys
            self.prevResponseMapKeys.add(responseMapKey)
            return self.responseMap[responseMapKey].makeResponses(allClasses, replayId, recordId, duplicate)
        else:
            return []
        
    def makeIdMapping(self, traffic, replayTrafficDesc):
        recordId, replayId = None, None
        if self.idFinder:
            recordId = self.idFinder.extractIdFromText(traffic.text)
            if recordId:
                replayTrafficText = replayTrafficDesc.split(":", 1)[-1]
                replayId = self.idFinder.extractIdFromText(replayTrafficText)
                if replayId not in self.idMap:
                    new_map = { replayId: recordId }
                    id_mapping.make_id_alterations_rc_file(new_map)
                    self.idMap.update(new_map)
        
        return replayId, recordId

    def findResponseToTrafficStartingWith(self, prefix):
        for currDesc, responseHandler in self.responseMap.items():
            _, text = currDesc.split(":", 1)
            if text.startswith(prefix):
                response = responseHandler.getFirstResponse()
                if response:
                    return response[6:]

    def getResponseMapKey(self, traffic, exact):
        desc = self.getTrafficLookupKey(traffic.getDescription())
        self.diag.debug("Trying to match '" + desc + "'")
        if desc in self.responseMap:
            self.diag.debug("Found exact match")
            return desc
        elif not exact:
            if self.exactMatching:
                raise config.CaptureMockReplayError("Could not find any replay request matching '" + desc + "'")
            else:
                return self.findBestMatch(desc)

    def findBestMatch(self, desc):
        descWords = self.getWords(desc)
        bestMatch = None
        bestMatchInfo = set(), 100000
        for currDesc, responseHandler in self.responseMap.items():
            if self.sameType(desc, currDesc):
                descToCompare = currDesc
                self.diag.debug("Comparing with '" + descToCompare + "'")
                matchInfo = self.getWords(descToCompare), responseHandler.getUnmatchedResponseCount()
                if self.isBetterMatch(matchInfo, bestMatchInfo, descWords):
                    bestMatchInfo = matchInfo
                    bestMatch = currDesc

        if bestMatch is not None:
            self.diag.debug("Best match chosen as '" + bestMatch + "'")
            return bestMatch

    def sameType(self, desc1, desc2):
        return desc1[2:5] == desc2[2:5]

    def getWords(self, desc):
        # Heuristic decisions trying to make the best of inexact matches
        separators = [ "/", "(", ")", "\\", None ] # the last means whitespace...
        return self._getWords(desc, separators)

    def _getWords(self, desc, separators):
        if len(separators) == 0:
            return [ desc ]

        words = []
        for part in desc.split(separators[0]):
            words += self._getWords(part, separators[1:])
        return words

    def getMatchingBlocks(self, list1, list2):
        matcher = difflib.SequenceMatcher(None, list1, list2)
        return list(matcher.get_matching_blocks())

    def commonElementCount(self, blocks):
        return sum((block.size for block in blocks))

    def nonMatchingSequenceCount(self, blocks):
        if len(blocks) > 1 and self.lastBlockReachesEnd(blocks):
            return len(blocks) - 2
        else:
            return len(blocks) - 1

    def lastBlockReachesEnd(self, blocks):
        return blocks[-2].a + blocks[-2].size == blocks[-1].a and \
               blocks[-2].b + blocks[-2].size == blocks[-1].b

    def isBetterMatch(self, info1, info2, targetWords):
        words1, unmatchedCount1 = info1
        words2, unmatchedCount2 = info2
        blocks1 = self.getMatchingBlocks(words1, targetWords)
        blocks2 = self.getMatchingBlocks(words2, targetWords)
        common1 = self.commonElementCount(blocks1)
        common2 = self.commonElementCount(blocks2)
        self.diag.debug("Words in common " + repr(common1) + " vs " + repr(common2))
        if common1 > common2:
            return True
        elif common1 < common2:
            return False

        nonMatchCount1 = self.nonMatchingSequenceCount(blocks1)
        nonMatchCount2 = self.nonMatchingSequenceCount(blocks2)
        self.diag.debug("Non matching sequences " + repr(nonMatchCount1) + " vs " + repr(nonMatchCount2))
        if nonMatchCount1 < nonMatchCount2:
            return True
        elif nonMatchCount1 > nonMatchCount2:
            return False

        self.diag.debug("Unmatched count difference " + repr(unmatchedCount1) + " vs " + repr(unmatchedCount2))
        return unmatchedCount1 > unmatchedCount2


# Need to handle multiple replies to the same question
class ReplayedResponseHandler:
    def __init__(self):
        self.timesChosen = 0
        self.responses = [[]]
        self.intermediateHandlers = []

    def __repr__(self):
        return repr(self.responses)

    def addIntermediate(self, handlers):
        self.intermediateHandlers.append(handlers)

    def newResponse(self):
        self.responses.append([])

    def addResponse(self, trafficStr):
        self.responses[-1].append(trafficStr)

    def allIntermediatesCalled(self):
        return all((handler.timesChosen for handler in self.intermediateHandlers[self.timesChosen - 1 ]))

    def getCurrentStrings(self, responseIndex):
        if self.intermediateHandlers:
            if responseIndex == 0:
                return self.responses[0], 1
            elif self.allIntermediatesCalled():
                moreHandlers = responseIndex < len(self.intermediateHandlers)
                return self.responses[responseIndex], int(moreHandlers)
            else:
                return self.responses[responseIndex - 1], 0
        elif responseIndex < len(self.responses):
            currStrings = self.responses[responseIndex]
        else:
            currStrings = self.responses[0]
        return currStrings, 1

    def getFirstResponse(self):
        responses, _ = self.getCurrentStrings(self.timesChosen)
        if len(responses):
            return responses[0]

    def getUnmatchedResponseCount(self):
        return len(self.responses) - self.timesChosen

    def makeResponses(self, allClasses, replayId, recordId, duplicate):
        responseIndex = self.timesChosen - 1 if (duplicate and self.timesChosen > 0) else self.timesChosen
        trafficStrings, increment = self.getCurrentStrings(responseIndex)
        responses = []
        for trafficStr in trafficStrings:
            prefix, text = trafficStr.split(":", 1)
            trafficType = prefix[-3:]
            for trafficClass in allClasses:
                if trafficClass.typeId == trafficType:
                    if replayId and recordId:
                        text = text.replace(replayId, recordId)
                    responses.append((trafficClass, text))
        if not duplicate:
            self.timesChosen += increment
        return responses
    

def filterFileForReplay(itemInfo, replayFile):
    with open(replayFile, newline=None) as f:
        return ReplayInfo.filterForReplay(itemInfo, f)

def filterCommands(commands, replayFile):
    return filterFileForReplay(ReplayInfo.makeCommandItems(commands), replayFile)

def filterPython(pythonAttrs, replayFile):
    return filterFileForReplay(ReplayInfo.makePythonItems(pythonAttrs), replayFile)
