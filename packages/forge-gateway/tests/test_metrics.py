"""Tests for Prometheus metrics endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_gateway.routes import metrics


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(metrics.router)
    return TestClient(app)


class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    def test_metrics_returns_200(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_content_type_is_text(self, client: TestClient) -> None:
        response = client.get("/metrics")
        content_type = response.headers["content-type"]
        assert "text/plain" in content_type

    def test_metrics_with_prometheus_installed(self, client: TestClient) -> None:
        mock_output = b"# HELP http_requests_total Total requests\nhttp_requests_total 42\n"
        mock_generate = MagicMock(return_value=mock_output)

        with patch.dict(
            "sys.modules",
            {"prometheus_client": MagicMock(generate_latest=mock_generate)},
        ):
            response = client.get("/metrics")

        assert response.status_code == 200
        assert "http_requests_total" in response.text

    def test_metrics_without_prometheus_returns_fallback(self, client: TestClient) -> None:
        with patch.dict("sys.modules", {"prometheus_client": None}):
            response = client.get("/metrics")

        assert response.status_code == 200
        assert "prometheus_client not available" in response.text

    def test_metrics_fallback_is_valid_comment(self, client: TestClient) -> None:
        with patch.dict("sys.modules", {"prometheus_client": None}):
            response = client.get("/metrics")

        # Prometheus comment lines start with #
        assert response.text.startswith("#")

    def test_metrics_prometheus_output_decoded_utf8(self, client: TestClient) -> None:
        mock_output = b"# TYPE gauge\nmy_metric 1.0\n"
        mock_generate = MagicMock(return_value=mock_output)

        with patch.dict(
            "sys.modules",
            {"prometheus_client": MagicMock(generate_latest=mock_generate)},
        ):
            response = client.get("/metrics")

        assert response.status_code == 200
        assert "my_metric 1.0" in response.text
