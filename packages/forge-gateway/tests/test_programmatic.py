"""Tests for programmatic API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_gateway.routes import programmatic
from pydantic import BaseModel


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

    def test_invoke_with_output_schema_passes_basemodel(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When output_schema is a JSON Schema dict, the agent receives a BaseModel class."""
        mock_agent.run_structured.return_value = {"name": "Alice", "age": 30}
        response = client.post(
            "/v1/agent/invoke",
            json={
                "intent": "create person",
                "output_schema": {
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name"],
                },
            },
        )
        assert response.status_code == 200

        # Verify the agent received a BaseModel subclass, not a raw dict.
        call_kwargs = mock_agent.run_structured.call_args
        schema_arg = call_kwargs.kwargs.get("output_schema") or call_kwargs[1].get("output_schema")
        assert schema_arg is not None
        assert isinstance(schema_arg, type)
        assert issubclass(schema_arg, BaseModel)

    def test_invoke_without_output_schema_passes_none(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When output_schema is omitted, the agent receives None."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "do something"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args
        schema_arg = call_kwargs.kwargs.get("output_schema") or call_kwargs[1].get("output_schema")
        assert schema_arg is None

    def test_invoke_with_invalid_schema_returns_error(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """A schema with no properties should return a 500 error."""
        response = client.post(
            "/v1/agent/invoke",
            json={
                "intent": "test",
                "output_schema": {"type": "object"},
            },
        )
        assert response.status_code == 500
