
import pika
from capturemock import traffic, encodingutils
from datetime import datetime

class AMQPConnector:
    def __init__(self, rcHandler):
        self.url = rcHandler.get("url", [ "amqp" ])
        self.exchange = rcHandler.get("exchange", [ "amqp" ])
        self.exchange_type = rcHandler.get("exchange_type", [ "amqp" ])
        params = pika.URLParameters(self.url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        self.channel.exchange_declare(self.exchange, exchange_type=self.exchange_type, durable=True, auto_delete=True)
        
    def record_from_queue(self, on_message):
        queue = self.exchange + ".capturemock"
        self.channel.queue_declare(queue, durable=True, auto_delete=True)
        self.channel.queue_bind(queue, self.exchange, routing_key="#")
        self.channel.basic_consume(queue, on_message)
        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            self.channel.stop_consuming()
        self.connection.close()
        
    def replay(self, routing_key, body, msgType, origin_server):
        headers = {}
        if origin_server:
            headers["traceparent"] = origin_server
        properties = pika.BasicProperties(headers=headers, type=msgType)
        self.channel.basic_publish(self.exchange, routing_key, body, properties=properties)



class AMQPTrafficServer:
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls(dispatcher)
    
    def __init__(self, dispatcher):
        self.count = 0
        self.dispatcher = dispatcher
        self.connector = AMQPConnector(self.dispatcher.rcHandler)
        
    def on_message(self, channel, method_frame, header_frame, body):
        self.count += 1
        traffic = AMQPTraffic(rcHandler=self.dispatcher.rcHandler, routing_key=method_frame.routing_key, body=body, props=header_frame)
        self.dispatcher.process(traffic, self.count)
        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
    
    def run(self):
        self.connector.record_from_queue(self.on_message)

    def getAddress(self):
        return "none"

    def setShutdownFlag(self):
        pass

    @staticmethod
    def getTrafficClasses(incoming):
        return [ AMQPTraffic ]

class AMQPTraffic(traffic.Traffic):
    direction = "<-"
    socketId = ""
    typeId = "RMQ"
    headerStr = "\n--HEA:"
    connector = None
    def __init__(self, text=None, responseFile=None, rcHandler=None, routing_key=None, body=b"", origin_server=None, props=None):
        self.replay = routing_key is None
        self.origin_server = origin_server
        sep = " : type="
        if self.replay: # replay
            lines = text.splitlines()
            self.routing_key, self.msgType = lines[0].split(sep)
            self.body = encodingutils.encodeString("\n".join(lines[1:]))
            self.rcHandler = rcHandler
        else:
            self.routing_key = routing_key
            self.body = body
            self.msgType = props.type
            text = routing_key + sep + self.msgType +"\n"
            text += encodingutils.decodeBytes(body)
            for header, value in props.headers.items():
                text += self.headerStr + header + "=" + value
        if rcHandler and rcHandler.getboolean("record_timestamps", [ "general" ], False):
            text += "\n--TIM:" + datetime.now().isoformat()
        traffic.Traffic.__init__(self, text, responseFile, rcHandler)
        
    def forwardToDestination(self):
        # Replay and record handled entirely separately, unlike most other traffic, due to how MQ brokers work
        if self.replay:
            if AMQPTraffic.connector is None:
                AMQPTraffic.connector = AMQPConnector(self.rcHandler)
            self.connector.replay(self.routing_key, self.body, self.msgType, self.origin_server)
        return []
            

    @classmethod
    def isClientClass(cls):
        return True
        
