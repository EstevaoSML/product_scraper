"""Shared URL policy. DNS is checked again and pinned at the egress proxy."""
import ipaddress
import socket
from urllib.parse import urlsplit


class URLPolicyError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def public_addresses(host: str, port: int = 443) -> list[str]:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except OSError:
        raise URLPolicyError("dns_resolution_failed", "DNS could not resolve the destination; check DNS and retry") from None
    denied = [ipaddress.ip_network(cidr) for cidr in ('168.63.129.16/32', '64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32')]
    if not addresses or any(not ipaddress.ip_address(ip).is_global or any(ipaddress.ip_address(ip) in net for net in denied) for ip in addresses):
        raise URLPolicyError("non_public_destination", "Destination must resolve exclusively to public IP addresses")
    return addresses


def configured_hosts(mode: str, hosts: str) -> set[str] | None:
    if mode == "public":
        return None
    if mode != "allowlist":
        raise ValueError("URL_POLICY must be public or allowlist")
    allowed = {h.strip().lower().rstrip(".").encode("idna").decode("ascii") for h in hosts.split(",") if h.strip()}
    if not allowed:
        raise ValueError("ALLOWED_HOSTS must not be empty in allowlist mode")
    return allowed


def validate_url_structure(url: str, allowed_hosts: set[str] | None = None) -> str:
    """Validate syntax and host policy without DNS in the isolated browser.

    Network destinations must still be resolved, checked and pinned by egress.
    """
    if any(ord(c) < 33 for c in url) or "\\" in url:
        raise URLPolicyError("invalid_url", "Use a plain HTTPS URL without whitespace or backslashes")
    try:
        parts = urlsplit(url)
        valid = parts.scheme == "https" and parts.hostname and parts.port in (None, 443)
        host = (parts.hostname or "").lower().rstrip(".").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise URLPolicyError("invalid_url", "Use a valid plain HTTPS URL on port 443") from None
    if not valid:
        raise URLPolicyError("invalid_url", "Only plain HTTPS URLs on port 443 are supported; do not paste Markdown links")
    if parts.username is not None or parts.password is not None:
        raise URLPolicyError("url_credentials_forbidden", "Credentials in URLs are not accepted")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise URLPolicyError("host_not_allowed", "Host is not in ALLOWED_HOSTS; add the exact hostname in .env and recreate the API container")
    return url


def validate_url(url: str, allowed_hosts: set[str] | None = None) -> str:
    validate_url_structure(url, allowed_hosts)
    host = (urlsplit(url).hostname or '').lower().rstrip('.').encode('idna').decode('ascii')
    public_addresses(host)
    return url
