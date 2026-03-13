"""Tests for programmatic API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import AgentDef, AgentsConfig, ForgeConfig, LLMConfig
from forge_gateway.routes import programmatic
from pydantic import BaseModel


def _mock_run_result(
    output: object = None,
    tools_used: list[str] | None = None,
    model_name: str | None = None,
) -> ForgeRunResult:
    """Create a ForgeRunResult for testing."""
    return ForgeRunResult(
        output=output if output is not None else {"answer": "42"},
        tools_used=tools_used or [],
        model_name=model_name,
    )


@pytest.fixture
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_structured.return_value = _mock_run_result()
    return agent


@pytest.fixture
def client(mock_agent: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(programmatic.router)
    programmatic.set_agent(mock_agent)
    programmatic.set_config(None)
    yield TestClient(app)
    programmatic.set_agent(None)
    programmatic.set_config(None)


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
        mock_agent.run_structured.return_value = _mock_run_result(
            output={"name": "Alice", "age": 30}
        )
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


class TestInvokeToolsUsedAndModel:
    """Verify tools_used and model fields are populated in invoke responses."""

    def test_invoke_includes_tools_used_when_tools_called(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When tools are invoked, tools_used contains their names."""
        mock_agent.run_structured.return_value = _mock_run_result(
            output={"summary": "done"},
            tools_used=["search_api", "fetch_url"],
        )
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "summarize page"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == ["search_api", "fetch_url"]

    def test_invoke_includes_model_string(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """The model field reflects the LLM model used for the run."""
        mock_agent.run_structured.return_value = _mock_run_result(
            model_name="gpt-4o-2024-05-13",
        )
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "gpt-4o-2024-05-13"

    def test_invoke_empty_tools_used_when_no_tools_called(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When no tools are invoked, tools_used is an empty list."""
        mock_agent.run_structured.return_value = _mock_run_result(
            tools_used=[],
        )
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "simple question"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == []

    def test_invoke_model_none_when_not_available(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When no model info is available, model is None."""
        mock_agent.run_structured.return_value = _mock_run_result(
            model_name=None,
        )
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] is None

    def test_invoke_tools_used_and_model_together(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Both tools_used and model are present in the same response."""
        mock_agent.run_structured.return_value = _mock_run_result(
            output={"result": "computed"},
            tools_used=["calculator", "database_query"],
            model_name="claude-3-opus-20240229",
        )
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "compute and store"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == ["calculator", "database_query"]
        assert data["model"] == "claude-3-opus-20240229"
        assert data["result"] == {"result": "computed"}

    def test_invoke_single_tool_used(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """A single tool in tools_used is returned as a one-element list."""
        mock_agent.run_structured.return_value = _mock_run_result(
            tools_used=["web_search"],
        )
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "search"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == ["web_search"]
        assert len(data["tools_used"]) == 1


class TestInvokePersonaRouting:
    """Tests for agent persona routing in the invoke endpoint."""

    @pytest.fixture
    def config_with_personas(self) -> ForgeConfig:
        return ForgeConfig(
            llm=LLMConfig(default_model="gpt-4o"),
            agents=AgentsConfig(
                agents=[
                    AgentDef(
                        name="coder",
                        description="A coding assistant",
                        system_prompt="You are a coding assistant.",
                        model="gpt-4o-mini",
                    ),
                    AgentDef(
                        name="writer",
                        description="A writing assistant",
                        system_prompt="You are a creative writer.",
                    ),
                ]
            ),
        )

    @pytest.fixture
    def persona_client(
        self, mock_agent: AsyncMock, config_with_personas: ForgeConfig
    ) -> TestClient:
        app = FastAPI()
        app.include_router(programmatic.router)
        programmatic.set_agent(mock_agent)
        programmatic.set_config(config_with_personas)
        yield TestClient(app)
        programmatic.set_agent(None)
        programmatic.set_config(None)

    def test_invoke_with_known_persona(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Invoking with a known persona passes overrides to the agent."""
        response = persona_client.post(
            "/v1/agent/invoke",
            json={"intent": "write code", "agent": "coder"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding assistant."
        assert call_kwargs["model_name_override"] == "gpt-4o-mini"

    def test_invoke_with_unknown_persona_returns_404(self, persona_client: TestClient) -> None:
        """Invoking with an unknown persona returns 404."""
        response = persona_client.post(
            "/v1/agent/invoke",
            json={"intent": "do something", "agent": "nonexistent"},
        )
        assert response.status_code == 404
        assert "Unknown agent persona" in response.json()["detail"]

    def test_invoke_without_persona_uses_defaults(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Invoking without agent field passes None overrides."""
        response = persona_client.post(
            "/v1/agent/invoke",
            json={"intent": "do something"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_invoke_with_empty_agent_uses_defaults(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Invoking with agent="" is treated the same as omitting it."""
        response = persona_client.post(
            "/v1/agent/invoke",
            json={"intent": "do something", "agent": ""},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_invoke_persona_without_model_override(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """A persona with no model override passes None for model_name_override."""
        response = persona_client.post(
            "/v1/agent/invoke",
            json={"intent": "write essay", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a creative writer."
        assert call_kwargs["model_name_override"] is None

    def test_invoke_persona_no_config_returns_404(self, mock_agent: AsyncMock) -> None:
        """When no config is loaded, any persona name returns 404."""
        app = FastAPI()
        app.include_router(programmatic.router)
        programmatic.set_agent(mock_agent)
        programmatic.set_config(None)
        tc = TestClient(app)
        response = tc.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "coder"},
        )
        assert response.status_code == 404
        programmatic.set_agent(None)


class TestErrorDetailRedaction:
    """HTTP 500 responses must not leak internal error details to clients."""

    def test_invoke_internal_error_does_not_leak_details(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Internal exception messages must be redacted from 500 responses."""
        mock_agent.run_structured.side_effect = ValueError("secret internal path /etc/db.conf")
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "fail"},
        )
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail == "Internal server error"
        assert "/etc/db.conf" not in detail
        assert "secret" not in detail
