#!/usr/bin/env python

import os, sys

def removeSelfFromPath():
   # On Windows, this file is capturemock.py, which means it can get imported!
   # Remove ourselves and cause a reimport in this case
   binDir = os.path.normpath(os.path.dirname(os.path.abspath(__file__))).replace("/", "\\").lower()
   for path in sys.path:
      if binDir == os.path.normpath(path).replace("/", "\\").lower():
         sys.path.remove(path)
         break

if os.name == "nt":
   removeSelfFromPath()

if __name__ == "__main__":
   from capturemock import commandline
   commandline()
else:
   del sys.modules["capturemock"]
   from capturemock import *
