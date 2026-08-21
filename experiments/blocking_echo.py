from socket import socket
from socket import SOL_SOCKET
from socket import SO_REUSEADDR
from socket import AF_INET
from socket import SOCK_STREAM


def echo_server(address):
    sock = socket(AF_INET, SOCK_STREAM)
    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    sock.bind(address)
    sock.listen(5)

    while True:
        client, addr = sock.accept()
        print("connection with", addr, "is established")
        with client:
            echo_handler(client, addr)


def echo_handler(client: socket, addr):
    while True:
        data = client.recv(1024)
        if not data:
            print(f"Connection with {addr} is closed")
            client.close()
            break
        bytes_send = client.send(data)
        print(str(bytes_send), "sent")


if __name__ == "__main__":
    echo_server(("", 25000))
