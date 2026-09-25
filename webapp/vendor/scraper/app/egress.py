"""HTTPS CONNECT gateway: reject private addresses and connect to the checked IP.

No TLS interception. Optional HTTP upstream proxy must support CONNECT to IPs.
Only port 443 tunnels are permitted; plaintext HTTP assets are intentionally denied.
"""
import base64
import os
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.policy import public_addresses

connections = threading.BoundedSemaphore(64)


def upstream_url():
    path = os.getenv("UPSTREAM_PROXY_FILE")
    return Path(path).read_text().strip() if path else os.getenv("UPSTREAM_PROXY_URL", "")


def open_tunnel(host, port):
    addresses = public_addresses(host, port)
    upstream = upstream_url()
    if not upstream:
        # Prefer IPv4 for Docker networks without IPv6; fall back within checked results.
        for target_ip in sorted(addresses, key=lambda ip: ':' in ip):
            try:
                return socket.create_connection((target_ip, port), timeout=5)
            except OSError:
                continue
        raise OSError("No checked destination address was reachable")
    target_ip = sorted(addresses, key=lambda ip: ':' in ip)[0]
    authority = f"[{target_ip}]:{port}" if ":" in target_ip else f"{target_ip}:{port}"
    proxy = urlsplit(upstream)
    if proxy.scheme != "http" or not proxy.hostname or proxy.path not in ("", "/") or proxy.query or proxy.fragment:
        raise ValueError("Upstream must be an HTTP CONNECT proxy URL")
    sock = socket.create_connection((proxy.hostname, proxy.port or 8080), timeout=10)
    try:
        headers = f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
        if proxy.username is not None:
            auth = f"{unquote(proxy.username)}:{unquote(proxy.password or '')}"
            headers += "Proxy-Authorization: Basic " + base64.b64encode(auth.encode()).decode() + "\r\n"
        sock.sendall((headers + "\r\n").encode("ascii"))
        response = bytearray()
        while not response.endswith(b"\r\n\r\n") and len(response) < 16384:
            part = sock.recv(1)
            if not part:
                break
            response.extend(part)
        if not response.endswith(b"\r\n\r\n") or response.split(b"\r\n", 1)[0].split()[1:2] != [b"200"]:
            raise OSError("Upstream rejected CONNECT")
        return sock
    except Exception:
        sock.close()
        raise


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    rbufsize = 0

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never log target URLs or proxy credentials.

    def do_GET(self):
        self.send_error(403, "Only HTTPS CONNECT is allowed")

    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_HEAD = do_GET

    def do_CONNECT(self):
        self.close_connection = True
        if not connections.acquire(blocking=False):
            self.send_error(503)
            return
        remote = None
        established = False
        try:
            target = urlsplit("//" + self.path)
            if not target.hostname or target.port != 443 or target.username or target.path or target.query or target.fragment:
                raise ValueError("Only port 443 is allowed")
            remote = open_tunnel(target.hostname, 443)
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.wfile.flush()
            established = True
            deadline = time.monotonic() + 120
            transferred = 0
            while time.monotonic() < deadline and transferred < 64 * 1024 * 1024:
                readable, _, _ = select.select([self.connection, remote], [], [], 1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    transferred += len(data)
                    (remote if source is self.connection else self.connection).sendall(data)
        except (ValueError, OSError, UnicodeError):
            if not established:
                self.send_error(403, "Destination denied or unavailable")
        finally:
            if remote:
                remote.close()
            connections.release()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
