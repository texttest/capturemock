
from locale import getpreferredencoding
import os

def decodeBytes(rawBytes):
    textStr = rawBytes.decode(getpreferredencoding()) if rawBytes else ""
    if os.name == "nt":
        return textStr.replace("\r\n", "\n")
    else:
        return textStr

def encodeString(textStr):
    rawBytes = textStr.encode(getpreferredencoding())
    if os.name == "nt":
        return rawBytes.replace(b"\n", b"\r\n")
    else:
        return rawBytes