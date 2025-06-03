
from capturemock import clientservertraffic
from datetime import datetime
import sys, struct, socket
from pprint import pformat
import logging
        
def get_logger():
    return logging.getLogger("Binary TCP Traffic")
        
class BinaryClientSocketTraffic(clientservertraffic.ClientSocketTraffic):
    serverConverter = None
    @classmethod
    def getServerConverter(cls, rcHandler, reset=False):
        if (reset or cls.serverConverter is None) and cls.destination is not None:
            sock = cls.connectServerSocket()
            diag = get_logger()
            if sock:
                diag.debug("Connected server %s", cls.destination)
                if cls.serverConverter is not None:
                    cls.serverConverter.update_socket(sock)
                else:
                    cls.serverConverter = BinaryTrafficConverter(rcHandler, sock)
            else:
                diag.debug("Failed to connect server %s", cls.destination)
        return cls.serverConverter
    
    def __init__(self, text, responseFile, rcHandler=None, payload=None, internal=False, **kw):
        clientservertraffic.ClientSocketTraffic.__init__(self, text, responseFile, rcHandler=rcHandler, **kw)
        self.payload = payload if text == self.text else None
        self.rcHandler = rcHandler
        self.internal = internal
        
    def forwardToServer(self):
        if not self.internal:
            conv = self.getServerConverter(self.rcHandler)
            if conv:
                if self.payload is None:
                    conv.convert_and_send(self.text)
                else:
                    conv.send_payload(self.payload)
        return []
    
class BinaryServerSocketTraffic(clientservertraffic.ServerTraffic):
    def __init__(self, text, responseFile, rcHandler=None):
        ts = self.get_timestamp(rcHandler)
        super(BinaryServerSocketTraffic, self).__init__(text, responseFile, rcHandler=rcHandler, timestamp=ts)

    def forwardToDestination(self):
        return []

def toString(data, fixNewLines=False):
    if isinstance(data, tuple): # produced by variable types, assume the key data is at the end
        return toString(data[-1])
    elif isinstance(data, bytes):
        try:
            text = data.decode()
            return text.replace("\r", "<CR>").replace("\n", "<LF>") if fixNewLines else text
        except UnicodeDecodeError:
            return "0x" + data.hex()
    else:
        return str(data)

def fromString(data, stringOnly=False):
    if isinstance(data, str):
        if data.startswith("0x"):
            return bytes.fromhex(data[2:])
        elif not stringOnly and data.isdigit():
            return int(data)
        else:
            return data.encode()
    else:
        return data

class BinaryTrafficConverter:
    def __init__(self, rcHandler, sock):
        self.rcHandler = rcHandler
        self.header_timeout = rcHandler.getfloat("header_timeout", [ "general" ], 0.5)
        self.update_socket(sock)
        self.headerConverter = BinaryMessageConverter(rcHandler, "tcp_header")
        self.footerConverter = BinaryMessageConverter(rcHandler, "tcp_footer")
        self.text = None
        self.footer = None
        self.headers = []
        self.header_fields_list = []
        self.bodies = []
        self.diag = get_logger()

    def update_socket(self, sock):
        self.socket = sock
        if sock:
            sock.settimeout(self.header_timeout)

    def get_payload(self):
        payload = b""
        for i, header in enumerate(self.headers):
            payload += header
            if i < len(self.bodies):
                payload += self.bodies[i]
        if self.footer:
            payload += self.footer
        return payload

    def read_text_until_newline(self, header):
        rawBytes = header
        while not rawBytes.endswith(b"\n"):
            rawBytes += self.socket.recv(1)
        return rawBytes.decode()
    
    def receive_data(self, length):
        data = self.socket.recv(length)
        if len(data) >= length:
            return data
        self.diag.debug("Received partial data of length %s", len(data))
        remaining = length - len(data)
        while remaining > 0:
            curr_data = self.socket.recv(remaining)
            data += curr_data
            remaining -= len(curr_data)
            self.diag.debug("Received additional data, still need %d bytes", remaining)

        return data

    def is_capturemock_header(self, header):
        lengthCheck = min(len(header), 8)
        for msg in [ b"SUT_SERV", b"TERMINAT", b"CAPTUREM" ]:
            if msg[:lengthCheck] == header[:lengthCheck]:
                return True
        return False
    
    def receive_body_until_footer(self, footer_length):
        body = b""
        while True:
            data = self.receive_data(footer_length)
            success, _ = self.footerConverter.parse(data, self.diag)
            if success:
                self.footer = data
                self.diag.debug("Found footer, got body %s", body)
                return body
            else:
                body += data

    def receive_body(self, header_fields):
        footer_length = self.footerConverter.getLength()
        if footer_length:
            return self.receive_body_until_footer(footer_length)
            
        length = self.get_body_length(header_fields)
        if length:
            body = self.receive_data(length)
            self.diag.debug("Got body of size %s %s", length, body)
            return body
        else:
            return b""

    def read_header_or_text(self):
        header_length = self.headerConverter.getLength()
        header = self.socket.recv(header_length)
        if len(header) == 0:
            raise TimeoutError("no data received")
        # go to blocking mode once we have a header, need to make sure we read the rest
        self.socket.settimeout(None)
        try:
            if header_length >= 4 and self.is_capturemock_header(header):
                self.text = self.read_text_until_newline(header)
                self.diag.debug("Got message %s", self.text)
            else:
                self.headers.append(header)
                success, header_fields = self.headerConverter.parse(header, self.diag)
                self.diag.debug("Got header %s %s", header, header_fields)
                if success:
                    self.bodies.append(self.receive_body(header_fields))
                    self.header_fields_list.append(header_fields)
                    if self.headerConverter.is_incomplete(header_fields):
                        self.diag.debug("Message is not complete, fetching next part")
                        return False
                elif header_length < 4 and self.is_capturemock_header(header):
                    self.text = self.read_text_until_newline(header)
                    self.diag.debug("Got message %s", self.text)
                else:
                    self.diag.debug("Failed to parse incoming header %s", header)
                    print("FAILED to parse incoming header", header, file=sys.stderr)
                    return False
        finally:
            self.socket.settimeout(self.header_timeout)
        return True

    def get_body_length(self, header_fields):
        msg_size = header_fields.get("msg_size")
        # ACI format does not allow odd message size...
        if self.headerConverter.padding and msg_size is not None and msg_size % 2 == 1:
            msg_size += 1
        header_size = int(header_fields.get("header_size", 0))
        if msg_size and header_size:
            msg_already_read = self.headerConverter.getLength() - header_size
            return msg_size - msg_already_read
        else:
            return self.get_given_length(header_fields)

    def get_given_length(self, header_fields):
        length = header_fields.get("length")
        if length:
            return length
        else:
            msg_size = header_fields.get("msg_size")
            if msg_size:
                return msg_size - self.headerConverter.getLength()

    def get_body_to_parse(self, index):
        length = self.get_given_length(self.header_fields_list[index])
        body = self.bodies[index]            
        if length is not None and len(body) > length:
            return body[:length]
        else:
            return body

    def parse_body(self):
        body = self.get_body_to_parse(0)
        body_type = toString(self.header_fields_list[0].get("type"))
        subHeaderReader = BinaryMessageConverter(self.rcHandler, body_type + "_header")
        intermHeaderType = body_type + "_intermediate"
        if subHeaderReader.hasData():
            _, subheader_values = subHeaderReader.parse(body, self.diag)
            self.diag.debug("Found subheader values %s", repr(subheader_values))
            self.header_fields_list[0].update(subheader_values)
            subHeaderLength = subHeaderReader.getLength()
            body = body[subHeaderLength:]
            subtype = toString(subheader_values.get("subtype"))
            if subtype:
                body_type += "." + subtype
                self.diag.debug("using subtype %s", body_type)
        if len(self.bodies) > 1:
            intermHeaderReader = BinaryMessageConverter(self.rcHandler, intermHeaderType)
            if intermHeaderReader.hasData():
                for i in range(1, len(self.bodies)):
                    extraBody = self.get_body_to_parse(i)
                    _, interm_values = intermHeaderReader.parse(extraBody, self.diag)
                    self.diag.debug("Found intermediate header values %s", repr(interm_values))
                    self.header_fields_list[i].update(interm_values)
                    intermHeaderLength = intermHeaderReader.getLength()
                    body += extraBody[intermHeaderLength:]
                
        bodyReader = BinaryMessageConverter(self.rcHandler, body_type)
        textParts = []
        for header_fields in self.header_fields_list:
            header = self.headerConverter.getHeaderDescription(header_fields)
            if header:
                textParts.append(header)
        if bodyReader.hasData():
            _, body_values = bodyReader.parse(body, self.diag)
            self.diag.debug("Got body %s", body_values)
            self.diag.debug("assume %s", repr(self.headerConverter.assume))
            self.diag.debug("hf %s", repr(self.header_fields_list))
            textParts.append(pformat(body_values, sort_dicts=False, width=200))
        else:
            if body_type != "None":
                self.diag.debug("Config section not found: %s", body_type)
            textParts.append(toString(body, fixNewLines=True))
        self.text = "\n".join(textParts)
        self.diag.debug("Recording %s", self.text)
        return self.text

    def clear(self):
        self.headers.clear()
        self.bodies.clear()
        self.header_fields_list.clear()

    def read_and_parse(self):
        self.clear()
        # If we can't read the header, ignore it and try the next one
        while not self.read_header_or_text():
            pass
        self.parse_body()
        return self.text, self.get_payload()

    def extract_header(self, text):
        if "\n" in text:
            return text.split("\n", 1)
        else:
            return "", text

    def convert_to_payload(self, text):
        final = False
        self.header_fields_list = []
        while not final:
            header_line, text = self.extract_header(text)
            final = text.startswith("{") or not header_line
            self.header_fields_list.append(self.headerConverter.parseHeaderDescription(header_line, final))
        body_type = toString(self.header_fields_list[0].get("type"))
        subHeaderConv = BinaryMessageConverter(self.rcHandler, body_type + "_header")
        intermHeaderType = body_type + "_intermediate"
        header_payloads = []
        if subHeaderConv.hasData():
            payload = subHeaderConv.fields_to_payload(self.header_fields_list[0], self.diag)
            self.diag.debug("Found subheader payload %s", repr(payload))
            header_payloads.append(payload)
            subtype = toString(self.header_fields_list[0].get("subtype"))
            if subtype:
                body_type += "." + subtype
                self.diag.debug("using subtype %s", body_type)
        else:
            header_payloads.append(b"")
        if len(self.header_fields_list) > 1:
            intermConv = BinaryMessageConverter(self.rcHandler, intermHeaderType)
            if intermConv.hasData():
                for header_fields in self.header_fields_list[1:]:
                    payload = intermConv.fields_to_payload(header_fields, self.diag)
                    self.diag.debug("Found intermediate payload %s", repr(payload))
                    header_payloads.append(payload)
        bodyConv = BinaryMessageConverter(self.rcHandler, body_type)
        if bodyConv.hasData():
            body_values = eval(text)
            self.diag.debug("Body fields %s", body_values)
            body_payload = bodyConv.fields_to_payload(body_values, self.diag)
        else:
            body_payload = text.replace("<CR>", "\r").replace("<LF>", "\n").encode()
        body_payloads = self.split_body(body_payload, len(self.header_fields_list))
        payload = b""
        for i, (header_fields, header_payload, body_payload) in enumerate(zip(self.header_fields_list, header_payloads, body_payloads)):
            curr_payload = header_payload + body_payload
            self.diag.debug("Current payload %s", curr_payload)
            header_fields["length"] = len(curr_payload)
            if "msg_size" not in header_fields:
                body_length = header_fields["length"]
                header_fields["msg_size"] = body_length + self.headerConverter.getLength()
            else:
                body_length = self.get_body_length(header_fields)
                if self.headerConverter.padding:
                    while(len(curr_payload)) < body_length:
                        curr_payload += b"\x00"
            self.diag.debug("Header fields %s", header_fields)
            header_payload = self.headerConverter.fields_to_payload(header_fields, self.diag)
            self.diag.debug("Header payload %s", header_payload)
            payload += header_payload + curr_payload
            if self.footerConverter.getLength():
                # Don't expect variable footer
                footer_fields = self.footerConverter.parseHeaderDescription("")
                payload += self.footerConverter.fields_to_payload(footer_fields, self.diag)
        return payload

    def split_body(self, payload, part_count):
        if part_count == 1:
            return [ payload ]
        part_size = 4096
        if len(payload) > part_size * part_count or len(payload) < part_size * (part_count - 1):
            part_size = len(payload) // part_count + 1
        parts = []
        for i in range(part_count):
            parts.append(payload[i * part_size:(i + 1) * part_size])
        return parts

    def convert_and_send(self, text):
        self.diag.debug("Payload text %s", text)
        payload = self.convert_to_payload(text)
        self.send_payload(payload)
     
    def send_payload(self, payload):
        self.diag.debug("Sending payload %s", payload)
        try:
            self.socket.sendall(payload)
        except OSError as e:
            self.diag.debug("Sending payload had error %s", str(e))

class NullConverter:
    def __repr__(self):
        return self.__class__.__name__

    def combine_converters(self, converters):
        pass

    def unpack(self, *args):
        return (None,), 0

    def get_data_size(self):
        return 0

    def pack(self, *args):
        return b""


class StructConverter:
    def __init__(self, fmt):
        self.fmt = fmt
        self.length = struct.calcsize(self.fmt)

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.fmt + ")"

    def combine_converters(self, converters):
        pass

    def unpack(self, rawBytes, offset, *args):
        return struct.unpack_from(self.fmt, rawBytes, offset=offset), self.length

    def get_data_size(self):
        size = 0
        skip_indices = 0
        fmtToUse = self.fmt[1:]
        for ix, char in enumerate(fmtToUse):
            if skip_indices:
                skip_indices -= 1
            elif char.isdigit():
                nextChar = fmtToUse[ix + 1]
                if nextChar != "s":
                    countStr = char
                    if nextChar.isdigit():
                        countStr += nextChar
                    count = int(countStr)
                    skip_indices = len(countStr)
                    size += count
            else:
                size += 1
        return size

    def pack(self, data, index, *args):
        fmtDataSize = self.get_data_size()
        currData = data[index:index + fmtDataSize]
        return struct.pack(self.fmt, *currData)

class SequenceConverter:
    def __init__(self, lengthFmt, fmt):
        self.lengthFmt = lengthFmt
        self.elementFmt = fmt
        self.extraLength = struct.calcsize(lengthFmt)

    def combine_converters(self, converters):
        pass

    def to_element(self, element_data):
        if len(element_data) == 1:
            return element_data[0]
        else:
            return tuple(element_data)

    def from_element(self, element, converters):
        if len(converters) > 1 or converters[0].get_data_size() > 1:
            return element
        else:
            return (element,)

    def get_data_size(self):
        return 1

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.lengthFmt + ", " + self.elementFmt + ")"

class StringConverter(SequenceConverter):
    def __init__(self, lengthFmt):
        fmt = lengthFmt[0] + "%ds"
        SequenceConverter.__init__(self, lengthFmt, fmt)

    def unpack(self, rawBytes, offset, *args):
        str_length = struct.unpack_from(self.lengthFmt, rawBytes, offset=offset)[0]
        if str_length > 0:
            string_data = struct.unpack_from(self.elementFmt % str_length, rawBytes, offset=offset+self.extraLength)
            return tuple(toString(rawBytes) for rawBytes in string_data), str_length + self.extraLength
        else:
            return [ "" ] if str_length == 0 else [ None ], self.extraLength

    def pack(self, data, index, diag):
        toPack = fromString(data[index], stringOnly=True)
        diag.debug("packing string %s", toPack)
        length = len(toPack) if toPack is not None else -1
        rawBytes = struct.pack(self.lengthFmt, length)
        if length > 0:
            rawBytes += struct.pack(self.elementFmt % length, toPack)
        return rawBytes

class ListConverter(SequenceConverter):
    def __init__(self, lengthFmt, elementFmt, elementConverters):
        SequenceConverter.__init__(self, lengthFmt, elementFmt)
        self.elementConverters = elementConverters

    def unpack(self, rawBytes, offset, diag):
        list_length = struct.unpack_from(self.lengthFmt, rawBytes, offset=offset)[0]
        diag.debug("Found list of length %d", list_length)
        if list_length == 0:
            return [[]], self.extraLength
        
        diag.debug("Found list of length %d", list_length)
        data = []
        element_offset = offset + self.extraLength
        for ix in range(list_length):
            element_data = []
            for converter in self.elementConverters:
                diag.debug("subconverting with %s", converter)
                diag.debug("current subdata is %s", rawBytes[element_offset:])
                curr_data, curr_offset = converter.unpack(rawBytes, element_offset, diag)
                diag.debug("Unpacked to %s %d", curr_data, curr_offset)
                element_data += curr_data
                diag.debug("Element data now %s", element_data)
                element_offset += curr_offset
            data.append(self.to_element(element_data))
        return [ data ], element_offset - offset

    def pack(self, data, index, diag):
        elements = data[index]
        diag.debug("packing list %s", elements)
        length = len(elements)
        rawBytes = struct.pack(self.lengthFmt, length)
        for element in elements:
            elementIndex = 0
            for converter in self.elementConverters:
                diag.debug("converting list element with %s %s %d", converter, element, elementIndex)
                curr_bytes = converter.pack(self.from_element(element, self.elementConverters), elementIndex, diag)
                diag.debug("Packed to %s", curr_bytes)
                rawBytes += curr_bytes
                elementIndex += converter.get_data_size()
        return rawBytes

    def combine_converters(self, converters):
        if len(self.elementConverters) == 0:
            return ListConverter(self.lengthFmt, self.elementFmt, converters)

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.lengthFmt + ", " + self.elementFmt + ", " + repr(self.elementConverters) + ")"

class OptionConverter(SequenceConverter):
    def __init__(self, lengthFmt, elementFmt, optionConverters, bitTemplates):
        SequenceConverter.__init__(self, lengthFmt, elementFmt)
        self.optionConverters = optionConverters
        self.bitTemplates = bitTemplates

    def find_converters(self, optionType, diag, warn=True):
        if optionType < len(self.optionConverters):
            return self.optionConverters[optionType]
        remainingOptionType = optionType
        allBitConverters = []
        for bit, bitConverters in self.bitTemplates.items():
            if remainingOptionType & bit:
                remainingOptionType -= bit
                allBitConverters += bitConverters
        if len(allBitConverters):
            remainder = self.find_converters(remainingOptionType, diag, warn=False)
            if len(remainder) == 1 and isinstance(remainder[0], NullConverter):
                return allBitConverters
            for bitConverter in allBitConverters:
                combined = bitConverter.combine_converters(remainder)
                if combined:
                    return [ combined ]
            return remainder + allBitConverters
        if warn:
            print("WARNING: failed to find option converter for value", optionType, "in format", self.elementFmt, file=sys.stderr)
            diag.debug("Failed to find option converter for value %s in format %s", optionType, self.elementFmt)
        return []

    def unpack(self, rawBytes, offset, diag):
        optionType = struct.unpack_from(self.lengthFmt, rawBytes, offset=offset)[0]
        data = []
        data_offset = offset + self.extraLength
        for converter in self.find_converters(optionType, diag):
            diag.debug("option converting with type %s, %s", optionType, converter)
            diag.debug("current option data is %s", rawBytes[data_offset:])
            curr_data, curr_dataLength = converter.unpack(rawBytes, data_offset, diag)
            data += curr_data
            diag.debug("option data now %s", data)
            data_offset += curr_dataLength
        return [ (optionType, self.to_element(data)) ], data_offset - offset

    def pack(self, data, index, diag):
        toPack = data[index]
        diag.debug("packing variable type %s", toPack)
        optionType, element = toPack
        rawBytes = struct.pack(self.lengthFmt, optionType)
        elementIndex = 0
        converters = self.find_converters(optionType, diag)
        for converter in converters:
            diag.debug("option converting with type %s, %s %s %d", optionType, converter, element, elementIndex)
            curr_bytes = converter.pack(self.from_element(element, converters), elementIndex, diag)
            diag.debug("Packed to %s", curr_bytes)
            rawBytes += curr_bytes
            elementIndex += converter.get_data_size()
        return rawBytes

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.lengthFmt + ", " + self.elementFmt + ", " + \
            repr(self.optionConverters) + ", " + repr(self.bitTemplates) + ")"


class BinaryMessageConverter:
    def __init__(self, rcHandler, rawSection):
        self.message_type = toString(rawSection)
        sections = [ self.message_type ]
        self.rcHandler = rcHandler
        self.fields = rcHandler.getList("fields", sections)
        self.formats = rcHandler.getList("format", sections)
        self.padding = rcHandler.getboolean("tcp_padding", [ "general" ], False)
        self.assume = self.readDictionary(rcHandler, "assume", sections)
        self.enforce = self.readDictionary(rcHandler, "enforce", sections)
        self.assume.update(self.enforce)
        self.incompleteMarkers = self.readDictionary(rcHandler, "incomplete", sections)
        self.length = None

    def hasData(self):
        return len(self.formats) > 0

    def getLength(self):
        if self.length is not None:
            # if we've already parsed it
            return self.length
        elif self.formats:
            return struct.calcsize(self.formats[0])
        else:
            return 0

    def readDictionary(self, rcHandler, key, sections):
        ret = {}
        for part in rcHandler.getList(key, sections):
            key, value = part.split("=")
            ret[key] = value
        return ret

    def is_incomplete(self, fields):
        return any((toString(fields.get(key)) == value for key, value in self.incompleteMarkers.items()))

    def getHeaderDescription(self, fields):
        msgType = fields.get("type")
        filtered_fields = {}
        for key, value in fields.items():
            strval = toString(value)
            if key not in [ "type", "length" ] and (self.padding or key != "msg_size") and \
                self.assume.get(key) != strval and self.incompleteMarkers.get(key) != strval:
                filtered_fields[key] = value
        parts = []
        if msgType is not None:
            return toString(msgType) + " " + repr(filtered_fields)
        elif filtered_fields:
            return repr(filtered_fields)

    def parseHeaderDescription(self, text, final=True):
        if " " in text:
            typeText, fieldText = text.split(" ", 1)
            fields = eval(fieldText)
            fields["type"] = typeText.encode()
        else:
            fields = {}
        for key, value in self.assume.items():
            if key not in fields:
                fields[key] = fromString(value)
        if not final:
            for key, value in self.incompleteMarkers.items():
                fields[key] = fromString(value)
                
        return fields
    
    def extract_sigbytes(self, field, values, fields):
        root, num, least = SigByteCalculation.parse_key(field)
        value = values.get(root)
        if least:
            bitshift = num * 8
            return value & (2 ** bitshift - 1)
        else:
            leastsig_fields = [ field for field in fields if field.startswith(root) and field.endswith("_leastsig") ]
            _, num, _ = SigByteCalculation.parse_key(leastsig_fields[0])
            return value >> (num * 8)

    def fields_to_data(self, values, fields, diag):
        data = []
        diag.debug("Fields %s", fields)
        for field in fields:
            if field in values:
                value = values[field]
                diag.debug("Packing field %s %s", field, value)
                data.append(self.try_deconvert(field, value, diag))
            elif field.endswith("_leastsig") or field.endswith("_mostsig"):
                value = self.extract_sigbytes(field, values, fields)
                diag.debug("Extracted sigbyte field %s %s", field, value)
                data.append(self.try_deconvert(field, value, diag))
            else:
                diag.debug("No data for field %s, packing None", field)
                data.append(None)
        return data

    def fields_to_payload(self, values, diag):
        data = self.fields_to_data(values, self.fields, diag)
        if "additional_params" in values:
            data += values["additional_params"]
        elif "parameters" in values:
            data += values["parameters"]

        return self.pack_data(data, diag)

    def pack_data(self, data, diag):
        diag.debug("Formats %s", self.formats)
        for fmt in self.formats:
            try:
                diag.debug("packing data %s", data)
                return self.pack(fmt, data, diag)
            except struct.error as e:
                errText = str(e)
                diag.debug("Failed to pack due to %s", errText)
                if "not an integer" in errText and "type" in self.fields:
                    # The type can be an integer or a string, if we've got here it's probably an integer...
                    ix = self.fields.index("type")
                    if data[ix].isdigit():
                        data[ix] = int(data[ix])
                        return self.pack_data(data, diag)

    def get_parameter_key(self, values):
        return

    @classmethod
    def find_close_bracket(cls, fmt, ix, bracket_char, close_bracket_char):
        open_bracket = fmt.find(bracket_char, ix + 1)
        close_bracket = fmt.find(close_bracket_char, ix)
        if open_bracket == -1 or close_bracket < open_bracket:
            return close_bracket
        next_close = cls.find_close_bracket(fmt, open_bracket, bracket_char, close_bracket_char)
        return cls.find_close_bracket(fmt, next_close + 1, bracket_char, close_bracket_char)

    @classmethod
    def find_length_format_index(cls, curr_fmt):
        ix = -1
        while curr_fmt[ix] == "x" or curr_fmt[ix].isdigit():
            ix -= 1
        return ix

    @classmethod
    def split_format(cls, fmt):
        ix = 0
        endianchar = fmt[0]
        curr_fmt = ""
        converters = []
        specialChars = "$[]()"
        while ix < len(fmt):
            currChar = fmt[ix]
            if currChar in specialChars:
                length_fmt_ix = cls.find_length_format_index(curr_fmt)
                prev = curr_fmt[:length_fmt_ix]
                if prev and prev != endianchar:
                    if not prev.startswith(endianchar):
                        prev = endianchar + prev
                    converters.append(StructConverter(prev))
                lengthFmt = endianchar + curr_fmt[length_fmt_ix:]
                if currChar == "[":
                    closePos = cls.find_close_bracket(fmt, ix, "[", "]")
                    fmtPart = fmt[ix + 1:closePos]
                    if fmtPart:
                        listFmt = endianchar + fmtPart
                        listConverters = cls.split_format(listFmt)
                        converters.append(ListConverter(lengthFmt, listFmt, listConverters))
                    else:
                        # placeholder for bit templates
                        converters.append(ListConverter(lengthFmt, "variable", []))
                    ix = closePos
                elif currChar == "(":
                    closePos = cls.find_close_bracket(fmt, ix, "(", ")")
                    allOptionFmt = fmt[ix + 1:closePos]
                    optionConverters = []
                    bitTemplates = {}
                    currSubFmt = ""
                    indent = 0
                    for optionFmt in allOptionFmt.split("|"):
                        optionFmtToUse = None
                        if "(" in optionFmt:
                            currSubFmt += optionFmt + "|"
                            indent += 1
                        elif indent > 0:
                            if ")" in optionFmt:
                                indent -= 1
                                if indent == 0:
                                    optionFmtToUse = currSubFmt + optionFmt
                                    currSubFmt = ""
                                else:
                                    currSubFmt += optionFmt + "|"
                            else:
                                currSubFmt += optionFmt + "|"
                        else:
                            optionFmtToUse = optionFmt
                        if optionFmtToUse is not None:
                            if "=" in optionFmtToUse:
                                bit, template = optionFmtToUse.split("=", 1)
                                if bit.isdigit():
                                    bitTemplates[int(bit)] = cls.split_format(endianchar + template)
                                    continue
                            if optionFmtToUse:
                                optionConverters.append(cls.split_format(endianchar + optionFmtToUse))
                            else:
                                optionConverters.append([ NullConverter() ])
                    converters.append(OptionConverter(lengthFmt, allOptionFmt, optionConverters, bitTemplates))
                    ix = closePos
                else:
                    converters.append(StringConverter(lengthFmt))
                curr_fmt = ""
            else:
                curr_fmt += fmt[ix]
            ix += 1
        if curr_fmt:
            if not curr_fmt.startswith(endianchar):
                curr_fmt = endianchar + curr_fmt
            converters.append(StructConverter(curr_fmt))
        return converters

    def unpack(self, fmt, rawBytes, diag):
        offset = 0
        data = []
        diag.debug("splitting format %s", fmt)
        for converter in self.split_format(fmt):
            diag.debug("converting with %s", converter)
            diag.debug("current data is %s", rawBytes[offset:])
            curr_data, curr_offset = converter.unpack(rawBytes, offset, diag)
            diag.debug("Unpacked to %s %d", curr_data, curr_offset)
            data += curr_data
            offset += curr_offset
        # If we aren't a header, place the rest in a final field
        if not self.message_type.endswith("_header") and offset < len(rawBytes):
            data.append(rawBytes[offset:].hex())
            offset = len(rawBytes)
        return data, offset

    @classmethod
    def pack(cls, fmt, data, diag):
        rawBytes = b""
        index = 0
        diag.debug("splitting format %s", fmt)
        for converter in cls.split_format(fmt):
            diag.debug("converting with %s", converter)
            curr_bytes = converter.pack(data, index, diag)
            diag.debug("Packed to %s", curr_bytes)
            rawBytes += curr_bytes
            index += converter.get_data_size()
        return rawBytes

    def parse(self, rawBytes, diag):
        data = None
        for fmt in self.formats:
            try:
                data, length = self.unpack(fmt, rawBytes, diag)
                self.length = length
                break
            except struct.error as e:
                diag.debug("Failed to unpack due to %s", str(e))
        if data is None:
            return False, { "unknown_format" : rawBytes.hex() }

        diag.debug("Found %s", data)
        field_values = {}
        for i, field in enumerate(self.fields):
            if i < len(data):
                value = self.try_convert(field, data[i])
                if value is not None:
                    field_values[field] = value
        parameters = list(data[len(self.fields):])
        if len(parameters) > 0:
            key = "additional_params" if "parameters" in field_values else "parameters"
            field_values[key] = parameters
        ok = True
        for key, value in self.enforce.items():
            if str(field_values.get(key)) != value:
                ok = False
        for key, value in self.assume.items():
            if key not in field_values:
                field_values[key] = value
        field_values = self.transform_for_sigbytes(field_values, diag)
        return ok, field_values

    def try_convert(self, field, value):
        if isinstance(value, int):
            return self.try_convert_enum(field, value)
        elif isinstance(value, list):
            return self.try_convert_list(field, value)
        else:
            return value

    def try_convert_enum(self, field, value):
        if field == "Time":
            return datetime.fromtimestamp(value).isoformat()

        enum_list = self.rcHandler.getList(field, [ "enums"])
        size = len(enum_list)
        if size and value < size:
            return enum_list[value]
        else:
            return value

    def try_convert_list(self, field, listvalue):
        element_names = self.rcHandler.getList(field, [ "list_elements"])
        count = len(element_names)
        if count == 0 or not all((len(element) == count for element in listvalue)):
            return listvalue

        new_list = []
        for element in listvalue:
            new_element = {}
            for name, value in zip(element_names, element):
                if value is not None:
                    new_element[name] = self.try_convert(name, value)
            new_list.append(new_element)
        return new_list

    def try_deconvert(self, field, value, diag):
        if isinstance(value, str):
            return self.try_deconvert_enum(field, value)
        elif isinstance(value, list):
            return self.try_deconvert_list(field, value, diag)
        else:
            return value

    def try_deconvert_enum(self, field, value):
        if field == "Time":
            return int(datetime.fromisoformat(value).timestamp())

        enum_list = self.rcHandler.getList(field, [ "enums"])
        if value in enum_list:
            return enum_list.index(value)
        else:
            return value

    def try_deconvert_list(self, field, listvalue, diag):
        element_names = self.rcHandler.getList(field, [ "list_elements"])
        if len(element_names) == 0 or not all((isinstance(element, dict) for element in listvalue)):
            return listvalue

        new_list = []
        for element in listvalue:
            new_element = self.fields_to_data(element, element_names, diag)
            new_list.append(tuple(new_element))
        return new_list
    
    @classmethod
    def transform_for_sigbytes(cls, field_values, diag):
        if not any(key.endswith("sig") for key in field_values):
            return field_values
        diag.debug("Transforming for sigbytes...")
        new_field_values = {}
        for key, value in field_values.items():
            if key.endswith("_leastsig") or key.endswith("_mostsig"):
                diag.debug("sigbytes key %s", key)
                calc = SigByteCalculation(key, value)
                curr_calc = new_field_values.get(calc.root)
                if curr_calc:
                    curr_calc.update(calc)
                else:
                    new_field_values[calc.root] = calc
            elif isinstance(value, dict):
                new_field_values[key] = cls.transform_for_sigbytes(value, diag)
            elif isinstance(value, list):
                new_field_values[key] = [ cls.transform_for_sigbytes(item, diag) for item in value ]
            else:
                new_field_values[key] = value
                    
        for key, calcOrValue in new_field_values.items():
            if isinstance(calcOrValue, SigByteCalculation):
                new_field_values[key] = calcOrValue.value()

        return new_field_values
 
class SigByteCalculation:
    def __init__(self, key, value):
        self.leastsig, self.mostsig = None, None
        self.root, num, least = self.parse_key(key)
        if least:
            self.leastsig = value
            self.leastsize = num
        else:
            self.mostsig = value
            self.leastsize = None

    @classmethod
    def parse_key(cls, key):
        parts = key.split("_")
        least = parts[-1] == "leastsig"
        if parts[-2].isdigit():
            num = int(parts[-2])
            root = "_".join(parts[:-2])
        else:
            num = 1
            root = "_".join(parts[:-1])
        return root, num, least
    
    def update(self, other):
        if self.leastsig is None:
            self.leastsig = other.leastsig
        if self.mostsig is None:
            self.mostsig = other.mostsig
        if self.leastsize is None:
            self.leastsize = other.leastsize

    def value(self):
        return (self.mostsig << (self.leastsize * 8)) | self.leastsig    

class SynchServerLocationTraffic(clientservertraffic.ClientSocketTraffic):
    socketId = "CAPTUREMOCK_SYNCH"
    def __init__(self, inText, *args):
        lastWord = inText.strip().split()[-1]
        self.set_data(lastWord)
        clientservertraffic.ClientSocketTraffic.__init__(self, inText, *args)

    def set_data(self, data):
        if ":" in data:
            host, port = data.split(":")
            if port.isdigit():
                dest = host, int(port)
                TcpHeaderTrafficServer.synch_server_locations.append(dest)

    def forwardToDestination(self):
        return []

    def record(self, *args):
        pass


class SynchStatusTraffic(clientservertraffic.ClientSocketTraffic):
    socketId = "CAPTUREMOCK_STATUS"
    def forwardToDestination(self):
        return []


class TcpHeaderTrafficServer:
    synch_server_locations = []
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls(address, dispatcher)

    def __init__(self, address, dispatcher):
        self.dispatcher = dispatcher
        self.connection_timeout = dispatcher.rcHandler.getfloat("connection_timeout", [ "general" ], 0.2)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.connection_timeout)
        self.socket.bind((address, 0))
        self.socket.listen()
        self.diag = get_logger()
        self.clientConverter = None
        self.serverConverter = None
        self.serverTrafficCache = []
        self.terminate = False
        self.requestCount = 0

    def getAddress(self):
        host, port = self.socket.getsockname()
        return host + ":" + str(port)

    def acceptSocket(self):
        try:
            connSocket, _ = self.socket.accept()
            connSocket.settimeout(self.connection_timeout)
            return connSocket
        except socket.timeout:
            self.diag.debug("Timeout accepting new connection")

    def tryConnectServer(self, reset=False):
        if reset or self.serverConverter is None:
            self.serverConverter = BinaryClientSocketTraffic.getServerConverter(self.dispatcher.rcHandler, reset)

    def send_synch_status(self, text):
        self.diag.debug("Sending synch status '" + text + "'")
        for synch_server in self.synch_server_locations:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(synch_server)
            sock.sendall((SynchStatusTraffic.socketId + ":" + text + "\n").encode())
            sock.close()
        self.diag.debug("Sent synch status '" + text + "'")
        
    def handle_client_traffic(self, text, payload=None):
        self.requestCount += 1
        internal = payload is None
        traffic = BinaryClientSocketTraffic(text, None, rcHandler=self.dispatcher.rcHandler, 
                                            serverConverter=self.serverConverter, payload=payload, internal=internal)
        responses = self.dispatcher.process(traffic, self.requestCount)
        if (not self.serverConverter or internal) and self.clientConverter:
            for response in responses:
                self.clientConverter.convert_and_send(response.text)
        if len(self.synch_server_locations) > 0 and not internal:
            self.send_synch_status(text)

    def handle_server_traffic(self, text, payload):
        if self.clientConverter:
            self.handle_server_traffic_for_real(text, payload)
        else:
            self.diag.debug("Server traffic, no client")
            self.serverTrafficCache.append((text, payload))

    def handle_server_traffic_for_real(self, text, payload):
        traffic = BinaryServerSocketTraffic(text, None, rcHandler=self.dispatcher.rcHandler)
        self.requestCount += 1
        self.dispatcher.process(traffic, self.requestCount)
        self.clientConverter.send_payload(payload)

    def try_client(self):
        if self.clientConverter:
            try:
                text, payload = self.clientConverter.read_and_parse()
                self.handle_client_traffic(text, payload)
                return True
            except TimeoutError:
                self.diag.debug("Read from client timed out")
            except OSError as e:
                self.diag.debug("Read from client had other error %s", str(e))
        return False

    def try_server(self):
        if not self.serverConverter:
            self.tryConnectServer()
        if self.serverConverter:
            try:
                text, payload = self.serverConverter.read_and_parse()
                self.handle_server_traffic(text, payload)
                return True
            except TimeoutError:
                self.diag.debug("Read from server timed out")
            except ConnectionAbortedError:
                self.diag.debug("Server connection aborted, resetting!")
                self.tryConnectServer(reset=True)
            except OSError as e:
                self.diag.debug("Read from server had other error %s", str(e))
        return False

    def handle_server_traffic_from_cache(self):
        if len(self.serverTrafficCache):
            for cacheText, cachePayload in self.serverTrafficCache:
                self.handle_server_traffic_for_real(cacheText, cachePayload)
            self.serverTrafficCache.clear()

    def set_client_converter(self, converter):
        if self.clientConverter is not None:
            self.diag.debug("Second connection from client, resetting connection to the server")
            self.tryConnectServer(reset=True)
        self.clientConverter = converter

    def try_new_or_internal(self):
        connSocket = self.acceptSocket()
        if connSocket:
            self.diag.debug("Accepted new connection from client")
            converter = BinaryTrafficConverter(self.dispatcher.rcHandler, connSocket)
            try:
                converter.read_header_or_text()
            except socket.timeout:
                self.set_client_converter(converter)
                self.handle_client_traffic("connect")
                self.handle_server_traffic_from_cache()
                self.diag.debug("Timeout client read, set socket")
                return False
            if converter.text: # complete message, i.e. special for CaptureMock
                self.requestCount += 1
                responses = self.dispatcher.processText(converter.text, None, self.requestCount)
                self.diag.debug("Got message %s", converter.text)
                self.tryConnectServer()
                for response in responses:
                    self.clientConverter.convert_and_send(response.text)
                return converter.text.startswith("CAPTUREMOCK") # internal status, may be more
            else:
                self.set_client_converter(converter)
                payload = self.clientConverter.get_payload()
                text = self.clientConverter.parse_body()
                self.handle_client_traffic(text, payload)
        return False

    def run(self):
        while not self.terminate:
            while self.try_server():
                pass

            if self.try_client():
                continue

            while self.try_new_or_internal():
                pass

    def shutdown(self):
        self.terminate = True

    @classmethod
    def sendTerminateMessage(cls, serverAddressStr):
        clientservertraffic.ClientSocketTraffic.sendTerminateMessage(serverAddressStr)

    @staticmethod
    def getTrafficClasses(incoming):
        if incoming:
            return [ clientservertraffic.ClassicServerStateTraffic, SynchServerLocationTraffic, SynchStatusTraffic, BinaryClientSocketTraffic ]
        else:
            return [ BinaryServerSocketTraffic, BinaryClientSocketTraffic ]


def get_header(line, headers):
    for header in headers:
        if line.startswith(header):
            return header

if __name__ == "__main__":
    #fmt = "<2Bq2Ii$I3Bi$i$Bi$Ii$i$i[i$]"
    #fmt = "<i[i$i[i$]i$Ii$i$i$B]"
    #fmt = "<q3Ii[i$]BHIBHIdi$i$Ii[i$i$i$Bi$Ii$i$i[i$]i$Ii$i$i$B]i[i$]i$i$I"
    #fmt = "<q3Ii[i$]i[B(x|B(x|?|b|B|h|H|i|I|q|Q|f|d|i$|128=i[])|I|B(x|?|b|B|h|H|i|I|q|Q|f|d|i$|128=i[])I)]i[B]"
    fmt = ">2I2HII4x[3I4B]"
    # fmt = "<5Ii$"
    # fmt = "<Ii$i$i$2I2BH"
    # fmt = "<4I2BH2Bq2Ii$I3Bi$i$Bi$Ii$i$i[i$]i$i$i$i$i$dI"
    # fmt = "<i[i$]"
    # rawBytes = b'HELFK\x00\x00\x00'
    # rawBytes = b'\x00\x00\x00\x00/\x00\x00\x00http://opcfoundation.org/UA/SecurityPolicy#None\xff\xff\xff\xff\xff\xff\xff\xff\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\xbe\x01\x00\x00@\x10\xd0\x02\x8es\xd9\x01\x01\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff\xe8\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x80\xee6\x00'
    # rawBytes = b'\x01\x00\x00\x00%\x00\x00\x00opc.tcp://0.0.0.0:0/freeopcua/server/'
    #logger = logging.getLogger("main")
    #logger.setLevel(logging.DEBUG)
    #handler = logging.StreamHandler(sys.stdout)
    #logger.addHandler(handler)
    print(BinaryMessageConverter.split_format(fmt))
    #print(BinaryMessageConverter.unpack(fmt, rawBytes, logger))
    # from capturemock import config
    # rc = r"D:\texttest_repos\tone\capturemock-any-systemmanager.rc"
    # rcHandler = config.RcFileHandler([ rc ])
    # diag = logging.getLogger("test")
    # conv = BinaryTrafficConverter(rcHandler, None, diag)
    # fn = r"D:\TT\tone.28Mar154841.35540\tone\Transports\PetainerData\SetIO\cpmock_binarytcp.txt"
    # curr_payload = b""
    # curr_text = ""
    # recording = False
    # count = 1
    # failed = []
    # prefix = "Binary TCP Traffic DEBUG - "
    # text_headers = [ prefix + "Payload text ", prefix + "Recording " ]
    # payload_header = prefix + "Sending payload "
    # header_header = prefix + "Got header "
    # body_header = prefix + "Got body of size "
    # end_header = prefix + "Server traffic, no client"
    # with open(fn) as f:
    #     for line in f:
    #         text_header = get_header(line, text_headers)
    #         if text_header:
    #             curr_text = line[len(text_header):]
    #             recording = True
    #         elif line.startswith(header_header):
    #             curr_payload += eval(line.split()[7])
    #         elif line.startswith(body_header):
    #             curr_payload += eval(line.split(" ", 10)[10])
    #         elif line.startswith(payload_header) or line.startswith(end_header):
    #             if line.startswith(payload_header):
    #                 curr_payload = eval(line[len(payload_header):])
    #             print(count, "compare", curr_text, "with")
    #             print(curr_payload)
    #             new_payload = conv.convert_to_payload(curr_text)
    #             print(new_payload)
    #             if new_payload == curr_payload:
    #                 print("SUCCESS")
    #             else:
    #                 print("FAILURE")
    #                 failed.append((curr_text, curr_payload, new_payload))
    #             recording = False
    #             curr_payload = b""
    #             curr_text = ""
    #             count += 1
    #         elif recording:
    #             curr_text += line
    # print("\nFAILED:")
    # class FakeSocket:
    #     def __init__(self, rawBytes):
    #         self.rawBytes = rawBytes
    #
    #     def recv(self, count):
    #         ret = self.rawBytes[:count]
    #         self.rawBytes = self.rawBytes[count:]
    #         return ret
    #
    #     def settimeout(self, *args):
    #         pass
    #
    # for curr_text, curr_payload, new_payload in failed:
    #     print("\n" + curr_text)
    #     print("got:", len(new_payload), new_payload)
    #     print("not:", len(curr_payload), curr_payload)
    #     fakeSock = FakeSocket(curr_payload)
    #     conv = BinaryTrafficConverter(rcHandler, fakeSock, diag)
    #     text, _ = conv.read_and_parse()
    #     print("\nNew:", text)
    #
    from pprint import pprint
    data = {'frame_number': 0,
 'capture_time_4_leastsig': 985001002,
 'frame_status': 0,
 'capture_time_mostsig': 15,
 'subsamp_gain_exposure_smpte': 25600,
 'markers': [{'ypos_leastsig': 162,
              'xpos_mostsig': 0,
              'xpos_2_leastsig': 49884,
              'xradius_2_leastsig': 159,
              'ypos_mostsig': 8,
              'yradius_mostsig': 0,
              'yradius_leastsig': 94,
              'xradius_mostsig': 0,
              'quality2': 100,
              'quality1': 40},
             {'ypos_leastsig': 254,
              'xpos_mostsig': 0,
              'xpos_2_leastsig': 51967,
              'xradius_2_leastsig': 108,
              'ypos_mostsig': 8,
              'yradius_mostsig': 0,
              'yradius_leastsig': 166,
              'xradius_mostsig': 0,
              'quality2': 31,
              'quality1': 53},
             {'ypos_leastsig': 75,
              'xpos_mostsig': 0,
              'xpos_2_leastsig': 19349,
              'xradius_2_leastsig': 272,
              'ypos_mostsig': 12,
              'yradius_mostsig': 0,
              'yradius_leastsig': 221,
              'xradius_mostsig': 0,
              'quality2': 48,
              'quality1': 18}]}
    logging.basicConfig(level=logging.DEBUG)
    diag = logging.getLogger()
    pprint(BinaryMessageConverter.transform_for_sigbytes(data, diag), sort_dicts=False)
    class FakeRcHandler:
        def getList(self, key, sections):
            if key == "fields":
                return "frame_number,capture_time_4_leastsig,frame_status,capture_time_mostsig,subsamp_gain_exposure_smpte,markers".split(",")
            elif key == "format":
                return [">2I2HII4x[2B3HBHB2x2B]"]
            elif "list_elements" in sections:
                return "ypos_leastsig,xpos_mostsig,xpos_2_leastsig,xradius_2_leastsig,ypos_mostsig,yradius_mostsig,yradius_2_leastsig,xradius_mostsig,quality2,quality1".split(",")
            else:
                return []
            
        def getboolean(self, *args):
            return False
    bodyConv = BinaryMessageConverter(FakeRcHandler(), "18")
    body_values = {'frame_number': 0,
 'capture_time': 65409510442,
 'frame_status': 0,
 'subsamp_gain_exposure_smpte': 25600,
 'markers': [{'ypos': 2210,
              'xpos': 49884,
              'xradius': 159,
              'yradius': 94,
              'quality2': 100,
              'quality1': 40},
             {'ypos': 2302,
              'xpos': 51967,
              'xradius': 108,
              'yradius': 166,
              'quality2': 31,
              'quality1': 53},
             {'ypos': 3147,
              'xpos': 19349,
              'xradius': 272,
              'yradius': 221,
              'quality2': 48,
              'quality1': 18}]}
    pprint(bodyConv.fields_to_payload(body_values, diag))
