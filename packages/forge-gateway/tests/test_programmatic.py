"""Tests for programmatic API endpoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_gateway.routes import programmatic


@pytest.fixture
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_structured.return_value = {"answer": "42"}
    return agent


@pytest.fixture
def client(mock_agent: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(programmatic.router)
    programmatic.set_agent(mock_agent)
    yield TestClient(app)
    programmatic.set_agent(None)


class TestInvokeEndpoint:
    def test_invoke_success(self, client: TestClient, mock_agent: AsyncMock) -> None:
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "params": {"q": "hello"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == {"answer": "42"}
        mock_agent.run_structured.assert_called_once()

    def test_invoke_no_agent(self) -> None:
        app = FastAPI()
        app.include_router(programmatic.router)
        programmatic.set_agent(None)
        tc = TestClient(app)
        response = tc.post("/v1/agent/invoke", json={"intent": "test"})
        assert response.status_code == 503

    def test_invoke_agent_error(self, client: TestClient, mock_agent: AsyncMock) -> None:
        mock_agent.run_structured.side_effect = RuntimeError("LLM timeout")
        response = client.post("/v1/agent/invoke", json={"intent": "fail"})
        assert response.status_code == 500
