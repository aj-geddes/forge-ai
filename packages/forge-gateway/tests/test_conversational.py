"""Tests for conversational endpoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_gateway.routes import conversational


@pytest.fixture
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_conversational.return_value = "Hello! How can I help?"
    return agent


@pytest.fixture
def client(mock_agent: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(conversational.router)
    conversational.set_agent(mock_agent)
    yield TestClient(app)
    conversational.set_agent(None)


class TestChatEndpoint:
    def test_chat_success(self, client: TestClient, mock_agent: AsyncMock) -> None:
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi there"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Hello! How can I help?"
        assert data["session_id"]  # Auto-generated

    def test_chat_with_session(self, client: TestClient) -> None:
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "session_id": "sess-123"},
        )
        assert response.status_code == 200
        assert response.json()["session_id"] == "sess-123"

    def test_chat_no_agent(self) -> None:
        app = FastAPI()
        app.include_router(conversational.router)
        conversational.set_agent(None)
        tc = TestClient(app)
        response = tc.post("/v1/chat/completions", json={"message": "Hi"})
        assert response.status_code == 503
