"""Tests for health check endpoints."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from forge_gateway.routes import health


@pytest.fixture
def client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_health_state() -> Iterator[None]:
    """Ensure module-level health state does not leak between tests."""
    yield
    health.set_ready(False)
    health.set_started(False)
    health.set_version("")
    health.reset_components()
    health.set_workload_health(None)


class TestHealthEndpoints:
    def test_liveness(self, client: TestClient) -> None:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_liveness_ok_even_when_not_ready(self, client: TestClient) -> None:
        """Liveness must stay 200 regardless of readiness/component state."""
        health.set_ready(False)
        health.set_component_status("agent", "failed")

        response = client.get("/health/live")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness_not_ready(self, client: TestClient) -> None:
        health.set_ready(False)
        response = client.get("/health/ready")
        assert response.status_code == 503

    def test_readiness_ready(self, client: TestClient) -> None:
        health.set_ready(True)
        try:
            response = client.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["status"] == "ready"
        finally:
            health.set_ready(False)

    def test_readiness_returns_503_when_agent_failed(self, client: TestClient) -> None:
        """Even if startup otherwise completed, a failed agent must not be 'ready'."""
        health.set_ready(True)
        health.set_component_status("agent", "failed")

        response = client.get("/health/ready")

        assert response.status_code == 503

    def test_readiness_failure_body_surfaces_agent_component(self, client: TestClient) -> None:
        health.set_ready(True)
        health.set_component_status("agent", "failed")

        response = client.get("/health/ready")
        body = response.json()

        assert body["status"] != "ready"
        assert body["components"]["agent"] == "failed"

    def test_readiness_ready_surfaces_agent_component(self, client: TestClient) -> None:
        health.set_ready(True)
        health.set_component_status("agent", "ready")

        response = client.get("/health/ready")
        body = response.json()

        assert response.status_code == 200
        assert body["components"]["agent"] == "ready"

    def test_readiness_includes_version_from_config_metadata(self, client: TestClient) -> None:
        health.set_ready(True)
        health.set_version("1.2.3")

        response = client.get("/health/ready")

        assert response.json()["version"] == "1.2.3"

    def test_readiness_agent_unavailable_does_not_block_readiness(self, client: TestClient) -> None:
        """Gateway-only mode (forge-agent not installed) is a valid ready state."""
        health.set_ready(True)
        health.set_component_status("agent", "unavailable")

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["components"]["agent"] == "unavailable"

    def test_startup_not_started(self, client: TestClient) -> None:
        health.set_started(False)
        response = client.get("/health/startup")
        assert response.status_code == 503

    def test_startup_started(self, client: TestClient) -> None:
        health.set_started(True)
        try:
            response = client.get("/health/startup")
            assert response.status_code == 200
            assert response.json()["status"] == "started"
        finally:
            health.set_started(False)


class TestWorkloadHealthDoesNotGateReadiness:
    """ADR-0004 SS8: the workload (SPIFFE/OPA/:8443-listener) health
    component is reported for visibility but must NEVER gate
    /health/ready -- a SPIRE/OPA outage degrades agent-to-agent traffic
    only, never the human :8000 plane's readiness."""

    def test_workload_unhealthy_does_not_flip_readiness_to_503(self, client: TestClient) -> None:
        health.set_ready(True)
        health.set_workload_health({"status": "unhealthy", "identity": "down", "authz": "down"})

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    def test_workload_health_is_surfaced_in_the_ready_response_body(
        self, client: TestClient
    ) -> None:
        health.set_ready(True)
        health.set_workload_health({"status": "healthy", "identity": "up", "authz": "up"})

        response = client.get("/health/ready")
        body = response.json()

        assert body["workload"] == {"status": "healthy", "identity": "up", "authz": "up"}

    def test_workload_health_defaults_to_none_when_agentweave_disabled(
        self, client: TestClient
    ) -> None:
        health.set_ready(True)

        response = client.get("/health/ready")

        assert response.json()["workload"] is None

    def test_workload_unavailable_reason_does_not_gate_readiness(self, client: TestClient) -> None:
        """SPIRE unreachable at startup: the workload plane never came up
        at all, distinct from "came up but unhealthy" -- readiness must
        still be unaffected."""
        health.set_ready(True)
        health.set_workload_health({"status": "unavailable", "reason": "spire_unreachable"})

        response = client.get("/health/ready")

        assert response.status_code == 200
