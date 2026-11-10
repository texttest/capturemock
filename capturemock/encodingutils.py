""" Encodings used by CaptureMock. Record files are always UTF-8, so they mean the same on every platform
and Python version. Config (rc) files are read like TextTest's: as the locale encoding unless they have a byte order mark """
import codecs
import locale
import os

# Encoding of all record and replay files
RECORD_ENCODING = "utf-8"

def getLocaleEncoding():
    # The real locale encoding. Unlike getpreferredencoding, this ignores Python's UTF-8 mode,
    # which is on by default from Python 3.15
    if hasattr(locale, "getencoding"): # Python 3.11 and later
        return locale.getencoding()
    else:
        return locale.getpreferredencoding(False)

def getCommandClientEncoding():
    # The encoding the command-line intercept uses to read what we send and write it to the system under test.
    # On Windows it is a frozen executable with an old Python, which uses the locale encoding.
    # Elsewhere it is a script run by this Python, which uses its default encoding.
    return getLocaleEncoding() if os.name == "nt" else locale.getpreferredencoding(False)

def openRecordFile(fileName, mode="r", **kw):
    return open(fileName, mode, encoding=RECORD_ENCODING, **kw)

def openConfigFile(fileName, mode="r"):
    # Config files are in the locale encoding, unless they have a byte order mark
    encoding = getLocaleEncoding()
    if "r" in mode:
        try:
            with open(fileName, "rb") as f:
                start = f.read(4)
        except OSError:
            start = b""
        # Longest first: the UTF-32 LE BOM starts with the UTF-16 LE one
        for bom, bomEncoding in [ (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                                  (codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16") ]:
            if start.startswith(bom):
                encoding = bomEncoding
                break
    return open(fileName, mode, encoding=encoding, errors="replace")

# Payloads such as HTTP and AMQP bodies are assumed to be UTF-8, as is usual nowadays. Anything else is recorded as hex
def decodeBytes(rawBytes):
    try:
        textStr = rawBytes.decode("utf-8") if rawBytes else ""
        return textStr.replace("\r\n", "\n")
    except UnicodeDecodeError:
        return "0x" + rawBytes.hex()

def encodeString(textStr):
    if textStr.startswith("0x"):
        return bytes.fromhex(textStr[2:])
    else:
        rawBytes = textStr.encode("utf-8")
        return rawBytes.replace(b"\n", b"\r\n")
