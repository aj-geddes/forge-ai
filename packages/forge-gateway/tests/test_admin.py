"""Tests for admin API routes, including authentication and SSRF protection.

Covers:
1. No auth header -> 401 for each admin endpoint
2. Invalid API key -> 401 for each endpoint
3. Valid API key -> 200/success for each endpoint
4. SSRF protection on peer ping
5. Edge cases: empty auth header, malformed bearer token, wrong auth scheme
6. Auth disabled / not configured scenarios
7. Key validation helpers
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_config.schema import (
    AgentsConfig,
    APIKeyConfig,
    ForgeConfig,
    PeerAgent,
    SecretRef,
    SecretSource,
)
from forge_gateway import auth as auth_module
from forge_gateway.routes import admin

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-secret-key"
WRONG_API_KEY = "wrong-key-xyz789"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_config() -> ForgeConfig:
    """A minimal ForgeConfig for testing."""
    return ForgeConfig()


@pytest.fixture()
def _wire_admin(default_config: ForgeConfig) -> Iterator[None]:
    """Wire admin state with a default config."""
    admin.set_state(config=default_config, config_path="/tmp/test-forge.yaml", agent=None)  # noqa: S108
    yield
    admin.set_state(config=None, config_path="", agent=None)


@pytest.fixture()
def _wire_auth() -> Iterator[None]:
    """Enable API key auth with a known test key."""
    config = APIKeyConfig(
        enabled=True,
        keys=[SecretRef(source=SecretSource.ENV, name="TEST_ADMIN_KEY")],
    )
    auth_module._api_key_config = config
    auth_module._resolved_keys = [VALID_API_KEY]
    yield
    auth_module._api_key_config = None
    auth_module._resolved_keys = []


@pytest.fixture()
def _wire_auth_disabled() -> Iterator[None]:
    """Configure auth module with API key auth disabled."""
    auth_module._api_key_config = APIKeyConfig(enabled=False, keys=[])
    auth_module._resolved_keys = []
    yield
    auth_module._api_key_config = None
    auth_module._resolved_keys = []


@pytest.fixture()
def _wire_auth_no_keys() -> Iterator[None]:
    """Configure auth module with auth enabled but no keys resolved."""
    auth_module._api_key_config = APIKeyConfig(enabled=True, keys=[])
    auth_module._resolved_keys = []
    yield
    auth_module._api_key_config = None
    auth_module._resolved_keys = []


@pytest.fixture()
def config_with_peers() -> ForgeConfig:
    """Config with peers for SSRF tests."""
    return ForgeConfig(
        agents=AgentsConfig(
            peers=[
                PeerAgent(
                    name="peer1",
                    endpoint="http://peer1.example.com:8000",
                    capabilities=["search"],
                ),
                PeerAgent(
                    name="peer-internal",
                    endpoint="http://192.168.1.100:8000",
                    capabilities=["internal"],
                ),
                PeerAgent(
                    name="peer-localhost",
                    endpoint="http://localhost:9000",
                    capabilities=["local"],
                ),
            ]
        )
    )


@pytest.fixture()
def _wire_admin_with_peers(config_with_peers: ForgeConfig) -> Iterator[None]:
    """Wire admin state with a config that has peers."""
    admin.set_state(
        config=config_with_peers,
        config_path="/tmp/test-forge.yaml",  # noqa: S108
        agent=None,
    )
    yield
    admin.set_state(config=None, config_path="", agent=None)


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app with admin routes."""
    app = FastAPI()
    app.include_router(admin.router)
    return app


@pytest.fixture()
def app() -> FastAPI:
    """FastAPI app instance with admin routes."""
    return _make_app()


@pytest.fixture()
def client() -> TestClient:
    """Sync TestClient for backwards-compatible tests."""
    return TestClient(_make_app())


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Headers with a valid admin API key."""
    return {"Authorization": f"Bearer {VALID_API_KEY}"}


@pytest.fixture()
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx.AsyncClient wired to the app via ASGITransport."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# =========================================================================
# 1. No auth header -> 401 for each admin endpoint
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_admin")
class TestAdminNoAuthHeader:
    """Every admin endpoint must return 401 when no auth header is provided."""

    async def test_admin_config_get_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/config")
        assert resp.status_code == 401

    async def test_admin_config_put_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.put("/v1/admin/config", json={"config": {}})
        assert resp.status_code == 401

    async def test_admin_config_schema_get_requires_auth(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/config/schema")
        assert resp.status_code == 401

    async def test_admin_tools_get_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/tools")
        assert resp.status_code == 401

    async def test_admin_tools_preview_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/v1/admin/tools/preview", json={"source": {}})
        assert resp.status_code == 401

    async def test_admin_sessions_get_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/sessions")
        assert resp.status_code == 401

    async def test_admin_session_delete_requires_auth(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.delete("/v1/admin/sessions/some-id")
        assert resp.status_code == 401

    async def test_admin_peers_get_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/peers")
        assert resp.status_code == 401

    async def test_admin_peer_ping_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/v1/admin/peers/peer1/ping")
        assert resp.status_code == 401

    async def test_no_auth_response_includes_detail(self, async_client: httpx.AsyncClient) -> None:
        """The 401 body should contain a descriptive detail message."""
        resp = await async_client.get("/v1/admin/config")
        body = resp.json()
        assert "detail" in body
        assert "Missing" in body["detail"] or "authentication" in body["detail"]


# =========================================================================
# 2. Invalid API key -> 401 for each endpoint
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_admin")
class TestAdminInvalidApiKey:
    """Every admin endpoint must return 401 when given an invalid API key."""

    def _bad(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {WRONG_API_KEY}"}

    async def test_admin_config_get_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/config", headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_config_put_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.put("/v1/admin/config", json={"config": {}}, headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_config_schema_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/config/schema", headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_tools_get_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/tools", headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_tools_preview_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/tools/preview", json={"source": {}}, headers=self._bad()
        )
        assert resp.status_code == 401

    async def test_admin_sessions_get_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/sessions", headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_session_delete_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.delete("/v1/admin/sessions/some-id", headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_peers_get_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/peers", headers=self._bad())
        assert resp.status_code == 401

    async def test_admin_peer_ping_rejects_invalid_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.post("/v1/admin/peers/peer1/ping", headers=self._bad())
        assert resp.status_code == 401

    async def test_invalid_key_response_includes_detail(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.get("/v1/admin/config", headers=self._bad())
        assert "Invalid" in resp.json()["detail"]


# =========================================================================
# 3. Valid API key -> success for each endpoint (mock underlying logic)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_admin")
class TestAdminValidApiKey:
    """Every admin endpoint must accept a valid API key and process the request."""

    async def test_admin_config_get_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "path" in data

    async def test_admin_config_put_with_valid_key(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Any,
    ) -> None:
        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: old\n")
        admin.set_state(config=ForgeConfig(), config_path=str(config_file), agent=None)

        new_config = ForgeConfig(
            metadata=ForgeConfig().metadata.model_copy(update={"name": "new-name"})
        )
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": new_config.model_dump(mode="json")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_admin_config_schema_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.get("/v1/admin/config/schema", headers=auth_headers)
        assert resp.status_code == 200
        assert "properties" in resp.json()

    async def test_admin_tools_get_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.get("/v1/admin/tools", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_admin_sessions_get_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.get("/v1/admin/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_admin_session_delete_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # No agent -> 404 (but NOT 401, proving auth passed)
        resp = await async_client.delete("/v1/admin/sessions/some-id", headers=auth_headers)
        assert resp.status_code == 404

    async def test_admin_peers_get_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.get("/v1/admin/peers", headers=auth_headers)
        assert resp.status_code == 200

    async def test_admin_peer_ping_unknown_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Unknown peer -> 404 (but NOT 401, proving auth passed)
        resp = await async_client.post("/v1/admin/peers/nonexistent/ping", headers=auth_headers)
        assert resp.status_code == 404

    async def test_admin_x_api_key_header_accepted(self, async_client: httpx.AsyncClient) -> None:
        """X-API-Key header is accepted as an alternative to Bearer."""
        resp = await async_client.get("/v1/admin/config", headers={"X-API-Key": VALID_API_KEY})
        assert resp.status_code == 200

    async def test_admin_tools_registry_with_valid_key(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        """Tools endpoint returns tool info when agent has a registry."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"
        mock_registry = MagicMock()
        mock_registry.tools = [mock_tool]
        mock_agent = MagicMock()
        mock_agent._registry = mock_registry
        admin.set_state(config=ForgeConfig(), config_path="/tmp/test.yaml", agent=mock_agent)  # noqa: S108

        resp = await async_client.get("/v1/admin/tools", headers=auth_headers)
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"


# =========================================================================
# 4. SSRF protection on peer ping
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_admin_with_peers")
class TestPeerPingSsrfProtection:
    """Peer ping must reject peers whose endpoints target private/internal IPs."""

    async def test_peer_ping_rejects_private_ip_endpoint(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.post("/v1/admin/peers/peer-internal/ping", headers=auth_headers)
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "private" in detail or "internal" in detail

    async def test_peer_ping_rejects_localhost_endpoint(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.post("/v1/admin/peers/peer-localhost/ping", headers=auth_headers)
        assert resp.status_code == 400

    async def test_peer_ping_unknown_peer_returns_404(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await async_client.post("/v1/admin/peers/unknown-peer/ping", headers=auth_headers)
        assert resp.status_code == 404

    async def test_peer_ping_public_endpoint_allowed(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        """Peer with public endpoint passes SSRF check; mock the outbound HTTP."""
        import unittest.mock
        from unittest.mock import AsyncMock

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.__aenter__.return_value = mock_http
        mock_http.__aexit__.return_value = None

        with unittest.mock.patch("httpx.AsyncClient", return_value=mock_http):
            resp = await async_client.post("/v1/admin/peers/peer1/ping", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "peer1"
        assert data["status"] == "reachable"


class TestValidatePeerEndpoint:
    """Unit tests for the validate_peer_endpoint helper."""

    def test_allows_public_hostname(self) -> None:
        assert auth_module.validate_peer_endpoint("https://api.example.com:8000") is True

    def test_allows_public_ip(self) -> None:
        assert auth_module.validate_peer_endpoint("http://203.0.113.10:8000") is True

    def test_blocks_private_10_network(self) -> None:
        assert auth_module.validate_peer_endpoint("http://10.0.0.1:8000") is False

    def test_blocks_private_172_network(self) -> None:
        assert auth_module.validate_peer_endpoint("http://172.16.0.1:8000") is False

    def test_blocks_private_192_network(self) -> None:
        assert auth_module.validate_peer_endpoint("http://192.168.1.1:8000") is False

    def test_blocks_loopback(self) -> None:
        assert auth_module.validate_peer_endpoint("http://127.0.0.1:8000") is False

    def test_blocks_link_local(self) -> None:
        assert auth_module.validate_peer_endpoint("http://169.254.1.1:8000") is False

    def test_blocks_localhost_hostname(self) -> None:
        assert auth_module.validate_peer_endpoint("http://localhost:8000") is False

    def test_blocks_dot_local_hostname(self) -> None:
        assert auth_module.validate_peer_endpoint("http://myhost.local:8000") is False

    def test_blocks_dot_internal_hostname(self) -> None:
        assert auth_module.validate_peer_endpoint("http://service.internal:8000") is False

    def test_blocks_dot_localhost_hostname(self) -> None:
        assert auth_module.validate_peer_endpoint("http://sub.localhost:8000") is False

    def test_blocks_empty_or_invalid_url(self) -> None:
        assert auth_module.validate_peer_endpoint("not-a-url") is False


# =========================================================================
# 5. Edge cases: empty auth header, malformed bearer, wrong scheme
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_admin")
class TestAdminAuthEdgeCases:
    """Edge cases for the admin auth dependency."""

    async def test_empty_authorization_header(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/config", headers={"Authorization": ""})
        assert resp.status_code == 401

    async def test_bearer_with_empty_token(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/config", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    async def test_basic_auth_scheme_rejected(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get(
            "/v1/admin/config",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    async def test_digest_auth_scheme_rejected(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get(
            "/v1/admin/config",
            headers={"Authorization": "Digest username=admin"},
        )
        assert resp.status_code == 401

    async def test_token_only_no_scheme(self, async_client: httpx.AsyncClient) -> None:
        """A raw token without 'Bearer' prefix must be rejected."""
        resp = await async_client.get("/v1/admin/config", headers={"Authorization": VALID_API_KEY})
        assert resp.status_code == 401

    async def test_bearer_lowercase_accepted(self, async_client: httpx.AsyncClient) -> None:
        """HTTP Bearer scheme should be case-insensitive per RFC 7235."""
        resp = await async_client.get(
            "/v1/admin/config",
            headers={"Authorization": f"bearer {VALID_API_KEY}"},
        )
        assert resp.status_code == 200

    async def test_x_api_key_with_invalid_value(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/config", headers={"X-API-Key": WRONG_API_KEY})
        assert resp.status_code == 401

    async def test_x_api_key_with_empty_value(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/config", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    async def test_both_headers_bearer_takes_priority(
        self, async_client: httpx.AsyncClient
    ) -> None:
        """When both Authorization and X-API-Key are present, Bearer wins."""
        resp = await async_client.get(
            "/v1/admin/config",
            headers={
                "Authorization": f"Bearer {VALID_API_KEY}",
                "X-API-Key": WRONG_API_KEY,
            },
        )
        assert resp.status_code == 200

    async def test_invalid_bearer_fails_despite_valid_x_api_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        """Invalid Bearer should fail even if X-API-Key is valid."""
        resp = await async_client.get(
            "/v1/admin/config",
            headers={
                "Authorization": f"Bearer {WRONG_API_KEY}",
                "X-API-Key": VALID_API_KEY,
            },
        )
        assert resp.status_code == 401

    async def test_bearer_with_leading_whitespace(self, async_client: httpx.AsyncClient) -> None:
        """Leading space in Authorization header is rejected."""
        resp = await async_client.get(
            "/v1/admin/config",
            headers={"Authorization": f" Bearer {VALID_API_KEY}"},
        )
        assert resp.status_code == 401


# =========================================================================
# 6. Auth disabled / not configured scenarios
# =========================================================================


@pytest.mark.usefixtures("_wire_admin")
class TestAdminAuthNotConfigured:
    """When API key config is None or disabled, admin endpoints return 403."""

    async def test_returns_403_when_auth_not_configured(
        self, async_client: httpx.AsyncClient
    ) -> None:
        auth_module._api_key_config = None
        auth_module._resolved_keys = []
        resp = await async_client.get("/v1/admin/config")
        assert resp.status_code == 403

    async def test_returns_403_when_auth_disabled(self, async_client: httpx.AsyncClient) -> None:
        auth_module._api_key_config = APIKeyConfig(enabled=False, keys=[])
        auth_module._resolved_keys = []
        resp = await async_client.get("/v1/admin/config")
        assert resp.status_code == 403

    async def test_returns_403_even_with_valid_looking_key(
        self, async_client: httpx.AsyncClient
    ) -> None:
        """Even with a valid-looking key, disabled auth means 403."""
        auth_module._api_key_config = APIKeyConfig(enabled=False, keys=[])
        auth_module._resolved_keys = []
        resp = await async_client.get(
            "/v1/admin/config",
            headers={"Authorization": "Bearer some-key"},
        )
        assert resp.status_code == 403


@pytest.mark.usefixtures("_wire_auth_no_keys", "_wire_admin")
class TestAdminAuthEnabledNoKeys:
    """When auth is enabled but no keys are resolved, admin returns 403."""

    async def test_returns_403_when_no_keys_resolved(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get(
            "/v1/admin/config",
            headers={"Authorization": f"Bearer {VALID_API_KEY}"},
        )
        assert resp.status_code == 403


# =========================================================================
# 7. Key validation unit tests
# =========================================================================


class TestKeyValidation:
    """Unit tests for the internal key validation helpers."""

    def test_validate_key_matches_correct_key(self) -> None:
        auth_module._resolved_keys = ["correct-key"]
        assert auth_module._validate_key("correct-key") is True
        auth_module._resolved_keys = []

    def test_validate_key_rejects_wrong_key(self) -> None:
        auth_module._resolved_keys = ["correct-key"]
        assert auth_module._validate_key("wrong-key") is False
        auth_module._resolved_keys = []

    def test_validate_key_empty_key_list(self) -> None:
        auth_module._resolved_keys = []
        assert auth_module._validate_key("any-key") is False

    def test_validate_key_multiple_keys(self) -> None:
        auth_module._resolved_keys = ["key-one", "key-two", "key-three"]
        assert auth_module._validate_key("key-two") is True
        assert auth_module._validate_key("key-four") is False
        auth_module._resolved_keys = []

    def test_extract_token_prefers_bearer(self) -> None:
        bearer = MagicMock()
        bearer.credentials = "bearer-token"
        assert auth_module._extract_token(bearer, "header-token") == "bearer-token"

    def test_extract_token_falls_back_to_api_key(self) -> None:
        assert auth_module._extract_token(None, "header-token") == "header-token"

    def test_extract_token_returns_none_when_empty(self) -> None:
        assert auth_module._extract_token(None, None) is None


# =========================================================================
# 8. Functional tests (existing behavior with auth — sync TestClient)
# =========================================================================


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestGetConfig:
    def test_returns_config(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        resp = client.get("/v1/admin/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "path" in data
        assert data["path"] == "/tmp/test-forge.yaml"  # noqa: S108

    def test_config_has_expected_sections(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/v1/admin/config", headers=auth_headers)
        config = resp.json()["config"]
        assert "metadata" in config
        assert "llm" in config
        assert "tools" in config
        assert "security" in config
        assert "agents" in config

    def test_returns_500_without_config(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        admin.set_state(config=None, config_path="", agent=None)
        resp = client.get("/v1/admin/config", headers=auth_headers)
        assert resp.status_code == 500


@pytest.mark.usefixtures("_wire_auth")
class TestGetConfigSchema:
    def test_returns_json_schema(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        resp = client.get("/v1/admin/config/schema", headers=auth_headers)
        assert resp.status_code == 200
        schema = resp.json()
        assert "properties" in schema
        assert "title" in schema


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestUpdateConfig:
    def test_validates_and_updates(
        self, client: TestClient, auth_headers: dict[str, str], tmp_path: Any
    ) -> None:
        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: old\n")
        admin.set_state(config=ForgeConfig(), config_path=str(config_file), agent=None)

        new_config = ForgeConfig(
            metadata=ForgeConfig().metadata.model_copy(update={"name": "new-name"})
        )
        resp = client.put(
            "/v1/admin/config",
            json={"config": new_config.model_dump(mode="json")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_rejects_invalid_config(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        resp = client.put(
            "/v1/admin/config",
            json={"config": {"llm": {"litellm": {"mode": "invalid_mode"}}}},
            headers=auth_headers,
        )
        assert resp.status_code == 400


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestListTools:
    def test_returns_empty_without_agent(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/v1/admin/tools", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_tools_from_registry(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        mock_registry = MagicMock()
        mock_registry.tools = [mock_tool]

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = client.get("/v1/admin/tools", headers=auth_headers)
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestListSessions:
    def test_returns_empty_without_agent(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/v1/admin/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestDeleteSession:
    def test_returns_404_without_agent(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/v1/admin/sessions/some-id", headers=auth_headers)
        assert resp.status_code == 404


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestListPeers:
    def test_returns_empty_without_peers(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/v1/admin/peers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_configured_peers(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        config = ForgeConfig(
            agents=AgentsConfig(
                peers=[
                    PeerAgent(
                        name="peer1",
                        endpoint="http://peer1:8000",
                        capabilities=["search"],
                    )
                ]
            )
        )
        admin.set_state(config=config, config_path="/tmp/test.yaml", agent=None)  # noqa: S108

        resp = client.get("/v1/admin/peers", headers=auth_headers)
        assert resp.status_code == 200
        peers = resp.json()
        assert len(peers) == 1
        assert peers[0]["name"] == "peer1"
        assert peers[0]["endpoint"] == "http://peer1:8000"


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestPingPeer:
    def test_returns_404_for_unknown_peer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post("/v1/admin/peers/unknown/ping", headers=auth_headers)
        assert resp.status_code == 404

    def test_rejects_peer_with_private_ip_endpoint(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        config = ForgeConfig(
            agents=AgentsConfig(
                peers=[PeerAgent(name="internal", endpoint="http://192.168.1.1:8000")]
            )
        )
        admin.set_state(config=config, config_path="/tmp/test.yaml", agent=None)  # noqa: S108

        resp = client.post("/v1/admin/peers/internal/ping", headers=auth_headers)
        assert resp.status_code == 400
        assert "private" in resp.json()["detail"].lower()

    def test_rejects_peer_with_localhost_endpoint(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        config = ForgeConfig(
            agents=AgentsConfig(peers=[PeerAgent(name="local", endpoint="http://localhost:8000")])
        )
        admin.set_state(config=config, config_path="/tmp/test.yaml", agent=None)  # noqa: S108

        resp = client.post("/v1/admin/peers/local/ping", headers=auth_headers)
        assert resp.status_code == 400


# =========================================================================
# 9. Helper function tests
# =========================================================================


class TestRedactSecrets:
    def test_redacts_env_secret(self) -> None:
        data: dict[str, Any] = {"token": {"source": "env", "name": "MY_SECRET", "key": None}}
        admin._redact_secrets(data)
        assert data["token"]["name"] == "***REDACTED***"

    def test_redacts_k8s_secret(self) -> None:
        data: dict[str, Any] = {
            "token": {"source": "k8s_secret", "name": "my-secret", "key": "api-key"}
        }
        admin._redact_secrets(data)
        assert data["token"]["name"] == "***REDACTED***"
        assert data["token"]["key"] == "***REDACTED***"

    def test_preserves_non_secrets(self) -> None:
        data: dict[str, Any] = {"name": "test", "value": 42}
        admin._redact_secrets(data)
        assert data["name"] == "test"
        assert data["value"] == 42


# =========================================================================
# 10. Coverage for missed lines — update_config edge cases (lines 112-123)
# =========================================================================


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestUpdateConfigEdgeCases:
    """Cover update_config branches: OSError on write, agent reload paths."""

    async def test_update_config_write_failure_returns_500(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Line 112-113: OSError when writing config to disk returns 500."""
        import unittest.mock

        admin.set_state(
            config=ForgeConfig(),
            config_path="/nonexistent/dir/forge.yaml",
            agent=None,
        )

        new_config = ForgeConfig()
        with unittest.mock.patch.object(
            Path, "write_text", side_effect=OSError("Permission denied")
        ):
            resp = await async_client.put(
                "/v1/admin/config",
                json={"config": new_config.model_dump(mode="json")},
                headers=auth_headers,
            )
        assert resp.status_code == 500
        assert "Failed to write config" in resp.json()["detail"]

    async def test_update_config_with_agent_reload_success(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Any,
    ) -> None:
        """Lines 117-121: agent exists with registry, build_and_swap succeeds."""
        from unittest.mock import AsyncMock

        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: old\n")

        mock_registry = MagicMock()
        mock_registry.build_and_swap = AsyncMock(return_value=True)
        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        admin.set_state(
            config=ForgeConfig(),
            config_path=str(config_file),
            agent=mock_agent,
        )

        new_config = ForgeConfig()
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": new_config.model_dump(mode="json")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["reloaded"] is True
        assert "tools reloaded" in data["message"]
        mock_registry.build_and_swap.assert_awaited_once()

    async def test_update_config_with_agent_reload_failure(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Any,
    ) -> None:
        """Lines 122-123: agent reload raises exception, logged but not fatal."""
        from unittest.mock import AsyncMock

        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: old\n")

        mock_registry = MagicMock()
        mock_registry.build_and_swap = AsyncMock(side_effect=RuntimeError("reload failed"))
        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        admin.set_state(
            config=ForgeConfig(),
            config_path=str(config_file),
            agent=mock_agent,
        )

        new_config = ForgeConfig()
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": new_config.model_dump(mode="json")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["reloaded"] is False

    async def test_update_config_agent_without_registry(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Any,
    ) -> None:
        """Lines 119-120: agent exists but has no _registry attribute."""
        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: old\n")

        mock_agent = MagicMock(spec=[])  # No attributes at all
        admin.set_state(
            config=ForgeConfig(),
            config_path=str(config_file),
            agent=mock_agent,
        )

        new_config = ForgeConfig()
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": new_config.model_dump(mode="json")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["reloaded"] is False


# =========================================================================
# 11. Coverage for missed lines — list_tools with no registry (line 155)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestListToolsNoRegistry:
    """Cover list_tools when agent exists but has no _registry."""

    async def test_list_tools_agent_without_registry_returns_empty(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Line 155: agent exists but no _registry -> return []."""
        mock_agent = MagicMock(spec=[])  # No _registry attribute
        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.get("/v1/admin/tools", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)


# =========================================================================
# 12. Coverage for missed lines — preview_tools (lines 176-196)
# =========================================================================


@pytest.mark.usefixtures("_wire_admin", "_wire_auth")
class TestPreviewTools:
    """Cover preview_tools success and error paths."""

    async def test_preview_tools_success(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 176-194: successful tool preview returns tool list."""
        import unittest.mock

        mock_tool_1 = MagicMock()
        mock_tool_1.name = "get_users"
        mock_tool_1.description = "Fetch users"

        mock_tool_2 = MagicMock()
        mock_tool_2.name = "create_user"
        mock_tool_2.description = "Create a user"

        mock_builder = MagicMock()
        mock_builder.build = unittest.mock.AsyncMock(return_value=[mock_tool_1, mock_tool_2])

        with (
            unittest.mock.patch(
                "forge_agent.builder.openapi.OpenAPIToolBuilder", return_value=mock_builder
            ),
            unittest.mock.patch(
                "forge_config.schema.OpenAPISource.model_validate", return_value=MagicMock()
            ),
        ):
            resp = await async_client.post(
                "/v1/admin/tools/preview",
                json={"source": {"url": "https://example.com/openapi.json"}},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["tools"]) == 2
        assert data["tools"][0]["name"] == "get_users"
        assert data["tools"][0]["source"] == "openapi"
        assert data["tools"][1]["name"] == "create_user"

    async def test_preview_tools_import_error(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 195-196: exception during preview returns 400."""
        import unittest.mock

        with unittest.mock.patch.dict("sys.modules", {"forge_agent.builder.openapi": None}):
            resp = await async_client.post(
                "/v1/admin/tools/preview",
                json={"source": {"url": "https://example.com/openapi.json"}},
                headers=auth_headers,
            )
        assert resp.status_code == 400

    async def test_preview_tools_validation_error(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 195-196: invalid source data raises validation error -> 400."""
        import unittest.mock

        with unittest.mock.patch(
            "forge_config.schema.OpenAPISource.model_validate",
            side_effect=ValueError("Invalid source config"),
        ):
            # Patch the import so it doesn't fail first
            mock_module = MagicMock()
            with unittest.mock.patch.dict(
                "sys.modules", {"forge_agent.builder.openapi": mock_module}
            ):
                resp = await async_client.post(
                    "/v1/admin/tools/preview",
                    json={"source": {"bad": "data"}},
                    headers=auth_headers,
                )
        assert resp.status_code == 400


# =========================================================================
# 13. Coverage for missed lines — list_sessions with data (lines 211-226)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestListSessionsWithData:
    """Cover list_sessions when agent has context with sessions."""

    async def test_list_sessions_with_sessions(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 211-226: agent has context with session store containing sessions."""
        session1 = MagicMock()
        session1.messages = ["msg1", "msg2", "msg3"]
        session1.agent = "assistant"

        session2 = MagicMock()
        session2.messages = []
        session2.agent = None

        mock_context = MagicMock()
        mock_context._sessions = {"sess-1": session1, "sess-2": session2}

        mock_agent = MagicMock()
        mock_agent._context = mock_context

        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.get("/v1/admin/sessions", headers=auth_headers)
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 2

        # Find each session in the response
        sess_map = {s["session_id"]: s for s in sessions}
        assert sess_map["sess-1"]["message_count"] == 3
        assert sess_map["sess-1"]["agent"] == "assistant"
        assert sess_map["sess-2"]["message_count"] == 0
        assert sess_map["sess-2"]["agent"] is None

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)

    async def test_list_sessions_agent_without_context(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 211-212: agent exists but has no _context attribute."""
        mock_agent = MagicMock(spec=[])  # No _context attribute
        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.get("/v1/admin/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)


# =========================================================================
# 14. Coverage for missed lines — delete_session success (lines 238-247)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestDeleteSessionSuccess:
    """Cover delete_session success and context-not-found paths."""

    async def test_delete_session_success(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 242-247: session exists and is deleted successfully."""
        session = MagicMock()
        session.messages = ["msg1"]
        session.agent = "assistant"

        mock_context = MagicMock()
        mock_context._sessions = {"target-session": session}

        mock_agent = MagicMock()
        mock_agent._context = mock_context

        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.delete("/v1/admin/sessions/target-session", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["session_id"] == "target-session"
        # Verify session was actually removed
        assert "target-session" not in mock_context._sessions

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)

    async def test_delete_session_not_found(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 243-244: session ID not in session store -> 404."""
        mock_context = MagicMock()
        mock_context._sessions = {"other-session": MagicMock()}

        mock_agent = MagicMock()
        mock_agent._context = mock_context

        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.delete("/v1/admin/sessions/nonexistent", headers=auth_headers)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)

    async def test_delete_session_no_context(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 238-240: agent exists but has no _context -> 404."""
        mock_agent = MagicMock(spec=[])  # No _context attribute
        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.delete("/v1/admin/sessions/some-id", headers=auth_headers)
        assert resp.status_code == 404
        assert "context" in resp.json()["detail"].lower()

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)


# =========================================================================
# 15. Coverage for missed lines — list_peers with no config (line 260)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestListPeersNoConfig:
    """Cover list_peers when _config is None."""

    async def test_list_peers_no_config_returns_empty(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Line 260: _config is None -> return []."""
        admin.set_state(config=None, config_path="", agent=None)

        resp = await async_client.get("/v1/admin/peers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)


# =========================================================================
# 16. Coverage for missed lines — ping_peer edge cases (lines 285, 308-309)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestPingPeerEdgeCases:
    """Cover ping_peer when config is None and HTTP error paths."""

    async def test_ping_peer_no_config_returns_404(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Line 285: _config is None -> 404."""
        admin.set_state(config=None, config_path="", agent=None)

        resp = await async_client.post("/v1/admin/peers/any-peer/ping", headers=auth_headers)
        assert resp.status_code == 404
        assert "No config loaded" in resp.json()["detail"]

    async def test_ping_peer_http_error_returns_unreachable(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 308-309: HTTPError during ping returns unreachable status."""
        import unittest.mock
        from unittest.mock import AsyncMock

        config = ForgeConfig(
            agents=AgentsConfig(
                peers=[
                    PeerAgent(
                        name="peer1",
                        endpoint="http://peer1.example.com:8000",
                        capabilities=["search"],
                    )
                ]
            )
        )
        admin.set_state(config=config, config_path="/tmp/test.yaml", agent=None)  # noqa: S108

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        with unittest.mock.patch("httpx.AsyncClient", return_value=mock_http):
            resp = await async_client.post("/v1/admin/peers/peer1/ping", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "peer1"
        assert data["status"] == "unreachable"
        assert "error" in data

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)

    async def test_ping_peer_timeout_returns_unreachable(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 308-309: Timeout during ping returns unreachable status."""
        import unittest.mock
        from unittest.mock import AsyncMock

        config = ForgeConfig(
            agents=AgentsConfig(
                peers=[
                    PeerAgent(
                        name="peer-timeout",
                        endpoint="http://slow-peer.example.com:8000",
                        capabilities=["compute"],
                    )
                ]
            )
        )
        admin.set_state(config=config, config_path="/tmp/test.yaml", agent=None)  # noqa: S108

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.ReadTimeout("Request timed out"))
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        with unittest.mock.patch("httpx.AsyncClient", return_value=mock_http):
            resp = await async_client.post(
                "/v1/admin/peers/peer-timeout/ping", headers=auth_headers
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "peer-timeout"
        assert data["status"] == "unreachable"
        assert "error" in data

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)

    async def test_ping_peer_http_status_error_returns_unreachable(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Lines 308-309: HTTP status error (e.g. 500) returns unreachable."""
        import unittest.mock
        from unittest.mock import AsyncMock

        config = ForgeConfig(
            agents=AgentsConfig(
                peers=[
                    PeerAgent(
                        name="peer-500",
                        endpoint="http://error-peer.example.com:8000",
                        capabilities=[],
                    )
                ]
            )
        )
        admin.set_state(config=config, config_path="/tmp/test.yaml", agent=None)  # noqa: S108

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_request = httpx.Request("GET", "http://error-peer.example.com:8000/health/live")
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error", request=mock_request, response=mock_resp
            )
        )

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        with unittest.mock.patch("httpx.AsyncClient", return_value=mock_http):
            resp = await async_client.post("/v1/admin/peers/peer-500/ping", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "peer-500"
        assert data["status"] == "unreachable"
        assert "error" in data

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)


# =========================================================================
# 17. Coverage for missed line — _classify_tool_source peer prefix (line 318)
# =========================================================================


class TestClassifyToolSource:
    """Cover _classify_tool_source helper."""

    def test_classify_peer_tool(self) -> None:
        """Line 318: tool name starting with 'peer_' classified as 'peer'."""
        assert admin._classify_tool_source("peer_search") == "peer"
        assert admin._classify_tool_source("peer_execute") == "peer"

    def test_classify_regular_tool(self) -> None:
        """Line 319: non-peer tool classified as 'configured'."""
        assert admin._classify_tool_source("search_api") == "configured"
        assert admin._classify_tool_source("my_tool") == "configured"

    def test_classify_tool_with_peer_in_middle(self) -> None:
        """Tool with 'peer' in name but not as prefix is 'configured'."""
        assert admin._classify_tool_source("my_peer_tool") == "configured"


# =========================================================================
# 18. Coverage for list_tools with peer-prefixed tools
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestListToolsWithPeerTools:
    """Cover list_tools with peer-prefixed tools to hit line 318 via the endpoint."""

    async def test_list_tools_with_peer_tools(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Ensure peer tools are classified with source='peer'."""
        mock_tool_regular = MagicMock()
        mock_tool_regular.name = "search_api"
        mock_tool_regular.description = "Search API"

        mock_tool_peer = MagicMock()
        mock_tool_peer.name = "peer_search"
        mock_tool_peer.description = "Peer search tool"

        mock_registry = MagicMock()
        mock_registry.tools = [mock_tool_regular, mock_tool_peer]

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        admin.set_state(
            config=ForgeConfig(),
            config_path="/tmp/test.yaml",  # noqa: S108
            agent=mock_agent,
        )

        resp = await async_client.get("/v1/admin/tools", headers=auth_headers)
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 2

        tool_map = {t["name"]: t for t in tools}
        assert tool_map["search_api"]["source"] == "configured"
        assert tool_map["peer_search"]["source"] == "peer"

        # Cleanup
        admin.set_state(config=None, config_path="", agent=None)
