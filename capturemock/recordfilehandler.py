
""" Very basic interface for appending to a file. Server version much more complex """ 

import os

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file
        self.fileExisted = os.path.isfile(self.file)

    def record(self, text):
        if self.fileExisted:
            os.remove(self.file)
            self.fileExisted = False

        writeFile = open(self.file, "a")
        writeFile.write(text)
        writeFile.flush()
        writeFile.close()
