"""E2E tests for health check endpoints."""

from __future__ import annotations

import httpx


class TestLiveness:
    """GET /health/live - should always return 200 when the server is up."""

    def test_liveness_returns_200(self, client: httpx.Client) -> None:
        response = client.get("/health/live")
        assert response.status_code == 200

    def test_liveness_response_schema(self, client: httpx.Client) -> None:
        data = client.get("/health/live").json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "components" in data

    def test_liveness_content_type(self, client: httpx.Client) -> None:
        response = client.get("/health/live")
        assert "application/json" in response.headers["content-type"]


class TestReadiness:
    """GET /health/ready - depends on startup state."""

    def test_readiness_returns_200_when_ready(self, client: httpx.Client) -> None:
        response = client.get("/health/ready")
        # Server is running so it should be ready
        assert response.status_code == 200

    def test_readiness_response_schema(self, client: httpx.Client) -> None:
        data = client.get("/health/ready").json()
        assert data["status"] == "ready"


class TestStartup:
    """GET /health/startup - indicates startup completion."""

    def test_startup_returns_200(self, client: httpx.Client) -> None:
        response = client.get("/health/startup")
        assert response.status_code == 200

    def test_startup_response_schema(self, client: httpx.Client) -> None:
        data = client.get("/health/startup").json()
        assert data["status"] == "started"


class TestHealthCrossEndpoint:
    """Cross-endpoint validation."""

    def test_all_health_endpoints_respond(self, client: httpx.Client) -> None:
        """All three health endpoints should respond concurrently."""
        endpoints = ["/health/live", "/health/ready", "/health/startup"]
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 200, f"{endpoint} failed with {response.status_code}"

    def test_health_endpoints_have_consistent_schema(self, client: httpx.Client) -> None:
        """All health endpoints return the same HealthResponse shape."""
        endpoints = ["/health/live", "/health/ready", "/health/startup"]
        for endpoint in endpoints:
            data = client.get(endpoint).json()
            assert "status" in data, f"{endpoint} missing 'status'"
            assert "version" in data, f"{endpoint} missing 'version'"
            assert "components" in data, f"{endpoint} missing 'components'"
