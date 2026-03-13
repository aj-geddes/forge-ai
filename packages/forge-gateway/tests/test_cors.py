"""Tests for CORS middleware on the FastAPI gateway."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from forge_gateway.app import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_config(allowed_origins: list[str] | None = None) -> MagicMock:
    """Build a minimal mock ForgeConfig with the given allowed_origins."""
    config = MagicMock()
    config.metadata.name = "test-forge"
    config.security.api_keys = None
    config.security.agentweave.enabled = False
    if allowed_origins is not None:
        config.security.allowed_origins = allowed_origins
    else:
        config.security.allowed_origins = ["*"]
    return config


def _create_test_client(allowed_origins: list[str] | None = None) -> TestClient:
    """Create a TestClient whose CORS middleware uses *allowed_origins*.

    Patches ``_resolve_cors_origins`` so the middleware is configured with
    exactly the origins we want, regardless of config file availability.
    """
    origins = allowed_origins if allowed_origins is not None else ["*"]
    with patch("forge_gateway.app._resolve_cors_origins", return_value=origins):
        app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. CORS preflight (OPTIONS) with matching origin -> 200 + headers
# ---------------------------------------------------------------------------


class TestCORSPreflightMatching:
    def test_preflight_with_wildcard_origin_returns_200(self) -> None:
        """OPTIONS request with any origin returns 200 when allowed_origins=['*'].

        When allow_credentials is True, Starlette reflects the requesting origin
        instead of returning the literal '*' — this is correct per the CORS spec.
        """
        client = _create_test_client(["*"])
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://example.com"

    def test_preflight_with_specific_matching_origin(self) -> None:
        """OPTIONS request with a configured origin returns that origin in the header."""
        client = _create_test_client(["https://app.example.com"])
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://app.example.com"

    def test_preflight_with_multiple_configured_origins(self) -> None:
        """OPTIONS picks the correct origin from a multi-entry list."""
        origins = ["https://first.example.com", "https://second.example.com"]
        client = _create_test_client(origins)
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://second.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://second.example.com"


# ---------------------------------------------------------------------------
# 2. CORS preflight with non-matching origin -> no CORS headers
# ---------------------------------------------------------------------------


class TestCORSPreflightNonMatching:
    def test_preflight_non_matching_origin_omits_cors_headers(self) -> None:
        """OPTIONS from an unlisted origin should not include Allow-Origin."""
        client = _create_test_client(["https://trusted.example.com"])
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in response.headers

    def test_preflight_non_matching_among_multiple_origins(self) -> None:
        """An origin not in the configured list must not receive CORS headers."""
        origins = ["https://a.example.com", "https://b.example.com"]
        client = _create_test_client(origins)
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://c.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# 3. Default config (no allowed_origins) -> wildcard '*' (dev mode)
# ---------------------------------------------------------------------------


class TestCORSDefaultConfig:
    def test_default_config_uses_wildcard(self) -> None:
        """When config cannot be loaded, CORS defaults to wildcard '*'.

        With allow_credentials=True, Starlette reflects the requesting origin
        rather than returning literal '*'.  We verify any origin is accepted.
        """
        with patch(
            "forge_config.load_config",
            side_effect=FileNotFoundError("no config"),
        ):
            app = create_app()
        client = TestClient(app)
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://anything.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://anything.example.com"

    def test_default_config_allows_arbitrary_origin(self) -> None:
        """Wildcard default means any origin is accepted for regular requests.

        For non-preflight requests, Starlette returns the literal '*' when
        allow_origins=['*'].
        """
        with patch(
            "forge_config.load_config",
            side_effect=FileNotFoundError("no config"),
        ):
            app = create_app()
        client = TestClient(app)
        response = client.get(
            "/health/live",
            headers={"Origin": "https://random.example.com"},
        )
        assert response.headers["access-control-allow-origin"] in (
            "*",
            "https://random.example.com",
        )


# ---------------------------------------------------------------------------
# 4. Specific origins configured -> only those get CORS headers
# ---------------------------------------------------------------------------


class TestCORSSpecificOrigins:
    def test_matching_origin_reflected(self) -> None:
        """A request from a listed origin gets Allow-Origin back."""
        client = _create_test_client(["https://dashboard.example.com"])
        response = client.get(
            "/health/live",
            headers={"Origin": "https://dashboard.example.com"},
        )
        assert response.headers["access-control-allow-origin"] == "https://dashboard.example.com"

    def test_non_matching_origin_rejected(self) -> None:
        """A request from an unlisted origin does not get Allow-Origin."""
        client = _create_test_client(["https://dashboard.example.com"])
        response = client.get(
            "/health/live",
            headers={"Origin": "https://attacker.example.com"},
        )
        assert "access-control-allow-origin" not in response.headers

    def test_request_without_origin_has_no_cors_header(self) -> None:
        """A request without an Origin header should not include CORS headers."""
        client = _create_test_client(["https://dashboard.example.com"])
        response = client.get("/health/live")
        assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# 5. Regular GET / POST requests include CORS headers in response
# ---------------------------------------------------------------------------


class TestCORSRegularRequests:
    def test_get_request_includes_cors_headers(self) -> None:
        """A normal GET with a valid Origin includes the Allow-Origin header."""
        client = _create_test_client(["https://ui.example.com"])
        response = client.get(
            "/health/live",
            headers={"Origin": "https://ui.example.com"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://ui.example.com"

    def test_post_request_includes_cors_headers(self) -> None:
        """A POST with a valid Origin includes CORS headers in the response."""
        client = _create_test_client(["https://ui.example.com"])
        # POST to health/live will 405, but CORS headers are still applied
        response = client.post(
            "/health/live",
            headers={"Origin": "https://ui.example.com"},
        )
        # Even error responses should carry CORS headers
        assert response.headers["access-control-allow-origin"] == "https://ui.example.com"

    def test_wildcard_get_request(self) -> None:
        """GET with wildcard config allows any origin.

        For non-preflight requests with allow_origins=['*'], Starlette returns
        the literal '*'.
        """
        client = _create_test_client(["*"])
        response = client.get(
            "/health/live",
            headers={"Origin": "https://anywhere.example.com"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] in (
            "*",
            "https://anywhere.example.com",
        )


# ---------------------------------------------------------------------------
# 6. Credentials header present when configured
# ---------------------------------------------------------------------------


class TestCORSCredentials:
    def test_credentials_header_on_preflight(self) -> None:
        """Preflight response includes Access-Control-Allow-Credentials."""
        client = _create_test_client(["https://app.example.com"])
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_credentials_header_on_regular_request(self) -> None:
        """Regular responses also carry Access-Control-Allow-Credentials."""
        client = _create_test_client(["https://app.example.com"])
        response = client.get(
            "/health/live",
            headers={"Origin": "https://app.example.com"},
        )
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_credentials_header_with_wildcard(self) -> None:
        """Wildcard origin still has allow_credentials=True in middleware config.

        Note: per CORS spec, when allow_credentials is True and origins is ['*'],
        Starlette will reflect the requesting origin rather than sending '*'.
        """
        client = _create_test_client(["*"])
        response = client.get(
            "/health/live",
            headers={"Origin": "https://any.example.com"},
        )
        # With allow_credentials=True, the middleware must respond
        assert "access-control-allow-origin" in response.headers


# ---------------------------------------------------------------------------
# 7. All HTTP methods allowed
# ---------------------------------------------------------------------------


class TestCORSAllMethods:
    @pytest.mark.parametrize(
        "method",
        ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    def test_preflight_allows_method(self, method: str) -> None:
        """Preflight response allows every standard HTTP method."""
        client = _create_test_client(["https://app.example.com"])
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": method,
            },
        )
        assert response.status_code == 200
        allowed = response.headers["access-control-allow-methods"]
        # Starlette CORS with allow_methods=["*"] returns "*"
        assert method in allowed or allowed == "*"

    def test_allow_methods_header_present(self) -> None:
        """The Access-Control-Allow-Methods header is present on preflight."""
        client = _create_test_client(["https://app.example.com"])
        response = client.options(
            "/health/live",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "PATCH",
            },
        )
        assert "access-control-allow-methods" in response.headers
