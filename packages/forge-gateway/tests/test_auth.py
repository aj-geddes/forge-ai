"""Tests for the forge_gateway.auth module.

The admin API-key authentication that used to live here is retired by
ADR-0001 (admin routes now use ``forge_gateway.security.require_permission``,
covered in ``test_authorization.py``). What remains is the SSRF guard on
outbound peer-endpoint calls.
"""

from __future__ import annotations

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
