from socket import socket
from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
from selectors import DefaultSelector
from selectors import EVENT_READ, EVENT_WRITE
from functools import partial

sel = DefaultSelector()

# What the accept loop can survive: a peer that connected and vanished before
# we got to it. Anything else on the listener means the server itself is broken.
TRANSIENT_ACCEPT_ERRORS = (
    ConnectionAbortedError,
    ConnectionResetError,
    InterruptedError,
)


class ConnectionState:
    def __init__(self, sock: socket, buffer: bytes = b"", addr=None):
        self.sock = sock
        self.buffer = buffer
        self.addr = addr
        # Built once, here, so key.data is always the same tagged callable.
        # Rebuilding it per event is how the role tag went missing before.
        self.handler = partial(echo_handler, self)
        self.handler.role = "echo"


def close_connection(sock: socket):
    """Tear down one connection. Unregister *before* close: once the socket is
    closed its fileno() is -1, and the selector can then only find the key via
    an exhaustive fallback search. KeyError = someone already tore it down."""
    try:
        sel.unregister(sock)
    except KeyError:
        pass
    sock.close()


def accept_handler(sock, mask):
    client, addr = sock.accept()
    client.setblocking(False)
    print("Start establishing the connection with", addr)
    connection = ConnectionState(sock=client, addr=addr)
    sel.register(client, EVENT_READ, connection.handler)


def init_listener(address):
    sock = socket(AF_INET, SOCK_STREAM)
    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    sock.bind(address)
    sock.setblocking(False)
    sock.listen(5)
    handler = partial(accept_handler, sock)
    handler.role = "listener"
    sel.register(sock, EVENT_READ, data=handler)


def echo_handler(connection: ConnectionState, mask):
    if mask & EVENT_READ:
        data: bytes = connection.sock.recv(1024)
        if not data:
            # Deliberate: whatever is still in connection.buffer gets dropped.
            # The peer half-closed, but its receive side may still be open and
            # waiting for those bytes. A real server flushes first, then closes.
            close_connection(connection.sock)
            return
        else:
            connection.buffer += data
            sel.modify(connection.sock, EVENT_READ | EVENT_WRITE, connection.handler)
    if mask & EVENT_WRITE:
        sent = connection.sock.send(connection.buffer)
        print(f"echoed message: {connection.buffer[:sent]}")
        connection.buffer = connection.buffer[sent:]
        if len(connection.buffer) == 0:
            sel.modify(connection.sock, EVENT_READ, connection.handler)


def dispatch(key, mask):
    """Run one handler. Returns normally if the loop should keep going;
    raises only when the failure is the *server's*, not a connection's."""
    role = getattr(key.data, "role", "echo")
    try:
        key.data(mask)
    except BlockingIOError:
        # select() said ready, the kernel changed its mind between the two
        # calls. Not an error: return and wait for the next readiness event.
        return
    except OSError as e:
        print(f"{role} error: {e!r}")
        if role == "listener":
            if isinstance(e, TRANSIENT_ACCEPT_ERRORS):
                return
            raise  # the listener is gone; dying loudly beats a process
            # that is alive but will never accept anything again
        close_connection(key.fileobj)


def run():
    try:
        while True:
            for key, mask in sel.select():
                dispatch(key, mask)
    except KeyboardInterrupt:
        print("\ninterrupted, shutting down")
    finally:
        sel.close()


if __name__ == "__main__":
    init_listener(("", 25000))
    run()
