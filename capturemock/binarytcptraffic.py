
from capturemock import clientservertraffic
from datetime import datetime
import sys, struct, socket
from pprint import pformat
import logging
        
class BinaryClientSocketTraffic(clientservertraffic.ClientSocketTraffic):
    def forwardToServer(self):
        return []
    
class BinaryServerSocketTraffic(clientservertraffic.ServerTraffic):
    def __init__(self, text, responseFile, rcHandler=None):
        ts = self.get_timestamp(rcHandler)
        super(BinaryServerSocketTraffic, self).__init__(text, responseFile, rcHandler=rcHandler, timestamp=ts)

    def forwardToDestination(self):
        return []

def toString(data):
    if isinstance(data, tuple): # produced by variable types, assume the key data is at the end
        return toString(data[-1])
    elif isinstance(data, bytes):
        return data.decode()
    else:
        return str(data)

def fromString(data):
    if data.isdigit():
        return int(data)
    elif isinstance(data, str):
        return data.encode()
    else:
        return data

class BinaryTrafficConverter:
    header_timeout = 0.5
    def __init__(self, rcHandler, sock, diag):
        self.rcHandler = rcHandler
        self.socket = sock
        if sock:
            sock.settimeout(self.header_timeout)
        self.headerConverter = BinaryMessageConverter(rcHandler, "tcp_header")
        self.text = None
        self.header = None
        self.header_fields = None
        self.body = None
        self.diag = diag

    def get_payload(self):
        return self.header + self.body

    def read_text_until_newline(self, header):
        rawBytes = header
        while not rawBytes.endswith(b"\n"):
            rawBytes += self.socket.recv(1)
        return rawBytes.decode()

    def read_header_or_text(self):
        header = self.socket.recv(self.headerConverter.getLength())
        if len(header) == 0:
            raise TimeoutError("no data received")
        # go to blocking mode once we have a header, need to make sure we read the rest
        self.socket.settimeout(None)
        try:
            if header.startswith(b"SUT_SERV") or header.startswith(b"TERMINAT"):
                self.text = self.read_text_until_newline(header)
                self.diag.debug("Got message %s", self.text)
            else:
                self.header = header
                success, self.header_fields = self.headerConverter.parse(header, self.diag)
                self.diag.debug("Got header %s %s", header, self.header_fields)
                if success:
                    length = self.get_body_length()
                    if length:
                        self.body = self.socket.recv(length)
                    self.diag.debug("Got body of size %s %s", length, self.body)
                else:
                    self.diag.debug("Failed to parse incoming header %s", header)
                    print("FAILED to parse incoming header", header, file=sys.stderr)
                    return False
        finally:
            self.socket.settimeout(self.header_timeout)
        return True

    def get_body_length(self):
        msg_size = self.header_fields.get("msg_size")
        if msg_size is not None and msg_size % 2 == 1: # ACI format does not allow odd message size...
            msg_size += 1
        header_size = int(self.header_fields.get("header_size"))
        if msg_size and header_size:
            msg_already_read = self.headerConverter.getLength() - header_size
            return msg_size - msg_already_read
        elif header_size:
            return self.header_fields.get("length") - header_size
        else:
            return self.header_fields.get("length")

    def get_given_length(self):
        length = self.header_fields.get("length")
        if length:
            return length
        else:
            return self.header_fields.get("msg_size") - int(self.header_fields.get("header_size"))

    def get_body_to_parse(self):
        length = self.get_given_length()
        if len(self.body) > length:
            return self.body[:length]
        else:
            return self.body

    def parse_body(self):
        body = self.get_body_to_parse()
        body_type = toString(self.header_fields.get("type"))
        subHeaderReader = BinaryMessageConverter(self.rcHandler, body_type + "_header")
        if subHeaderReader.hasData():
            _, subheader_values = subHeaderReader.parse(body, self.diag)
            self.diag.debug("Found subheader values %s", repr(subheader_values))
            self.header_fields.update(subheader_values)
            subHeaderLength = subHeaderReader.getLength()
            body = body[subHeaderLength:]
            subtype = toString(subheader_values.get("subtype"))
            if subtype:
                body_type += "." + subtype
                self.diag.debug("using subtype %s", body_type)
        bodyReader = BinaryMessageConverter(self.rcHandler, body_type)
        _, body_values = bodyReader.parse(body, self.diag)
        self.diag.debug("Got body %s", body_values)
        self.diag.debug("assume %s", repr(self.headerConverter.assume))
        self.diag.debug("hf %s", repr(self.header_fields))
        self.text = self.headerConverter.getHeaderDescription(self.header_fields) + "\n" + pformat(body_values, sort_dicts=False, width=200)
        self.diag.debug("Recording %s", self.text)
        return self.text

    def read_and_parse(self):
        # If we can't read the header, ignore it and try the next one
        while not self.read_header_or_text():
            pass
        self.parse_body()
        return self.text, self.get_payload()

    def convert_to_payload(self, text):
        header_line, remainder = text.split("\n", 1)
        self.header_fields = self.headerConverter.parseHeaderDescription(header_line)
        body_type = toString(self.header_fields.get("type"))
        subHeaderConv = BinaryMessageConverter(self.rcHandler, body_type + "_header")
        payload = b""
        if subHeaderConv.hasData():
            payload = subHeaderConv.fields_to_payload(self.header_fields, self.diag)
            self.diag.debug("Found subheader payload %s", repr(payload))
            subtype = toString(self.header_fields.get("subtype"))
            if subtype:
                body_type += "." + subtype
                self.diag.debug("using subtype %s", body_type)
        bodyConv = BinaryMessageConverter(self.rcHandler, body_type)
        body_values = eval(remainder)
        self.diag.debug("Body fields %s", body_values)
        payload += bodyConv.fields_to_payload(body_values, self.diag)
        self.diag.debug("Body payload %s", payload)
        self.header_fields["length"] = len(payload)
        if "msg_size" not in self.header_fields:
            body_length = self.header_fields["length"]
            self.header_fields["msg_size"] = body_length + self.header_fields["header_size"]
        else:
            body_length = self.get_body_length()
            while(len(payload)) < body_length:
                payload += b"\x00"
        self.diag.debug("Header fields %s", self.header_fields)
        header_payload = self.headerConverter.fields_to_payload(self.header_fields, self.diag)
        self.diag.debug("Header payload %s", header_payload)
        return header_payload + payload

    def send_payload(self, text):
        self.diag.debug("Payload text %s", text)
        payload = self.convert_to_payload(text)
        self.diag.debug("Sending payload %s", payload)
        self.socket.sendall(payload)

class StructConverter:
    def __init__(self, fmt):
        self.fmt = fmt
        self.length = struct.calcsize(self.fmt)

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.fmt + ")"

    def unpack(self, rawBytes, offset, *args):
        return struct.unpack_from(self.fmt, rawBytes, offset=offset), self.length

    def get_data_size(self):
        size = 0
        for ix, char in enumerate(self.fmt[1:]):
            if char.isdigit():
                nextChar = self.fmt[ix + 1]
                if nextChar != "s":
                    count = int(char)
                    size += count - 1
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

    def to_element(self, element_data):
        if len(element_data) == 1:
            return element_data[0]
        else:
            return tuple(element_data)
        
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
            return string_data, str_length + self.extraLength
        else:
            return [ b"" ] if str_length == 0 else [ None ], self.extraLength

    def pack(self, data, index, diag):
        toPack = data[index]
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
        if list_length == 0:
            return [[]], self.extraLength
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

    def from_element(self, element):
        if len(self.elementConverters) > 1 or self.elementConverters[0].get_data_size() > 1:
            return element
        else:
            return (element,)

    def pack(self, data, index, diag):
        elements = data[index]
        diag.debug("packing list %s", elements)
        length = len(elements)
        rawBytes = struct.pack(self.lengthFmt, length)
        for element in elements:
            elementIndex = 0
            for converter in self.elementConverters:
                diag.debug("converting list element with %s %s %d", converter, element, elementIndex)
                curr_bytes = converter.pack(self.from_element(element), elementIndex, diag)
                diag.debug("Packed to %s", curr_bytes)
                rawBytes += curr_bytes
                elementIndex += converter.get_data_size()
        return rawBytes

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.lengthFmt + ", " + self.elementFmt + ", " + repr(self.elementConverters) + ")"

class OptionConverter(SequenceConverter):
    def __init__(self, lengthFmt, elementFmt, optionConverters, bitTemplates):
        SequenceConverter.__init__(self, lengthFmt, elementFmt)
        self.optionConverters = optionConverters
        self.bitTemplates = bitTemplates

    def find_converters(self, optionType):
        if optionType < len(self.optionConverters):
            return self.optionConverters[optionType]
        for bit, template in self.bitTemplates.items():
            if optionType & bit:
                subOptionType = optionType - bit
                if subOptionType < len(self.optionConverters):
                    elementConverters = self.optionConverters[subOptionType]
                    if "[]" in template:
                        lengthFmt = self.lengthFmt[0] + template[0]
                        return [ ListConverter(lengthFmt, template, elementConverters) ]
        print("WARNING: failed to find option converter for value", optionType, "in format", self.elementFmt, file=sys.stderr)
        return []

    def unpack(self, rawBytes, offset, diag):
        optionType = struct.unpack_from(self.lengthFmt, rawBytes, offset=offset)[0]
        data = []
        data_offset = offset + self.extraLength
        for converter in self.find_converters(optionType):
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
        for converter in self.find_converters(optionType):
            diag.debug("option converting with type %s, %s %s %d", optionType, converter, element, elementIndex)
            curr_bytes = converter.pack(element, elementIndex, diag)
            diag.debug("Packed to %s", curr_bytes)
            rawBytes += curr_bytes
            elementIndex += converter.get_data_size()
        return rawBytes

    def __repr__(self):
        return self.__class__.__name__ + "(" + self.lengthFmt + ", " + self.elementFmt + ", " + repr(self.optionConverters) + ")"


class BinaryMessageConverter:
    def __init__(self, rcHandler, rawSection):
        section = toString(rawSection)
        sections = [ section ]
        self.rcHandler = rcHandler
        self.fields = rcHandler.getList("fields", sections)
        self.formats = rcHandler.getList("format", sections)
        self.assume = self.readDictionary(rcHandler, "assume", sections)
        self.enforce = self.readDictionary(rcHandler, "enforce", sections)
        self.assume.update(self.enforce)
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

    def getHeaderDescription(self, fields):
        msgType = fields.get("type")
        filtered_fields = {}
        for key, value in fields.items():
            if key not in [ "type", "length", "msg_size" ] and self.assume.get(key) != toString(value):
                filtered_fields[key] = value
        return toString(msgType) + " " + repr(filtered_fields)

    def parseHeaderDescription(self, text):
        typeText, fieldText = text.split(" ", 1)
        fields = eval(fieldText)
        fields["type"] = typeText.encode()
        for key, value in self.assume.items():
            if key not in fields:
                fields[key] = fromString(value)
        return fields

    def fields_to_payload(self, values, diag):
        data = []
        diag.debug("Fields %s", self.fields)
        for field in self.fields:
            if field in values:
                diag.debug("Packing field %s %s", field, values[field])
                data.append(self.try_enum_to_payload(field, values[field]))
            else:
                data.append(None)

        if "additional_params" in values:
            data += values["additional_params"]
        elif "parameters" in values:
            data += values["parameters"]

        diag.debug("Formats %s", self.formats)
        for fmt in self.formats:
            try:
                diag.debug("packing data %s", data)
                return self.pack(fmt, data, diag)
            except struct.error as e:
                diag.debug("Failed to pack due to %s", str(e))

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
    def split_format(cls, fmt):
        ix = 0
        endianchar = fmt[0]
        curr_fmt = ""
        converters = []
        specialChars = "$[]()"
        while ix < len(fmt):
            currChar = fmt[ix]
            if currChar in specialChars:
                prev = curr_fmt[:-1]
                if prev and prev != endianchar:
                    if not prev.startswith(endianchar):
                        prev = endianchar + prev
                    converters.append(StructConverter(prev))
                lengthFmt = endianchar + curr_fmt[-1]
                if currChar == "[":
                    closePos = cls.find_close_bracket(fmt, ix, "[", "]")
                    listFmt = endianchar + fmt[ix + 1:closePos]
                    listConverters = cls.split_format(listFmt)
                    converters.append(ListConverter(lengthFmt, listFmt, listConverters))
                    ix = closePos
                elif currChar == "(":
                    closePos = cls.find_close_bracket(fmt, ix, "(", ")")
                    allOptionFmt = fmt[ix + 1:closePos]
                    optionConverters = []
                    bitTemplates = {}
                    for optionFmt in allOptionFmt.split("|"):
                        if "=" in optionFmt:
                            bit, template = optionFmt.split("=")
                            bitTemplates[int(bit)] = template
                        else:
                            optionConverters.append(cls.split_format(endianchar + optionFmt))
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

    @classmethod
    def unpack(cls, fmt, rawBytes, diag):
        offset = 0
        data = []
        diag.debug("splitting format %s", fmt)
        for converter in cls.split_format(fmt):
            diag.debug("converting with %s", converter)
            diag.debug("current data is %s", rawBytes[offset:])
            curr_data, curr_offset = converter.unpack(rawBytes, offset, diag)
            diag.debug("Unpacked to %s %d", curr_data, curr_offset)
            data += curr_data
            offset += curr_offset
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
        diag.debug("Packed to %s", rawBytes)
        return rawBytes
        #struct.pack(fmt, *data)

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
                value = self.try_convert_enum(field, data[i])
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
        return ok, field_values

    def try_convert_enum(self, field, value):
        if not isinstance(value, int):
            return value

        if field == "Time":
            return datetime.fromtimestamp(value).isoformat()

        enum_list = self.rcHandler.getList(field, [ "enums"])
        size = len(enum_list)
        if size and value <= size:
            return enum_list[value - 1]
        else:
            return value

    def try_enum_to_payload(self, field, value):
        if isinstance(value, int):
            return value

        if field == "Time":
            return int(datetime.fromisoformat(value).timestamp())

        enum_list = self.rcHandler.getList(field, [ "enums"])
        if value in enum_list:
            return enum_list.index(value) + 1
        else:
            return value

class TcpHeaderTrafficServer:
    connection_timeout = 0.2
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls(address, dispatcher)

    def __init__(self, address, dispatcher):
        self.dispatcher = dispatcher
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.connection_timeout)
        self.socket.bind((address, 0))
        self.socket.listen(2)
        self.diag = logging.getLogger("Binary TCP Traffic")
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
            pass

    def tryConnectServer(self):
        dest = clientservertraffic.ClientSocketTraffic.destination
        if dest is not None and self.serverConverter is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect(dest)
                self.serverConverter = BinaryTrafficConverter(self.dispatcher.rcHandler, sock, self.diag)
                self.diag.debug("Connected server %s", dest)
            except OSError:
                self.diag.debug("Connecting server failed %s", dest)

    def handle_client_traffic(self, text, payload=None):
        self.requestCount += 1
        traffic = BinaryClientSocketTraffic(text, None, rcHandler=self.dispatcher.rcHandler)
        responses = self.dispatcher.process(traffic, self.requestCount)
        if self.serverConverter and payload is not None:
            self.serverConverter.socket.sendall(payload)
        elif self.clientConverter:
            for response in responses:
                self.clientConverter.send_payload(response.text)

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
        self.clientConverter.socket.sendall(payload)

    def try_client(self):
        if self.clientConverter:
            try:
                text, payload = self.clientConverter.read_and_parse()
                self.handle_client_traffic(text, payload)
                return True
            except OSError:
                self.diag.debug("Read from client timed out")
        return False

    def try_server(self):
        if not self.serverConverter:
            self.tryConnectServer()
        if self.serverConverter:
            try:
                text, payload = self.serverConverter.read_and_parse()
                self.handle_server_traffic(text, payload)
                return True
            except OSError:
                self.diag.debug("Read from server timed out")
        return False

    def handle_server_traffic_from_cache(self):
        if len(self.serverTrafficCache):
            for cacheText, cachePayload in self.serverTrafficCache:
                self.handle_server_traffic_for_real(cacheText, cachePayload)
            self.serverTrafficCache.clear()

    def run(self):
        while not self.terminate:
            while self.try_server():
                pass

            if self.try_client():
                continue
            connSocket = self.acceptSocket()
            if connSocket:
                converter = BinaryTrafficConverter(self.dispatcher.rcHandler, connSocket, self.diag)
                try:
                    converter.read_header_or_text()
                except socket.timeout:
                    self.clientConverter = converter
                    self.handle_client_traffic("connect")
                    self.handle_server_traffic_from_cache()
                    self.diag.debug("Timeout client read, set socket")
                    continue
                if converter.text: # complete message, i.e. special for CaptureMock
                    self.dispatcher.processText(converter.text, None, self.requestCount)
                    self.diag.debug("Got message %s", converter.text)
                    self.tryConnectServer()
                else:
                    self.clientConverter = converter
                    payload = self.clientConverter.get_payload()
                    text = self.clientConverter.parse_body()
                    self.handle_client_traffic(text, payload)

    def shutdown(self):
        self.terminate = True

    @classmethod
    def sendTerminateMessage(cls, serverAddressStr):
        clientservertraffic.ClientSocketTraffic.sendTerminateMessage(serverAddressStr)

    @staticmethod
    def getTrafficClasses(incoming):
        if incoming:
            return [ clientservertraffic.ClassicServerStateTraffic, BinaryClientSocketTraffic ]
        else:
            return [ BinaryServerSocketTraffic, BinaryClientSocketTraffic ]


def get_header(line, headers):
    for header in headers:
        if line.startswith(header):
            return header

if __name__ == "__main__":
    fmt = "<2Bq2Ii$I3Bi$i$Bi$Ii$i$i[i$]"
    fmt = "<i[i$i[i$]i$Ii$i$i$B]"
    fmt = "<q3Ii[i$]BHIBHIdi$i$Ii[i$i$i$Bi$Ii$i$i[i$]i$Ii$i$i$B]i[i$]i$i$I"
    # fmt = "<5Ii$"
    # fmt = "<Ii$i$i$2I2BH"
    # fmt = "<4I2BH2Bq2Ii$I3Bi$i$Bi$Ii$i$i[i$]i$i$i$i$i$dI"
    # fmt = "<i[i$]"
    # rawBytes = b'HELFK\x00\x00\x00'
    # rawBytes = b'\x00\x00\x00\x00/\x00\x00\x00http://opcfoundation.org/UA/SecurityPolicy#None\xff\xff\xff\xff\xff\xff\xff\xff\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\xbe\x01\x00\x00@\x10\xd0\x02\x8es\xd9\x01\x01\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff\xe8\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x80\xee6\x00'
    # rawBytes = b'\x01\x00\x00\x00%\x00\x00\x00opc.tcp://0.0.0.0:0/freeopcua/server/'
    print(BinaryMessageConverter.split_format(fmt))
    logger = logging.getLogger("main")
    logger.setLevel(logging.DEBUG)
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

