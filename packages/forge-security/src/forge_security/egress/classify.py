"""Pure, synchronous SSRF host/IP classification.

This is the *classifier* plane of the egress guard (ADR-0006). It is the
first-line, network-free-where-possible check hoisted out of
``forge_gateway.auth`` (which was at the top of the dependency chain and so
unreachable from the tool builders) down into ``forge_security`` so that both
the tool builders (forge-agent) and the gateway admin routes (forge-gateway)
share one blocklist with no drift.

It is deliberately NOT authoritative: a name that resolves to a public IP here
can still rebind to an internal IP by connect time. The authoritative check is
``forge_security.egress.transport.GuardedBackend``, which resolves-and-pins at
the socket. This module is the cheap UX/first-line gate.

The blocklist is extended beyond the AWS ``169.254.169.254`` IMDS to the full
multi-cloud metadata set: Alibaba ``100.100.100.200``, Oracle ``192.0.0.192``,
and the AWS IPv6 IMDS ``fd00:ec2::254`` (GCP's ``169.254.169.254`` is already
covered by the ``169.254.0.0/16`` link-local range).
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from forge_config.schema import EgressPolicy

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Networks treated as internal / non-routable-for-egress. Uses an explicit
#: list rather than ``ipaddress.is_private`` (which on 3.12 also flags the
#: public documentation ranges, e.g. 203.0.113.0/24). The final three entries
#: are cloud metadata singletons beyond the AWS/GCP link-local IMDS.
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (AWS+GCP 169.254.169.254)
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("192.0.0.192/32"),  # Oracle Cloud metadata
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IPv6 IMDS
]

#: Cluster-internal / cloud-metadata names rejected by name, before any
#: resolution (fast, network-free, correct even where they do not resolve).
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "kubernetes",
        "kubernetes.default",
        "kubernetes.default.svc",
        "metadata",
        "metadata.google.internal",
    }
)
BLOCKED_HOSTNAME_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".svc",
    ".cluster.local",
)


def is_internal_ip(ip: _IpAddress) -> bool:
    """True if *ip* is private/loopback/link-local/unique-local/metadata.

    Checks the explicit ``PRIVATE_NETWORKS`` list plus the loopback/
    link-local/unspecified flags as defense-in-depth.
    """
    if any(ip in network for network in PRIVATE_NETWORKS):
        return True
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def candidate_ips(host: str) -> list[_IpAddress]:
    """Every IP address *host* could denote, canonicalized.

    Closes the non-canonical-encoding bypass: ``ipaddress.ip_address`` only
    parses canonical dotted/colon IPs, so integer (``2852039166``), hex
    (``0x7f000001``), octal (``0177.0.0.1``), and short-dotted (``127.1``)
    forms raise ``ValueError``. ``socket.inet_aton`` canonicalizes those;
    ``getaddrinfo`` resolves a real hostname; and any IPv4-mapped IPv6 address
    (``::ffff:169.254.169.254``) also contributes its embedded IPv4. Empty
    only when *host* is a name that does not resolve here.
    """
    ips: list[_IpAddress] = []

    # a. Canonical textual IP (v4/v6, including IPv4-mapped IPv6).
    with contextlib.suppress(ValueError):
        ips.append(ipaddress.ip_address(host))

    # b. Non-canonical numeric IPv4 (decimal/hex/octal/short-dotted).
    with contextlib.suppress(OSError):
        ips.append(ipaddress.ip_address(socket.inet_aton(host)))

    # c. DNS resolution -- only when host is not already a numeric IP form.
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


def is_blocked_hostname(host: str) -> bool:
    """True if *host* is an obvious internal name (empty, a blocked literal,
    or a blocked suffix). Network-free."""
    h = host.strip().rstrip(".").lower()
    return not h or h in BLOCKED_HOSTNAMES or h.endswith(BLOCKED_HOSTNAME_SUFFIXES)


def validate_endpoint(url: str, policy: EgressPolicy | None = None) -> bool:
    """Cheap first-line SSRF check on *url* (NOT authoritative).

    Returns ``True`` if the endpoint appears safe, ``False`` if it targets a
    private/internal network in ANY IP encoding, an internal name, or (when a
    *policy* is supplied) violates its https/allow-list constraints.

    When ``policy is None`` this is behaviorally identical to the legacy
    ``validate_peer_endpoint``: the policy branch may only ever turn a
    ``True`` into ``False``, never the reverse.
    """
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        return False

    host = hostname.strip().rstrip(".").lower()
    if not host:
        return False

    if is_blocked_hostname(host):
        return False

    candidates = candidate_ips(host)
    # A resolved name with no internal candidate is safe; a name that did not
    # resolve here is allowed -- the transport re-validates it at connect time.
    base = not any(is_internal_ip(ip) for ip in candidates) if candidates else True

    if base and policy is not None and policy.enabled:
        if policy.require_https and parsed.scheme != "https":
            return False
        if policy.allowed_hosts and not any(
            host == p.lower() or (p.startswith("*.") and host.endswith(p[1:].lower()))
            for p in policy.allowed_hosts
        ):
            return False

    return base
