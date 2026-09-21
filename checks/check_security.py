import socket
from unittest.mock import Mock

import pytest

from app import egress, policy


def dns(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in ips]


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '168.63.129.16', '192.168.0.1', '172.16.0.1', '0.0.0.0', '::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1', '64:ff9b::a00:1'])
def check_private_dns_denied(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: dns(ip))
    with pytest.raises(ValueError):
        policy.public_addresses('example.com')


def check_mixed_dns_denied(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: dns('93.184.216.34', '10.0.0.1'))
    with pytest.raises(ValueError):
        policy.public_addresses('example.com')


@pytest.mark.parametrize('url', ['http://example.com', 'https://example.com:444', 'https://user:pass@example.com', 'https://example.com.evil.org', 'file:///etc/passwd', 'https://example.com\\@127.0.0.1', 'https://example.com/\nfoo'])
def check_bad_urls(url):
    with pytest.raises(ValueError):
        policy.validate_url(url, {'example.com'})


def check_dns_connection_is_pinned(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: dns('93.184.216.34'))
    connect = Mock()
    monkeypatch.setattr(socket, 'create_connection', connect)
    monkeypatch.setattr(egress, 'upstream_url', lambda: '')
    egress.open_tunnel('example.com', 443)
    connect.assert_called_once_with(('93.184.216.34', 443), timeout=5)


def check_upstream_credentials_and_pinned_target(monkeypatch):
    monkeypatch.setattr(egress, 'public_addresses', lambda *a: ['93.184.216.34'])
    monkeypatch.setattr(egress, 'upstream_url', lambda: 'http://user:p%40ss@proxy.example:8080')
    upstream = Mock()
    upstream.recv.side_effect = [bytes([b]) for b in b'HTTP/1.1 200 OK\r\n\r\n']
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: upstream)
    assert egress.open_tunnel('example.com', 443) is upstream
    sent = upstream.sendall.call_args.args[0]
    assert b'CONNECT 93.184.216.34:443' in sent
    assert b'Proxy-Authorization: Basic dXNlcjpwQHNz' in sent
