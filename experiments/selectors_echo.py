from socket import socket
from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
from selectors import DefaultSelector
from selectors import EVENT_READ, EVENT_WRITE

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
        self.type = type
        self.addr = addr


def init_listener(address):
    sock = socket(AF_INET, SOCK_STREAM)
    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    sock.bind(address)
    sock.setblocking(False)
    sock.listen(5)
    sel.register(sock, EVENT_READ, data=ConnectionState(sock=sock))

init_listener(('', 25000))


def accept_handler(sock):
    print(f'Accept handler for {sock} started')
    client, addr = sock.accept()
    client.setblocking(False)
    print('Start establishing the connection with', addr)
    sel.register(
        client,
        EVENT_READ,
        ConnectionState(sock=client, type='client', addr=addr)
    )

def echo_handler(connection: ConnectionState, mask):
        
    if mask & EVENT_READ:
        data: bytes = connection.sock.recv(1024)
        if not data:
            sel.unregister(connection.sock)
            connection.sock.close()
            return
        else:
            connection.buffer += data
            sel.modify(connection.sock, EVENT_READ | EVENT_WRITE, connection)
    if mask & EVENT_WRITE:
        sent = connection.sock.send(connection.buffer)
        print(f'echoed message: {connection.buffer[:sent]}')
        connection.buffer = connection.buffer[sent:]
        if len(connection.buffer) == 0:
            sel.modify(connection.sock, EVENT_READ, connection)

 
def run():
    while True:
        events = sel.select()
        for key, mask in events:
            if key.data.type == 'server':
                print('Socket', key.data.sock, 'is ready to accept client connections')
                accept_handler(key.data.sock)
            if key.data.type == 'client':
                echo_handler(key.data, mask)
run()


