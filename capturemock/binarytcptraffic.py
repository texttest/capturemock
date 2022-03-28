
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
        header = self.socket.recv(self.headerConverter.length)
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
                success, self.header_fields = self.headerConverter.parse(header)
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
        if msg_size % 2 == 1: # ACI format does not allow odd message size...
            msg_size += 1
        header_size = self.header_fields.get("header_size")
        if msg_size and header_size:
            msg_already_read = self.headerConverter.length - header_size
            return msg_size - msg_already_read
        else:
            return self.header_fields.get("length")
        
    def get_body_to_parse(self):
        length = self.header_fields.get("length")
        if len(self.body) > length:
            return self.body[:length]
        else:
            return self.body
        
    def parse_body(self):
        bodyReader = BinaryMessageConverter(self.rcHandler, self.header_fields.get("type"))
        _, body_values = bodyReader.parse(self.get_body_to_parse())
        self.diag.debug("Got body %s", body_values)
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
        bodyConv = BinaryMessageConverter(self.rcHandler, self.header_fields.get("type"))
        body_values = eval(remainder)
        payload = bodyConv.fields_to_payload(body_values)
        self.header_fields["length"] = len(payload)
        body_length = self.get_body_length()
        while(len(payload)) < body_length:
            payload += b"\x00"
        header_payload = self.headerConverter.fields_to_payload(self.header_fields)
        return header_payload + payload
    
    def send_payload(self, text):
        self.diag.debug("Payload text %s", text)
        payload = self.convert_to_payload(text)
        self.diag.debug("Sending payload %s", payload)
        self.socket.sendall(payload)
                
        
class BinaryMessageConverter:
    def __init__(self, rcHandler, rawSection):
        section = self.toString(rawSection)
        self.rcHandler = rcHandler
        self.fields = rcHandler.getList("fields", [ section ])
        self.formats = rcHandler.getList("format", [ section ])
        self.assume = self.readDictionary(rcHandler, "assume", section)
        self.enforce = self.readDictionary(rcHandler, "enforce", section)
        self.assume.update(self.enforce)
        self.length = struct.calcsize(self.formats[0]) if self.formats else 0
    
    def readDictionary(self, rcHandler, key, section):
        ret = {}
        for part in rcHandler.getList(key, [ section ]):
            key, value = part.split("=")
            ret[key] = value
        return ret
    
    def getHeaderDescription(self, fields):
        msgType = fields.get("type")
        filtered_fields = {}
        for key, value in fields.items():
            if key not in [ "type", "length" ] and self.assume.get(key) != str(value):
                filtered_fields[key] = value
        return self.toString(msgType) + " " + repr(filtered_fields)

    def parseHeaderDescription(self, text):
        typeText, fieldText = text.split(" ", 1)
        fields = eval(fieldText)
        fields["type"] = typeText.encode()
        for key, value in self.assume.items():
            if key not in fields:
                fields[key] = int(value) if value.isdigit() else value
        return fields
    
    def fields_to_payload(self, values):
        data = []
        for field in self.fields:
            if field in values:
                data.append(self.try_enum_to_payload(field, values[field]))
            
        if "additional_params" in values:
            data += values["additional_params"]
        elif "parameters" in values:
            data += values["parameters"]
            
        for fmt in self.formats:
            try:
                return struct.pack(fmt, *data)
            except struct.error:
                pass
            
    def get_parameter_key(self, values):
        return 

    def toString(self, data):
        if isinstance(data, bytes):
            return data.decode()
        else:
            return str(data)
        
    def parse(self, rawBytes):
        data = None
        for fmt in self.formats:
            try:
                data = struct.unpack_from(fmt, rawBytes)
                break
            except struct.error:
                pass
        if data is None:
            return False, { "unknown_format" : rawBytes.hex() }
        
        field_values = {}
        for i, field in enumerate(self.fields):
            if i < len(data):
                value = self.try_convert_enum(field, data[i])
                field_values[field] = value
        parameters = list(data[len(field_values):])
        if len(parameters) > 0:
            key = "additional_params" if "parameters" in field_values else "parameters"
            field_values[key] = parameters
        ok = True
        for key, value in self.enforce.items():
            if str(field_values.get(key)) != value:
                ok = False
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
    from capturemock import config
    rc = r"D:\texttest_repos\tone\capturemock-any-systemmanager.rc"
    rcHandler = config.RcFileHandler([ rc ])
    diag = logging.getLogger("test")
    conv = BinaryTrafficConverter(rcHandler, None, diag)
    fn = r"D:\TT\tone.28Mar154841.35540\tone\Transports\PetainerData\SetIO\cpmock_binarytcp.txt"
    curr_payload = b""
    curr_text = ""
    recording = False
    count = 1
    failed = []
    prefix = "Binary TCP Traffic DEBUG - "
    text_headers = [ prefix + "Payload text ", prefix + "Recording " ]
    payload_header = prefix + "Sending payload "
    header_header = prefix + "Got header "
    body_header = prefix + "Got body of size "
    end_header = prefix + "Server traffic, no client"
    with open(fn) as f:
        for line in f:
            text_header = get_header(line, text_headers)
            if text_header:
                curr_text = line[len(text_header):]
                recording = True
            elif line.startswith(header_header):
                curr_payload += eval(line.split()[7])
            elif line.startswith(body_header):
                curr_payload += eval(line.split(" ", 10)[10])
            elif line.startswith(payload_header) or line.startswith(end_header):
                if line.startswith(payload_header):
                    curr_payload = eval(line[len(payload_header):])
                print(count, "compare", curr_text, "with")
                print(curr_payload)
                new_payload = conv.convert_to_payload(curr_text)
                print(new_payload)
                if new_payload == curr_payload:
                    print("SUCCESS")
                else:
                    print("FAILURE")
                    failed.append((curr_text, curr_payload, new_payload))
                recording = False
                curr_payload = b""
                curr_text = ""
                count += 1
            elif recording:
                curr_text += line
    print("\nFAILED:")
    class FakeSocket:
        def __init__(self, rawBytes):
            self.rawBytes = rawBytes
            
        def recv(self, count):
            ret = self.rawBytes[:count]
            self.rawBytes = self.rawBytes[count:]
            return ret
        
        def settimeout(self, *args):
            pass
    
    for curr_text, curr_payload, new_payload in failed:
        print("\n" + curr_text)
        print("got:", len(new_payload), new_payload)
        print("not:", len(curr_payload), curr_payload)
        fakeSock = FakeSocket(curr_payload)
        conv = BinaryTrafficConverter(rcHandler, fakeSock, diag)
        text, _ = conv.read_and_parse()
        print("\nNew:", text)
                
