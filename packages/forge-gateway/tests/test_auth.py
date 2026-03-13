"""Dedicated unit tests for the auth module.

Covers:
1. SSRF protection via validate_peer_endpoint (private IPs, loopback, link-local,
   internal hostnames, public IPs/hostnames, malformed URLs)
2. API key validation via _validate_key (valid key, invalid key, timing-safe
   comparison, empty key)
3. Token extraction via _extract_token (Bearer header, X-API-Key header,
   precedence, missing credentials)
"""

from __future__ import annotations

import hmac
from unittest.mock import patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from forge_config.schema import APIKeyConfig, SecretRef, SecretSource
from forge_gateway.auth import (
    _extract_token,
    _validate_key,
    validate_peer_endpoint,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_auth_state() -> None:
    """Reset module-level auth state before each test."""
    from forge_gateway import auth as auth_module

    auth_module._api_key_config = None
    auth_module._resolved_keys = []


@pytest.fixture()
def _wire_keys() -> None:
    """Wire resolved keys directly, bypassing secret resolution."""
    from forge_gateway import auth as auth_module

    auth_module._resolved_keys = ["valid-key-1", "valid-key-2"]
    auth_module._api_key_config = APIKeyConfig(
        enabled=True,
        keys=[
            SecretRef(source=SecretSource.ENV, name="k1"),
            SecretRef(source=SecretSource.ENV, name="k2"),
        ],
    )


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
# TestValidateKey — API key validation
# ---------------------------------------------------------------------------


class TestValidateKey:
    """Tests for _validate_key constant-time key comparison."""

    @pytest.mark.usefixtures("_wire_keys")
    def test_valid_key_returns_true(self) -> None:
        assert _validate_key("valid-key-1") is True
        assert _validate_key("valid-key-2") is True

    @pytest.mark.usefixtures("_wire_keys")
    def test_invalid_key_returns_false(self) -> None:
        assert _validate_key("wrong-key") is False

    @pytest.mark.usefixtures("_wire_keys")
    def test_timing_safe_comparison(self) -> None:
        """Verify that hmac.compare_digest is used for constant-time comparison."""
        with patch.object(hmac, "compare_digest", wraps=hmac.compare_digest) as mock_compare:
            _validate_key("valid-key-1")
            assert mock_compare.called
            # The first call should compare the token against the first key
            args = mock_compare.call_args_list[0].args
            assert args == (b"valid-key-1", b"valid-key-1")

    @pytest.mark.usefixtures("_wire_keys")
    def test_empty_key_returns_false(self) -> None:
        assert _validate_key("") is False


# ---------------------------------------------------------------------------
# TestExtractToken — credential extraction
# ---------------------------------------------------------------------------


class TestExtractToken:
    """Tests for _extract_token credential extraction."""

    def test_extracts_bearer_token(self) -> None:
        bearer = HTTPAuthorizationCredentials(scheme="Bearer", credentials="my-bearer-token")
        result = _extract_token(bearer, None)
        assert result == "my-bearer-token"

    def test_extracts_api_key_header(self) -> None:
        result = _extract_token(None, "my-api-key")
        assert result == "my-api-key"

    def test_bearer_takes_precedence(self) -> None:
        bearer = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bearer-value")
        result = _extract_token(bearer, "api-key-value")
        assert result == "bearer-value"

    def test_no_credentials_returns_none(self) -> None:
        result = _extract_token(None, None)
        assert result is None
