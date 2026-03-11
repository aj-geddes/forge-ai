"""Tests for health check endpoints."""

import pytest
from fastapi.testclient import TestClient
from forge_gateway.routes import health


@pytest.fixture
def client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


class TestHealthEndpoints:
    def test_liveness(self, client: TestClient) -> None:
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
