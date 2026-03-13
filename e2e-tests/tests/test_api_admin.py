"""E2E tests for admin API endpoints with API key auth."""

from __future__ import annotations

import httpx


class TestAdminConfig:
    """GET/PUT /v1/admin/config - config management."""

    def test_config_requires_auth(self, client: httpx.Client) -> None:
        """Config endpoint requires API key."""
        response = client.get("/v1/admin/config")
        assert response.status_code in (401, 403)

    def test_config_returns_200_with_key(self, admin_client: httpx.Client) -> None:
        """Config endpoint returns 200 with valid API key."""
        response = admin_client.get("/v1/admin/config")
        assert response.status_code == 200

    def test_config_response_structure(self, admin_client: httpx.Client) -> None:
        """Config response has expected structure."""
        data = admin_client.get("/v1/admin/config").json()
        assert "config" in data
        assert "path" in data
        config = data["config"]
        assert "metadata" in config
        assert "llm" in config
        assert "tools" in config
        assert "security" in config

    def test_config_redacts_secrets(self, admin_client: httpx.Client) -> None:
        """API keys are redacted in config response."""
        data = admin_client.get("/v1/admin/config").json()
        config = data["config"]
        keys = config["security"]["api_keys"]["keys"]
        for key in keys:
            assert "REDACTED" in str(key.get("name", "")) or "REDACTED" in str(key.get("key", ""))


class TestAdminConfigSchema:
    """GET /v1/admin/config/schema - JSON schema."""

    def test_schema_requires_auth(self, client: httpx.Client) -> None:
        response = client.get("/v1/admin/config/schema")
        assert response.status_code in (401, 403)

    def test_schema_returns_200_with_key(self, admin_client: httpx.Client) -> None:
        response = admin_client.get("/v1/admin/config/schema")
        assert response.status_code == 200

    def test_schema_is_valid_json_schema(self, admin_client: httpx.Client) -> None:
        schema = admin_client.get("/v1/admin/config/schema").json()
        assert "properties" in schema or "$defs" in schema


class TestAdminTools:
    """GET /v1/admin/tools - tool listing."""

    def test_tools_requires_auth(self, client: httpx.Client) -> None:
        response = client.get("/v1/admin/tools")
        assert response.status_code in (401, 403)

    def test_tools_returns_200_with_key(self, admin_client: httpx.Client) -> None:
        response = admin_client.get("/v1/admin/tools")
        assert response.status_code == 200

    def test_tools_returns_list(self, admin_client: httpx.Client) -> None:
        data = admin_client.get("/v1/admin/tools").json()
        assert isinstance(data, list)


class TestAdminSessions:
    """GET /v1/admin/sessions - session listing."""

    def test_sessions_requires_auth(self, client: httpx.Client) -> None:
        response = client.get("/v1/admin/sessions")
        assert response.status_code in (401, 403)

    def test_sessions_returns_200_with_key(self, admin_client: httpx.Client) -> None:
        response = admin_client.get("/v1/admin/sessions")
        assert response.status_code == 200

    def test_sessions_returns_list(self, admin_client: httpx.Client) -> None:
        data = admin_client.get("/v1/admin/sessions").json()
        assert isinstance(data, list)


class TestAdminPeers:
    """GET /v1/admin/peers - peer listing."""

    def test_peers_requires_auth(self, client: httpx.Client) -> None:
        response = client.get("/v1/admin/peers")
        assert response.status_code in (401, 403)

    def test_peers_returns_200_with_key(self, admin_client: httpx.Client) -> None:
        response = admin_client.get("/v1/admin/peers")
        assert response.status_code == 200

    def test_peers_returns_list(self, admin_client: httpx.Client) -> None:
        data = admin_client.get("/v1/admin/peers").json()
        assert isinstance(data, list)


class TestAdminAuthEdgeCases:
    """Edge cases for admin authentication."""

    def test_invalid_api_key_returns_401(self, client: httpx.Client) -> None:
        response = client.get(
            "/v1/admin/config",
            headers={"Authorization": "Bearer invalid-key-here"},
        )
        assert response.status_code == 401

    def test_empty_bearer_rejected(self, client: httpx.Client) -> None:
        """Empty bearer token is rejected at protocol level."""
        import httpx as _httpx

        try:
            response = client.get(
                "/v1/admin/config",
                headers={"Authorization": "Bearer "},
            )
            # If it does reach the server, it should be an error
            assert response.status_code in (401, 403)
        except _httpx.LocalProtocolError:
            # httpx rejects "Bearer " as an illegal header value - acceptable
            pass

    def test_x_api_key_header_works(self, client: httpx.Client) -> None:
        """X-API-Key header is also accepted."""
        from conftest import ADMIN_API_KEY

        response = client.get(
            "/v1/admin/config",
            headers={"X-API-Key": ADMIN_API_KEY},
        )
        assert response.status_code == 200
