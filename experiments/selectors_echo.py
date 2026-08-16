from socket import socket
from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
from selectors import DefaultSelector
from selectors import EVENT_READ, EVENT_WRITE
from functools import partial

sel = DefaultSelector()

class ConnectionState:
    def __init__(
        self, sock: socket, buffer: bytes = b'', addr = None
    ):
        self.sock = sock
        self.buffer = buffer
        self.addr = addr


def accept_handler(sock, mask):
    print(f'Accept handler for {sock} started')
    client, addr = sock.accept()

    client.setblocking(False)
    print('Start establishing the connection with', addr)
    connection = ConnectionState(sock=client, addr=addr)
    handler = partial(echo_handler, connection)
    sel.register(client, EVENT_READ, handler)


def init_listener(address):
    sock = socket(AF_INET, SOCK_STREAM)
    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    sock.bind(address)
    sock.setblocking(False)
    sock.listen(5)
    handler = partial(accept_handler, sock)
    handler.type = 'listener'
    sel.register(sock, EVENT_READ, data=handler)


def echo_handler(connection: ConnectionState, mask):
    handler = partial(echo_handler, connection)
    handler.type = 'echo'
        
    if mask & EVENT_READ:
        data: bytes = connection.sock.recv(1024)
        if not data:
            connection.sock.close()
            sel.unregister(connection.sock)
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
        try:
            events = sel.select()
        except KeyboardInterrupt:
            print('lolkek cheburek')
            sel.close()
            break
        for key, mask in events:
            callback = key.data
            try:
                callback(mask)
            except BlockingIOError as e:
                print('Error:', e)
                continue
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
                print('Error:', e)
                if key.data.type == 'listener':
                    continue
                sock = key.fileobj
                sel.unregister(key.fileobj)
                sock.close()


if __name__ == '__main__':
    init_listener(('', 25000))
    run()
