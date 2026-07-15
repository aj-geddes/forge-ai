"""Tests for the forge_gateway.auth module.

The admin API-key authentication that used to live here is retired by
ADR-0001 (admin routes now use ``forge_gateway.security.require_permission``,
covered in ``test_authorization.py``). What remains is the SSRF guard on
outbound peer-endpoint calls.
"""

from __future__ import annotations

import socket

import pytest
from forge_gateway.auth import validate_peer_endpoint

# ---------------------------------------------------------------------------
# TestValidatePeerEndpoint — SSRF protection
# ---------------------------------------------------------------------------


class TestValidatePeerEndpoint:
    """Tests for validate_peer_endpoint SSRF protection."""

    def test_allows_public_ip(self) -> None:
        assert validate_peer_endpoint("http://203.0.113.1:8080") is True  # noqa: S104

    def test_blocks_private_10_network(self) -> None:
        assert validate_peer_endpoint("http://10.0.0.1:8080") is False  # noqa: S104

    def test_blocks_private_172_network(self) -> None:
        assert validate_peer_endpoint("http://172.16.0.1:8080") is False  # noqa: S104

    def test_blocks_private_192_network(self) -> None:
        assert validate_peer_endpoint("http://192.168.1.1:8080") is False  # noqa: S104

    def test_blocks_loopback_127(self) -> None:
        assert validate_peer_endpoint("http://127.0.0.1:8080") is False  # noqa: S104

    def test_blocks_ipv6_loopback(self) -> None:
        assert validate_peer_endpoint("http://[::1]:8080") is False

    def test_blocks_link_local(self) -> None:
        assert validate_peer_endpoint("http://169.254.1.1:8080") is False  # noqa: S104

    def test_blocks_localhost_hostname(self) -> None:
        assert validate_peer_endpoint("http://localhost:8080") is False

    def test_blocks_dot_local_suffix(self) -> None:
        assert validate_peer_endpoint("http://myservice.local:8080") is False

    def test_blocks_dot_internal_suffix(self) -> None:
        assert validate_peer_endpoint("http://myservice.internal:8080") is False

    def test_allows_public_hostname(self) -> None:
        assert validate_peer_endpoint("http://api.example.com:8080") is True

    def test_blocks_no_hostname(self) -> None:
        assert validate_peer_endpoint("not-a-url") is False

    def test_blocks_ipv6_unique_local(self) -> None:
        assert validate_peer_endpoint("http://[fd12::1]:8080") is False


# ---------------------------------------------------------------------------
# SECURITY ROUND 2 [HIGH] — SSRF via non-canonical IP encodings
# ---------------------------------------------------------------------------


class TestValidatePeerEndpointEncodedVariants:
    """**SECURITY** [HIGH]: the round-1 guard called
    ``ipaddress.ip_address(host)`` which ONLY parses canonical dotted/colon
    IPs. Every other numeric encoding raised ``ValueError`` and fell through
    to a small literal-hostname denylist -- so the integer, hex, octal,
    short-dotted, and IPv4-mapped-IPv6 encodings of an internal address all
    PASSED. The guard must canonicalize the host before classifying it, and
    reject if ANY resolved/normalized address is internal.
    """

    @pytest.mark.parametrize(
        ("endpoint", "canonical"),
        [
            ("http://2852039166/", "169.254.169.254 (IMDS) as a 32-bit decimal int"),
            ("http://2130706433/", "127.0.0.1 as a decimal int"),
            ("http://167772161/", "10.0.0.1 as a decimal int"),
            ("http://3232235777/", "192.168.1.1 as a decimal int"),
            ("http://0x7f000001/", "127.0.0.1 as hex"),
            ("http://0177.0.0.1/", "127.0.0.1 with an octal first octet"),
            ("http://127.1/", "127.0.0.1 short-dotted"),
            ("http://[::ffff:169.254.169.254]/", "IPv4-mapped IPv6 -> IMDS"),
        ],
    )
    def test_non_canonical_internal_encoding_is_rejected(
        self, endpoint: str, canonical: str
    ) -> None:
        assert validate_peer_endpoint(endpoint) is False, f"{endpoint} == {canonical}"

    def test_hostname_that_resolves_to_a_private_ip_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolve-then-classify: a public-looking hostname whose DNS A
        record points at a private/internal IP (a classic DNS-rebinding /
        internal-alias SSRF) must be rejected on the resolved address."""

        def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.11.12.13", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert validate_peer_endpoint("http://rebind.attacker.example/") is False

    def test_hostname_that_resolves_to_a_public_ip_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert validate_peer_endpoint("http://real-public.example/") is True
