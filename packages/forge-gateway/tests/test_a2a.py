"""Tests for A2A endpoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_gateway.routes import a2a
from forge_gateway.routes.a2a import AgentCard


@pytest.fixture
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_structured.return_value = {"processed": True}
    return agent


@pytest.fixture
def client(mock_agent: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(a2a.router)
    a2a.set_agent(mock_agent)
    yield TestClient(app)
    a2a.set_agent(None)
    a2a.set_agent_card(None)


class TestAgentCard:
    def test_default_card(self, client: TestClient) -> None:
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "forge"

    def test_custom_card(self, client: TestClient) -> None:
        a2a.set_agent_card(
            AgentCard(
                name="my-agent",
                description="Custom agent",
                capabilities=["search", "analyze"],
            )
        )
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "my-agent"
        assert "search" in data["capabilities"]


class TestA2ATask:
    def test_submit_task(self, client: TestClient) -> None:
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "search", "payload": {"q": "test"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"] == {"processed": True}

    def test_task_failure(self, client: TestClient, mock_agent: AsyncMock) -> None:
        mock_agent.run_structured.side_effect = RuntimeError("Agent error")
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "fail", "payload": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Agent error" in data["error"]
