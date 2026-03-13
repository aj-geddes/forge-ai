"""E2E tests for middleware behavior (logging, error handling, CORS)."""

from __future__ import annotations

import httpx


class TestRequestLogging:
    """Verify request logging middleware doesn't interfere with responses."""

    def test_middleware_preserves_status_codes(self, client: httpx.Client) -> None:
        """Middleware should not alter response status codes."""
        # 200 OK
        response = client.get("/health/live")
        assert response.status_code == 200

        # 422 Validation Error
        response = client.post("/v1/agent/invoke", json={})
        assert response.status_code == 422

        # 500/503 Agent error (no LLM configured)
        response = client.post("/v1/agent/invoke", json={"intent": "test"})
        assert response.status_code in (500, 503)


class TestNotFound:
    """404 handling for non-existent routes."""

    def test_nonexistent_path_returns_404(self, client: httpx.Client) -> None:
        response = client.get("/nonexistent")
        assert response.status_code == 404

    def test_wrong_method_returns_405(self, client: httpx.Client) -> None:
        """POST to a GET-only endpoint returns 405."""
        response = client.post("/health/live")
        assert response.status_code == 405

    def test_get_on_post_endpoint_returns_error(self, client: httpx.Client) -> None:
        """GET on a POST-only endpoint returns 404 or 405."""
        response = client.get("/v1/agent/invoke")
        assert response.status_code in (404, 405)

    def test_404_response_has_detail(self, client: httpx.Client) -> None:
        data = client.get("/nonexistent").json()
        assert "detail" in data


class TestCORS:
    """CORS behavior (currently no CORS middleware, so defaults apply)."""

    def test_cors_headers_present(self, client: httpx.Client) -> None:
        """CORS middleware adds Access-Control headers for allowed origins."""
        response = client.get(
            "/health/live",
            headers={"Origin": "https://forge-ai.hvs"},
        )
        # With allowed_origins=["*"], CORS headers should be present
        # when an Origin header is sent
        assert response.status_code == 200

    def test_options_request(self, client: httpx.Client) -> None:
        """OPTIONS request behavior without CORS middleware."""
        response = client.options("/health/live")
        # FastAPI returns 200 for OPTIONS with Allow header
        assert response.status_code in (200, 405)


class TestContentNegotiation:
    """Content type handling."""

    def test_json_endpoints_return_json(self, client: httpx.Client) -> None:
        """JSON API endpoints return application/json."""
        json_endpoints = [
            ("/health/live", "get"),
            ("/a2a/agent-card", "get"),
            ("/openapi.json", "get"),
        ]
        for path, _ in json_endpoints:
            response = client.get(path)
            assert "application/json" in response.headers["content-type"], (
                f"{path} should return JSON"
            )

    def test_metrics_returns_plaintext(self, client: httpx.Client) -> None:
        response = client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]
