import socket
import threading
from http.server import ThreadingHTTPServer
import pytest
from app import egress


@pytest.fixture
def proxy():
    server = ThreadingHTTPServer(('127.0.0.1', 0), egress.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server.server_address
    server.shutdown()
    server.server_close()
    worker.join(timeout=3)


def headers(sock):
    data = b''
    while not data.endswith(b'\r\n\r\n'):
        piece = sock.recv(1)
        if not piece:
            break
        data += piece
    return data


@pytest.mark.parametrize('target', ['127.0.0.1:443', '169.254.169.254:443', '168.63.129.16:443', 'example.com:80'])
def check_real_socket_denies_private_and_wrong_port(proxy, target):
    with socket.create_connection(proxy, timeout=3) as client:
        client.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n'.encode())
        assert b'403' in headers(client).split(b'\r\n')[0]


def check_http_denied(proxy):
    with socket.create_connection(proxy, timeout=3) as client:
        client.sendall(b'GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n')
        assert b'403' in headers(client).split(b'\r\n')[0]


def check_bidirectional_tunnel(proxy, monkeypatch):
    remote, peer = socket.socketpair()
    peer.settimeout(3)
    monkeypatch.setattr(egress, 'open_tunnel', lambda *a: remote)
    with peer, socket.create_connection(proxy, timeout=3) as client:
        client.sendall(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n')
        assert b'200' in headers(client).split(b'\r\n')[0]
        client.sendall(b'hello')
        assert peer.recv(5) == b'hello'
        peer.sendall(b'world')
        assert client.recv(5) == b'world'
