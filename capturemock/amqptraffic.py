
import pika
from capturemock import traffic, encodingutils
from datetime import datetime
import sys

class AMQPConnector:
    own_routing_key = "CaptureMock"
    terminate_body = b"terminate"
    def __init__(self, rcHandler=None, servAddr=None, connName=None):
        if rcHandler is not None:
            self.url = rcHandler.get("url", [ "amqp" ])
            self.exchange = rcHandler.get("exchange", [ "amqp" ])
            self.exchange_type = rcHandler.get("exchange_type", [ "amqp" ])
            self.exchange_forward = rcHandler.get("exchange_forward", [ "amqp" ])
            self.exchange_forward_prefix = rcHandler.get("exchange_forward_prefix", [ "amqp" ])
            self.auto_delete = rcHandler.getboolean("auto_delete", [ "amqp" ], True)
            self.durable = rcHandler.getboolean("durable", [ "amqp" ], True)
        else:
            self.url, self.exchange = servAddr.rsplit("/", 1)
            self.exchange_type = None
            self.exchange_forward = None
            self.exchange_forward_prefix = None
            self.auto_delete = True
            self.durable = True

        params = pika.URLParameters(self.url)
        if connName:
            params.client_properties = { 'connection_name' : connName }
        try:
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()
            if self.exchange_type:
                self.channel.exchange_declare(self.exchange, exchange_type=self.exchange_type, durable=self.durable, auto_delete=self.auto_delete)
            if self.exchange_forward and not self.exchange_forward_prefix:
                self.channel.exchange_bind(self.exchange_forward, self.exchange, routing_key="#")
        except pika.exceptions.AMQPConnectionError as e:
            print("Failed to connect to RabbitMQ at", self.url, file=sys.stderr)
            print(e.args, file=sys.stderr)
            self.connection = None
            self.channel = None
        
    def get_queue_name(self):
        return self.exchange + ".capturemock"

    def setup_recording(self, on_message):
        if self.channel:
            queue = self.get_queue_name()
            self.channel.queue_declare(queue, durable=True, auto_delete=True)
            self.channel.queue_bind(queue, self.exchange, routing_key="#")
            self.channel.basic_consume(queue, on_message)        
    
    def record_from_queue(self):
        if self.channel:
            try:
                self.channel.start_consuming()
            except KeyboardInterrupt:
                self.channel.stop_consuming()
        if self.connection:
            self.connection.close()
        
    def replay(self, routing_key, body, props, headers):
        if routing_key or self.exchange_type != "topic":
            properties = pika.BasicProperties(headers=headers, **props)
            self.channel.basic_publish(self.exchange, routing_key, body, properties=properties)
        
    def try_forward_with_prefix(self, routing_key, body, props, headers):
        if self.exchange_forward_prefix:
            properties = pika.BasicProperties(headers=headers, **props)
            self.channel.basic_publish(self.exchange_forward, self.exchange_forward_prefix + "." + routing_key, body, properties=properties)
        
    def sendTerminateMessage(self):
        try:
            if self.channel:
                properties = pika.BasicProperties(content_type='text/plain', delivery_mode=pika.spec.TRANSIENT_DELIVERY_MODE)
                self.channel.basic_publish(self.exchange, self.own_routing_key, self.terminate_body, properties=properties, mandatory=True)
        except pika.exceptions.UnroutableError as e:
            print("Failed to send terminate message to CaptureMock!\n", e, file=sys.stderr)
        
    def isTermination(self, routing_key, body):
        return routing_key == self.own_routing_key and body == self.terminate_body
    
    def terminate(self):
        self.channel.stop_consuming()
        queue = self.get_queue_name()
        self.channel.queue_delete(queue)
        self.channel.exchange_delete(self.exchange)
        
    def getAddress(self):
        return self.url + "/" + self.exchange



class AMQPTrafficServer:
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls(dispatcher)
    
    def __init__(self, dispatcher):
        self.count = 0
        self.dispatcher = dispatcher
        self.dispatcher.diag.debug("Starting AMQP Connector for recording...")
        self.connector = AMQPConnector(self.dispatcher.rcHandler, connName="CaptureMock recorder")
        self.dispatcher.diag.debug("Setting up recording")
        self.connector.setup_recording(self.on_message)
        
    def on_message(self, channel, method_frame, header_frame, body):
        self.count += 1
        routing_key = method_frame.routing_key
        self.dispatcher.diag.debug("Received AMQP message with routing key %s", routing_key)
        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
        if self.connector.isTermination(routing_key, body):
            self.dispatcher.diag.debug("Message is termination, stop recording and clean up...")
            self.connector.terminate()
        else:
            traffic = AMQPTraffic(rcHandler=self.dispatcher.rcHandler, routing_key=routing_key, body=body, props=header_frame)
            traffic.try_forward_with_prefix(self.connector)
            self.dispatcher.process(traffic, self.count)
        
    def run(self):
        self.connector.record_from_queue()

    def getAddress(self):
        return self.connector.getAddress()

    def setShutdownFlag(self):
        pass

    @staticmethod
    def getTrafficClasses(incoming):
        return [ AMQPTraffic ]
    
    @classmethod
    def sendTerminateMessage(cls, servAddr):
        connector = AMQPConnector(servAddr=servAddr, connName="CaptureMock terminator")
        connector.sendTerminateMessage()


class AMQPTraffic(traffic.Traffic):
    direction = "<-"
    socketId = ""
    typeId = "RMQ"
    headerStr = "\n--HEA:"
    connector = None
    default_props = { "delivery_mode": 2, "priority": 0 }
    def __init__(self, text=None, responseFile=None, rcHandler=None, routing_key=None, body=b"", origin=None, props=None):
        self.replay = routing_key is None
        self.origin = origin
        sep = " : "
        timestamp = None
        self.headers = {}
        self.props = {}
        record_timestamps = rcHandler and rcHandler.getboolean("record_timestamps", [ "general" ], False)
        if not self.replay:
            self.routing_key = routing_key or self.exchange_routing_key(rcHandler)
            self.body = body
            self.props = self.make_props_dict(props)
            text = self.routing_key + sep + ",".join((k + "=" + v for k, v in self.props.items())) + "\n"
            text += encodingutils.decodeBytes(body)
            if record_timestamps:
                timestamp = props.headers.get("timestamp")
                if timestamp is None:
                    timestamp = datetime.now().isoformat()
            for header, value in sorted(props.headers.items()):
                if header != "timestamp":
                    text += self.headerStr + header + "=" + value
                if header == "originfile":
                    self.origin = value
        elif record_timestamps:
            timestamp = datetime.now().isoformat()
        traffic.Traffic.__init__(self, text, responseFile, rcHandler, timestamp=timestamp)
        self.text = self.applyAlterations(self.text)
        if self.replay:
            lines = self.text.splitlines()
            routing_key, propText = lines[0].split(sep)
            self.props.update(self.default_props)
            for part in propText.split(","):
                key, value = part.split("=")
                self.props[key] = value
            self.routing_key = routing_key if not routing_key.startswith("exchange=") else "" 
            bodyStr = self.extractHeaders("\n".join(lines[1:]))
            self.body = encodingutils.encodeString(bodyStr)
            self.rcHandler = rcHandler
            
    def make_props_dict(self, basic_properties):
        props = {}
        for key, value in sorted(basic_properties.__dict__.items()):
            if key not in [ "timestamp", "headers" ] and value != self.default_props.get(key):
                props[key] = str(value)
        return props
            
    def exchange_routing_key(self, rcHandler):
        exchange_record = rcHandler.get("exchange_record", [ "amqp" ])
        return "exchange=" + exchange_record if exchange_record else ""
            
    def extractHeaders(self, textStr):
        if self.headerStr in textStr:
            parts = textStr.split(self.headerStr)
            for headerStr in parts[1:]:
                header, value = headerStr.strip().split("=", 1)
                self.headers[header] = value
            return self.stripNewline(parts[0])
        else:
            return self.stripNewline(textStr)
        
    def stripNewline(self, text):
        return text[:-1] if text.endswith("\n") else text
        
    def forwardToDestination(self):
        # Replay and record handled entirely separately, unlike most other traffic, due to how MQ brokers work
        if self.replay:
            if AMQPTraffic.connector is None:
                AMQPTraffic.connector = AMQPConnector(self.rcHandler, connName="CaptureMock replay")
                
            if self.origin:
                self.headers["originfile"] = self.origin
            self.connector.replay(self.routing_key, self.body, self.props, self.headers)
        return []
    
    def try_forward_with_prefix(self, connector):
        connector.try_forward_with_prefix(self.routing_key, self.body, self.props, self.headers)
    
    def shouldBeRecorded(self, *args):
        return not self.replay and self.origin != "norecord" # never record these when replaying, must do it in one place
            
    @classmethod
    def isClientClass(cls):
        return True
    
class AMQPResponseTraffic(AMQPTraffic):
    direction = "->"
    def __init__(self, text, responseFile, rcHandler):
        AMQPTraffic.__init__(self, text, responseFile, rcHandler, origin="norecord")
