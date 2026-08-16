from socket import socket
from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
from selectors import DefaultSelector
from selectors import EVENT_READ, EVENT_WRITE
from functools import partial

sel = DefaultSelector()

class ConnectionState:
    def __init__(
        self, sock: socket,
        buffer: bytes = b'',
        type: str = 'server',
        addr = None
    ):
        self.sock = sock
        self.buffer = buffer
        self.addr = addr


def accept_handler(sock, mask):
    print(f'Accept handler for {sock} started')
    client, addr = sock.accept()
    client.setblocking(False)
    print('Start establishing the connection with', addr)
    connection = ConnectionState(sock=client, type='client', addr=addr)
    handler = partial(echo_handler, connection)
    sel.register(client, EVENT_READ, handler)


def init_listener(address):
    sock = socket(AF_INET, SOCK_STREAM)
    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    sock.bind(address)
    sock.setblocking(False)
    sock.listen(5)
    handler = partial(accept_handler, sock)
    sel.register(sock, EVENT_READ, data=handler)

init_listener(('', 25000))


def echo_handler(connection: ConnectionState, mask):
    handler = partial(echo_handler, connection)
        
    if mask & EVENT_READ:
        data: bytes = connection.sock.recv(1024)
        if not data:
            sel.unregister(connection.sock)
            connection.sock.close()
            return
        else:
            connection.buffer += data
            sel.modify(
                connection.sock,
                EVENT_READ | EVENT_WRITE,
                handler
            )
    if mask & EVENT_WRITE:
        sent = connection.sock.send(connection.buffer)
        print(f'echoed message: {connection.buffer[:sent]}')
        connection.buffer = connection.buffer[sent:]
        if len(connection.buffer) == 0:
            sel.modify(connection.sock, EVENT_READ, handler)

 
def run():
    while True:
        events = sel.select()
        for key, mask in events:
            callback = key.data
            callback(mask)
run()
