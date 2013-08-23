
""" Very basic interface for appending to a file. Server version much more complex """ 

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file
        self.delayedLines = []

    def record(self, text, delay=False):
        if not self.file:
            return
        
        if delay:
            self.delayedLines.append(text)
        else:
            self.writeToFile(text)
            for delayedText in self.delayedLines:
                self.writeToFile(delayedText)
            self.delayedLines = []
            
    def writeToFile(self, text):
        writeFile = open(self.file, "a")
        writeFile.write(text)
        writeFile.flush()
        writeFile.close()
