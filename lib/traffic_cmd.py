#!/usr/bin/env python

import sys, os, string, socket

def createSocket():
    servAddr = os.getenv("TEXTTEST_MIM_SERVER")
    if servAddr:
        host, port = servAddr.split(":")
        serverAddress = (host, int(port))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(serverAddress)
        return sock

def sendServerState(stateDesc):
    sock = createSocket()
    if sock:
        sock.sendall("SUT_SERVER:" + stateDesc + "\n")
        sock.close()

def getResponse(sock):
    response = sock.recv(2048)
    if len(response) < 2048 and not response.endswith("|TT_CMD_SEP|"):
        return response
    else:
        return response + getResponse(sock)

if __name__ == "__main__":
    sock = createSocket()
    text = "SUT_COMMAND_LINE:" + repr(sys.argv) + ":SUT_ENVIRONMENT:" + repr(os.environ)
    sock.sendall(text)
    sock.shutdown(1)
    response = getResponse(sock)
    sock.close()
    try:
        stdout, stderr, exitStr = response.split("|TT_CMD_SEP|")
        sys.stdout.write(stdout)
        sys.stdout.flush()
        sys.stderr.write(stderr)
        sys.stderr.flush()
        exitCode = int(exitStr)
        if os.name == "posix":
            exitCode = os.WEXITSTATUS(exitCode)
        sys.exit(exitCode)
    except ValueError:
        sys.stderr.write("Received unexpected communication from MIM server:\n " + response + "\n\n")
