
""" Very basic interface for appending to a file. Server version much more complex """ 

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file

    def record(self, text):
        if self.file:
            writeFile = open(self.file, "a")
            writeFile.write(text)
            writeFile.flush()
            writeFile.close()
