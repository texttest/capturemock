
""" Very basic interface for appending to a file. Server version much more complex """ 
import os

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file
        self.lastTruncationPoint = None
        self.recordedSinceTruncationPoint = []
        self._writeFile = open(file, "a", buffering=8192) if file else None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        self.close()

    def close(self):
        if self._writeFile is not None:
            self._writeFile.flush()
            self._writeFile.close()
            self._writeFile = None

    def record(self, text, truncationPoint=False):
        if self._writeFile:
            if truncationPoint:
                self._writeFile.flush()
                self.lastTruncationPoint = os.path.getsize(self.file)
                self.recordedSinceTruncationPoint = []                
            if self.lastTruncationPoint is not None:
                self.recordedSinceTruncationPoint.append(text)
            self._writeFile.write(text)
            self._writeFile.flush()
            
    def rerecord(self, oldText, newText):
        if self._writeFile:
            self._writeFile.truncate(self.lastTruncationPoint)
            for text in self.recordedSinceTruncationPoint:
                self._writeFile.write(text.replace(oldText, newText))
            self._writeFile.flush()
            self.lastTruncationPoint = None
            self.recordedSinceTruncationPoint = []
