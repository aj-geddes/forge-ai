"""SSRF protection for outbound peer-endpoint calls.

The admin API-key authentication that used to live here
(``require_admin_key``) is retired by ADR-0001: admin routes now go
through the same bypass-free ``forge_gateway.security`` resolver as every
other route (``require_permission("config:read"|"config:write")``). The
one thing in this module that is unrelated to authentication --
``validate_peer_endpoint``'s SSRF guard on ``POST /v1/admin/peers/{name}/
ping`` -- is retained unchanged.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def validate_peer_endpoint(endpoint: str) -> bool:
    """Validate that a peer endpoint URL is not targeting private/internal IPs.

    Returns True if the endpoint appears safe, False if it targets a
    private/internal network.
    """
    parsed = urlparse(endpoint)
    hostname = parsed.hostname
    if hostname is None:
        return False

    try:
        addr = ipaddress.ip_address(hostname)
        return not any(addr in network for network in _PRIVATE_NETWORKS)
    except ValueError:
        # It's a hostname, not an IP — allow it (DNS resolution happens later)
        # but block obvious internal hostnames
        lower = hostname.lower()
        blocked_suffixes = (".local", ".internal", ".localhost")
        return lower != "localhost" and not lower.endswith(blocked_suffixes)
