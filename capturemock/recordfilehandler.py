
""" Very basic interface for appending to a file. Server version much more complex """ 

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file
        self.lastTruncationPoint = None
        self.recordedSinceTruncationPoint = []

    def record(self, text, truncationPoint=False):
        if self.file:
            writeFile = open(self.file, "a")
            if truncationPoint:
                self.lastTruncationPoint = writeFile.tell()
                self.recordedSinceTruncationPoint = []
            if self.lastTruncationPoint is not None:
                self.recordedSinceTruncationPoint.append(text)
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
