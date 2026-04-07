"""URL validation for outbound requests (webhooks, etc.)."""

import ipaddress
import socket
from urllib.parse import urlparse


def is_safe_webhook_url(url: str) -> tuple[bool, str]:
    """Validate a webhook URL is safe for server-side requests.

    Blocks:
    - Non-HTTP(S) protocols
    - Localhost and loopback addresses
    - Private/internal IP ranges (10.x, 172.16-31.x, 192.168.x, etc.)
    - Link-local addresses
    - AWS metadata endpoint (169.254.169.254)

    Returns (is_safe, error_message).
    """
    if not url:
        return False, "URL is required."

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False, "Only http:// and https:// URLs are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL must include a hostname."

    # Block obvious localhost variants
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False, "Localhost URLs are not allowed."

    # Resolve hostname and check the IP
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"

    for _family, _type, _proto, _canonname, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if ip.is_loopback:
            return False, "Loopback addresses are not allowed."
        if ip.is_private:
            return False, "Private/internal IP addresses are not allowed."
        if ip.is_link_local:
            return False, "Link-local addresses are not allowed."
        if ip.is_reserved:
            return False, "Reserved IP addresses are not allowed."
        # AWS metadata endpoint
        if ip_str == "169.254.169.254":
            return False, "Cloud metadata endpoints are not allowed."

    return True, ""
