
""" Very basic interface for appending to a file. Server version much more complex """ 
import os

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file
        self.lastTruncationPoint = None
        self.recordedSinceTruncationPoint = []

    def record(self, text, truncationPoint=False):
        if self.file:
            if truncationPoint:
                self.lastTruncationPoint = os.path.getsize(self.file)
                self.recordedSinceTruncationPoint = []                
            if self.lastTruncationPoint is not None:
                self.recordedSinceTruncationPoint.append(text)
            writeFile = open(self.file, "a")
            writeFile.write(text)
            writeFile.flush()
            writeFile.close()
            
    def rerecord(self, oldText, newText):
        if self.file:
            writeFile = open(self.file, "a")
            writeFile.truncate(self.lastTruncationPoint)
            for text in self.recordedSinceTruncationPoint:
                writeFile.write(text.replace(oldText, newText))
            writeFile.flush()
            writeFile.close()
            self.lastTruncationPoint = None
            self.recordedSinceTruncationPoint = []
