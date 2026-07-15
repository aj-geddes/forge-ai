"""SSRF protection for outbound peer-endpoint calls.

The admin API-key authentication that used to live here
(``require_admin_key``) is retired by ADR-0001: admin routes now go
through the same bypass-free ``forge_gateway.security`` resolver as every
other route (``require_permission("config:read"|"config:write")``). The
one thing in this module that is unrelated to authentication --
``validate_peer_endpoint``'s SSRF guard on ``POST /v1/admin/peers/{name}/
ping`` (and every overlay write path) -- is retained, hardened against
non-canonical IP encodings (SECURITY round 2).
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from urllib.parse import urlparse

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (incl. 169.254.169.254 IMDS)
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

#: Cluster-internal / cloud-metadata names to reject by name, before any
#: resolution (fast, network-free, and correct even where these do not
#: resolve in a given environment).
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "kubernetes",
        "kubernetes.default",
        "kubernetes.default.svc",
        "metadata",
        "metadata.google.internal",
    }
)
_BLOCKED_HOSTNAME_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".svc",
    ".cluster.local",
)


def _is_internal_ip(ip: _IpAddress) -> bool:
    """True if *ip* is on a private, loopback, link-local (incl. the
    169.254.169.254 IMDS), unique-local, or unspecified range. Deliberately
    checks the explicit ``_PRIVATE_NETWORKS`` list (rather than
    ``ipaddress.is_private``, which on Python 3.12 also flags the public
    documentation ranges, e.g. 203.0.113.0/24) plus the loopback/link-local/
    unspecified flags as defense-in-depth."""
    if any(ip in network for network in _PRIVATE_NETWORKS):
        return True
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def _candidate_ips(host: str) -> list[_IpAddress]:
    """Every IP address *host* could denote, canonicalized.

    Closes the non-canonical-encoding bypass: a bare
    ``ipaddress.ip_address()`` only parses canonical dotted/colon IPs, so
    the integer (``2852039166``), hex (``0x7f000001``), octal
    (``0177.0.0.1``), and short-dotted (``127.1``) forms used to raise
    ``ValueError`` and fall through the guard entirely. ``socket.inet_aton``
    canonicalizes all of those; ``getaddrinfo`` resolves a real hostname
    (catching a name that resolves to an internal IP); and any IPv4-mapped
    IPv6 address (``::ffff:169.254.169.254``) also contributes its embedded
    IPv4. Empty only when *host* is a name that does not resolve.
    """
    ips: list[_IpAddress] = []

    # a. Canonical textual IP (v4/v6, including IPv4-mapped IPv6).
    with contextlib.suppress(ValueError):
        ips.append(ipaddress.ip_address(host))

    # b. Non-canonical numeric IPv4 (decimal/hex/octal/short-dotted).
    #    inet_aton canonicalizes them and raises OSError for a real hostname.
    with contextlib.suppress(OSError):
        ips.append(ipaddress.ip_address(socket.inet_aton(host)))

    # c. DNS resolution -- only when the host is not already a numeric IP
    #    form (avoids a pointless lookup for the numeric encodings above).
    if not ips:
        with contextlib.suppress(OSError, UnicodeError):
            for info in socket.getaddrinfo(host, None):
                sockaddr = info[4]
                with contextlib.suppress(ValueError):
                    ips.append(ipaddress.ip_address(str(sockaddr[0]).split("%")[0]))

    # d. Expand any IPv4-mapped IPv6 to also classify the embedded IPv4.
    for ip in list(ips):
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ips.append(mapped)

    return ips


def validate_peer_endpoint(endpoint: str) -> bool:
    """Validate that a peer endpoint URL is not targeting private/internal IPs.

    Returns ``True`` if the endpoint appears safe, ``False`` if it targets a
    private/internal network -- in ANY IP encoding (canonical, integer, hex,
    octal, short-dotted, or IPv4-mapped IPv6), or via a hostname that
    resolves to such an address.
    """
    parsed = urlparse(endpoint)
    hostname = parsed.hostname
    if hostname is None:
        return False

    host = hostname.strip().rstrip(".").lower()
    if not host:
        return False

    # 1. Obvious internal names -- reject by name, no resolution needed.
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOSTNAME_SUFFIXES):
        return False

    # 2. Canonicalize to every IP the host could denote; reject if ANY is
    #    internal. This is the SSRF choke point that the round-1 guard's
    #    ip_address()-only parse let non-canonical encodings slip past.
    candidates = _candidate_ips(host)
    if candidates:
        return not any(_is_internal_ip(ip) for ip in candidates)

    # 3. A name that is not obviously internal and did not resolve here:
    #    allow. There is nothing to reach until it resolves; the call site
    #    re-validates the resolved address at connect time (resolve-then-pin).
    return True
