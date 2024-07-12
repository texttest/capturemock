
from locale import getpreferredencoding
import os

def decodeBytes(rawBytes):
    try:
        textStr = rawBytes.decode(getpreferredencoding()) if rawBytes else ""
        return textStr.replace("\r\n", "\n")
    except UnicodeDecodeError:
        return "0x" + rawBytes.hex()

def encodeString(textStr):
    if textStr.startswith("0x"):
        return bytes.fromhex(textStr[2:])
    else:
        rawBytes = textStr.encode(getpreferredencoding())
        return rawBytes.replace(b"\n", b"\r\n")
