
import pika
from capturemock import traffic, encodingutils
from datetime import datetime

class AMQPConnector:
    own_routing_key = "CaptureMock"
    terminate_body = b"terminate"
    def __init__(self, rcHandler=None, servAddr=None):
        if rcHandler is not None:
            self.url = rcHandler.get("url", [ "amqp" ])
            self.exchange = rcHandler.get("exchange", [ "amqp" ])
            self.exchange_type = rcHandler.get("exchange_type", [ "amqp" ])
        else:
            self.url, self.exchange = servAddr.rsplit("/", 1)
            self.exchange_type = None

        params = pika.URLParameters(self.url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        if self.exchange_type:
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
        
    def replay(self, routing_key, body, msgType, headers):
        properties = pika.BasicProperties(headers=headers, type=msgType)
        self.channel.basic_publish(self.exchange, routing_key, body, properties=properties)

    def sendTerminateMessage(self):
        self.channel.basic_publish(self.exchange, self.own_routing_key, self.terminate_body)

    def isTermination(self, routing_key, body):
        return routing_key == self.own_routing_key and body == self.terminate_body
    
    def terminate(self):
        self.channel.stop_consuming()
        
    def getAddress(self):
        return self.url + "/" + self.exchange



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
        routing_key = method_frame.routing_key
        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
        if self.connector.isTermination(routing_key, body):
            self.connector.terminate()
        else:
            traffic = AMQPTraffic(rcHandler=self.dispatcher.rcHandler, routing_key=routing_key, body=body, props=header_frame)
            self.dispatcher.process(traffic, self.count)
        
    def run(self):
        self.connector.record_from_queue(self.on_message)

    def getAddress(self):
        return self.connector.getAddress()

    def setShutdownFlag(self):
        pass

    @staticmethod
    def getTrafficClasses(incoming):
        return [ AMQPTraffic ]
    
    @classmethod
    def sendTerminateMessage(cls, servAddr):
        connector = AMQPConnector(servAddr=servAddr)
        connector.sendTerminateMessage()


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
            if rcHandler and rcHandler.getboolean("record_timestamps", [ "general" ], False):
                timestamp = props.headers.get("timestamp") or datetime.now().isoformat()
                text += "\n--TIM:" + timestamp
            for header, value in props.headers.items():
                if header != "timestamp":
                    text += self.headerStr + header + "=" + value
        traffic.Traffic.__init__(self, text, responseFile, rcHandler)
        
    def forwardToDestination(self):
        # Replay and record handled entirely separately, unlike most other traffic, due to how MQ brokers work
        if self.replay:
            if AMQPTraffic.connector is None:
                AMQPTraffic.connector = AMQPConnector(self.rcHandler)
                
            headers = {}
            if self.origin_server:
                headers["traceparent"] = self.origin_server
            if self.rcHandler.getboolean("record_timestamps", [ "general" ], False):
                headers["timestamp"] = datetime.now().isoformat()
            self.connector.replay(self.routing_key, self.body, self.msgType, headers)
        return []
            

    @classmethod
    def isClientClass(cls):
        return True
        
