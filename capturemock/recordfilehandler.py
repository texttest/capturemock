
""" Very basic interface for appending to a file. Server version much more complex """ 

import os

class RecordFileHandler(object):
    def __init__(self, file):
        self.file = file
        if os.path.isfile(self.file):
            os.remove(self.file)

    def record(self, text):
        writeFile = open(self.file, "a")
        writeFile.write(text)
        writeFile.flush()
        writeFile.close()
