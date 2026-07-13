"""Tests for Prometheus metrics endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_gateway import metrics_registry
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


class TestMetricsExposesApplicationMetrics:
    """GET /metrics with the real (unmocked) prometheus_client exposes the
    application metrics registered in forge_gateway.metrics_registry --
    not just the default process/GC collectors."""

    def test_metrics_output_includes_http_requests_metric_name(self, client: TestClient) -> None:
        """Verify /metrics exposes forge_http_requests_total after recording a request."""
        metrics_registry.record_http_request("GET", "/unit-test-visible-path", 200, 0.01)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "forge_http_requests_total" in response.text

    def test_metrics_output_includes_agent_invocations_metric_name(
        self, client: TestClient
    ) -> None:
        """Verify /metrics exposes forge_agent_invocations_total after recording an invocation."""
        metrics_registry.record_agent_invocation("unit-test-chat", "success", 0.1)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "forge_agent_invocations_total" in response.text

    def test_metrics_output_includes_tool_invocations_metric_name(self, client: TestClient) -> None:
        """Verify /metrics exposes forge_tool_invocations_total after recording an invocation."""
        metrics_registry.record_tool_invocation("unit_test_visible_tool")
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "forge_tool_invocations_total" in response.text

    def test_metrics_output_reflects_incremented_counter_value(self, client: TestClient) -> None:
        """Verify /metrics counter value increments correctly for repeated observations."""
        metrics_registry.record_http_request("GET", "/unit-test-value-check", 200, 0.01)
        metrics_registry.record_http_request("GET", "/unit-test-value-check", 200, 0.01)
        response = client.get("/metrics")
        assert response.status_code == 200

        lines = response.text.splitlines()
        target_line = None
        for line in lines:
            if (
                "forge_http_requests_total{" in line
                and 'path="/unit-test-value-check"' in line
                and 'method="GET"' in line
                and 'status="200"' in line
            ):
                target_line = line
                break

        assert target_line is not None, "Expected metric line not found in /metrics output"

        # Extract numeric value after closing brace
        brace_end = target_line.rfind("}")
        value_str = target_line[brace_end + 1 :].strip()
        value = float(value_str)
        assert value >= 2.0, f"Expected counter >= 2.0, got {value}"
