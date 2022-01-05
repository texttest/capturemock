
import pika
from capturemock import traffic
import sys
from locale import getpreferredencoding

class AMQPTrafficServer:
    @classmethod
    def createServer(cls, address, dispatcher):
        return cls(dispatcher)
    
    def __init__(self, dispatcher):
        self.count = 0
        self.dispatcher = dispatcher
        self.url = self.dispatcher.rcHandler.get("url", [ "amqp" ])
        self.exchange = self.dispatcher.rcHandler.get("exchange", [ "amqp" ])
        self.queue = self.exchange + ".capturemock"
        self.exchange_type = self.dispatcher.rcHandler.get("exchange_type", [ "amqp" ])
        
    def on_message(self, channel, method_frame, header_frame, body):
        self.count += 1
        traffic = AMQPTraffic(method_frame.routing_key, header_frame.headers, body)
        self.dispatcher.process(traffic, self.count)
        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
    
    def run(self):
        params = pika.URLParameters(self.url)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(self.exchange, exchange_type=self.exchange_type, durable=True, auto_delete=True)
        channel.queue_declare(self.queue, durable=True, auto_delete=True)
        channel.queue_bind(self.queue, self.exchange, routing_key="#")
        channel.basic_consume(self.queue, self.on_message)
        try:
            channel.start_consuming()
        except KeyboardInterrupt:
            channel.stop_consuming()
        connection.close()

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
    def __init__(self, routing_key, headers, body):
        self.routing_key = routing_key
        self.headers = headers
        text = routing_key + "\n"
        text += body.decode(getpreferredencoding())
        for header, value in self.headers.items():
            text += self.headerStr + header + "=" + value
        traffic.Traffic.__init__(self, text, None)
        
