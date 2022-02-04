
from locale import getpreferredencoding
import os

def decodeBytes(rawBytes):
    try:
        textStr = rawBytes.decode(getpreferredencoding()) if rawBytes else ""
        if os.name == "nt":
            return textStr.replace(os.linesep, "\n")
        else:
            return textStr
    except UnicodeDecodeError:
        return "0x" + rawBytes.hex()

def encodeString(textStr):
    rawBytes = textStr.encode(getpreferredencoding())
    if os.name == "nt":
        return rawBytes.replace(b"\n", b"\r\n")
    else:
        return rawBytes