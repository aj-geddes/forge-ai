"""E2E tests for metrics endpoint."""

from __future__ import annotations

import httpx


class TestMetrics:
    """GET /metrics - Prometheus metrics."""

    def test_metrics_returns_200(self, client: httpx.Client) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_returns_text(self, client: httpx.Client) -> None:
        response = client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_contains_content(self, client: httpx.Client) -> None:
        """Should return either prometheus metrics or a fallback message."""
        response = client.get("/metrics")
        text = response.text
        # Either real prometheus output or the fallback
        assert "prometheus_client not available" in text or "# HELP" in text or "# TYPE" in text
