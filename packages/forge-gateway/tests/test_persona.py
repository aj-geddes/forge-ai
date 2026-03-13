"""Tests for agent persona routing.

Verifies that the ``request.agent`` field correctly selects a configured
persona and that the persona's overrides (system_prompt, model, max_turns)
are forwarded to the underlying ForgeAgent methods.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import AgentDef, AgentsConfig, ForgeConfig
from forge_gateway.routes import conversational, programmatic
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_config(*personas: AgentDef, default: str = "assistant") -> ForgeConfig:
    """Build a minimal ForgeConfig with the given agent personas."""
    return ForgeConfig(
        agents=AgentsConfig(
            default=default,
            agents=list(personas),
        ),
    )


# Three reusable persona definitions
CODER = AgentDef(
    name="coder",
    description="A coding assistant",
    system_prompt="You are a coding expert.",
    model="gpt-4o",
    max_turns=5,
)

WRITER = AgentDef(
    name="writer",
    description="A creative writer",
    system_prompt="You are a creative writing assistant.",
    model="claude-3-opus-20240229",
    max_turns=15,
)

MINIMAL = AgentDef(
    name="minimal",
    description="Bare-bones persona with no overrides",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_agent() -> AsyncMock:
    """Mock agent with default run results for both structured and conversational."""
    agent = AsyncMock()
    agent.run_structured.return_value = _mock_run_result()
    agent.run_conversational.return_value = ForgeRunResult(
        output="Hello from persona!",
    )
    return agent


@pytest.fixture()
def config_with_personas() -> ForgeConfig:
    """ForgeConfig populated with coder, writer, and minimal personas."""
    return _make_config(CODER, WRITER, MINIMAL)


@pytest.fixture()
def invoke_client(mock_agent: AsyncMock, config_with_personas: ForgeConfig) -> Iterator[TestClient]:
    """TestClient wired to the programmatic router with personas configured."""
    app = FastAPI()
    app.include_router(programmatic.router)
    programmatic.set_agent(mock_agent)
    programmatic.set_config(config_with_personas)
    yield TestClient(app)
    programmatic.set_agent(None)
    programmatic.set_config(None)


@pytest.fixture()
def chat_client(mock_agent: AsyncMock, config_with_personas: ForgeConfig) -> Iterator[TestClient]:
    """TestClient wired to the conversational router with personas configured."""
    app = FastAPI()
    app.include_router(conversational.router)
    conversational.set_agent(mock_agent)
    conversational.set_config(config_with_personas)
    yield TestClient(app)
    conversational.set_agent(None)
    conversational.set_config(None)


# ===========================================================================
# 1. Default (no agent specified) -> uses default agent behaviour
# ===========================================================================


class TestDefaultPersona:
    """When no agent field is provided, the default behaviour is used."""

    def test_invoke_default_no_agent_field(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Omitting the agent field invokes with no persona overrides."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_chat_default_no_agent_field(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Omitting the agent field invokes with no persona overrides."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_invoke_agent_null(self, invoke_client: TestClient, mock_agent: AsyncMock) -> None:
        """Explicitly passing agent=null behaves like omitting it."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": None},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_chat_agent_null(self, chat_client: TestClient, mock_agent: AsyncMock) -> None:
        """Explicitly passing agent=null behaves like omitting it."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": None},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None


# ===========================================================================
# 2. Valid agent name -> persona overrides applied
# ===========================================================================


class TestValidPersonaSelection:
    """A valid agent name resolves the correct persona and applies overrides."""

    def test_invoke_valid_persona_applies_system_prompt_and_model(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The coder persona's system_prompt and model are forwarded."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "write code", "agent": "coder"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding expert."
        assert call_kwargs["model_name_override"] == "gpt-4o"

    def test_chat_valid_persona_applies_system_prompt_and_model(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The coder persona's system_prompt and model are forwarded."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Help me code", "agent": "coder"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding expert."
        assert call_kwargs["model_name_override"] == "gpt-4o"


# ===========================================================================
# 3. Unknown agent name -> 404 "Unknown agent persona"
# ===========================================================================


class TestUnknownPersona:
    """Requesting an unknown persona returns a 404 error."""

    def test_invoke_unknown_persona_returns_404(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "nonexistent"},
        )
        assert response.status_code == 404
        assert "Unknown agent persona" in response.json()["detail"]
        mock_agent.run_structured.assert_not_called()

    def test_chat_unknown_persona_returns_404(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "nonexistent"},
        )
        assert response.status_code == 404
        assert "Unknown agent persona" in response.json()["detail"]
        mock_agent.run_conversational.assert_not_called()

    def test_invoke_unknown_persona_no_config(self, mock_agent: AsyncMock) -> None:
        """When no config is set, any non-empty agent name returns 404."""
        app = FastAPI()
        app.include_router(programmatic.router)
        programmatic.set_agent(mock_agent)
        programmatic.set_config(None)
        tc = TestClient(app)

        response = tc.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "anything"},
        )
        assert response.status_code == 404

        programmatic.set_agent(None)

    def test_chat_unknown_persona_no_config(self, mock_agent: AsyncMock) -> None:
        """When no config is set, any non-empty agent name returns 404."""
        app = FastAPI()
        app.include_router(conversational.router)
        conversational.set_agent(mock_agent)
        conversational.set_config(None)
        tc = TestClient(app)

        response = tc.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "anything"},
        )
        assert response.status_code == 404

        conversational.set_agent(None)


# ===========================================================================
# 4. Agent field empty string -> treated as default
# ===========================================================================


class TestEmptyStringAgent:
    """An empty-string agent field is treated the same as no agent (default)."""

    def test_invoke_empty_string_uses_default(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": ""},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_chat_empty_string_uses_default(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": ""},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None


# ===========================================================================
# 5. Persona with system_prompt override -> agent receives it
# ===========================================================================


class TestSystemPromptOverride:
    """A persona's system_prompt is forwarded as system_prompt_override."""

    def test_invoke_system_prompt_forwarded(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a creative writing assistant."

    def test_chat_system_prompt_forwarded(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Write a poem", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a creative writing assistant."

    def test_invoke_no_system_prompt_sends_none(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The minimal persona has no system_prompt, so None is forwarded."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "minimal"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None

    def test_chat_no_system_prompt_sends_none(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The minimal persona has no system_prompt, so None is forwarded."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "minimal"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None


# ===========================================================================
# 6. Persona with model override -> agent receives it
# ===========================================================================


class TestModelOverride:
    """A persona's model field is forwarded as model_name_override."""

    def test_invoke_model_forwarded(self, invoke_client: TestClient, mock_agent: AsyncMock) -> None:
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["model_name_override"] == "claude-3-opus-20240229"

    def test_chat_model_forwarded(self, chat_client: TestClient, mock_agent: AsyncMock) -> None:
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Write something", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["model_name_override"] == "claude-3-opus-20240229"

    def test_invoke_no_model_sends_none(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The minimal persona has no model, so None is forwarded."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "minimal"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["model_name_override"] is None

    def test_chat_no_model_sends_none(self, chat_client: TestClient, mock_agent: AsyncMock) -> None:
        """The minimal persona has no model, so None is forwarded."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "minimal"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["model_name_override"] is None


# ===========================================================================
# 7. Persona with max_turns override -> agent receives it
# ===========================================================================


class TestMaxTurnsOverride:
    """A persona's max_turns is part of the resolved AgentDef.

    The current route implementation forwards system_prompt and model
    overrides explicitly. max_turns validation happens at the persona
    resolution level — we verify the AgentDef was correctly resolved
    with the right max_turns value via the _resolve_persona function.
    """

    def test_resolve_persona_coder_max_turns(self, config_with_personas: ForgeConfig) -> None:
        """The coder persona has max_turns=5."""
        programmatic.set_config(config_with_personas)
        persona = programmatic._resolve_persona("coder")
        assert persona is not None
        assert persona.max_turns == 5
        programmatic.set_config(None)

    def test_resolve_persona_writer_max_turns(self, config_with_personas: ForgeConfig) -> None:
        """The writer persona has max_turns=15."""
        programmatic.set_config(config_with_personas)
        persona = programmatic._resolve_persona("writer")
        assert persona is not None
        assert persona.max_turns == 15
        programmatic.set_config(None)

    def test_resolve_persona_minimal_default_max_turns(
        self, config_with_personas: ForgeConfig
    ) -> None:
        """The minimal persona uses the AgentDef default max_turns=10."""
        programmatic.set_config(config_with_personas)
        persona = programmatic._resolve_persona("minimal")
        assert persona is not None
        assert persona.max_turns == 10
        programmatic.set_config(None)


# ===========================================================================
# 8. Multiple personas configured -> correct one selected
# ===========================================================================


class TestMultiplePersonaSelection:
    """When multiple personas are configured, the correct one is selected."""

    def test_invoke_selects_coder_not_writer(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Requesting 'coder' selects coder overrides, not writer."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "coder"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding expert."
        assert call_kwargs["model_name_override"] == "gpt-4o"

    def test_invoke_selects_writer_not_coder(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Requesting 'writer' selects writer overrides, not coder."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a creative writing assistant."
        assert call_kwargs["model_name_override"] == "claude-3-opus-20240229"

    def test_chat_selects_coder_not_writer(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Requesting 'coder' selects coder overrides in chat."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Help", "agent": "coder"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding expert."
        assert call_kwargs["model_name_override"] == "gpt-4o"

    def test_chat_selects_writer_not_coder(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Requesting 'writer' selects writer overrides in chat."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Help", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a creative writing assistant."
        assert call_kwargs["model_name_override"] == "claude-3-opus-20240229"

    def test_invoke_minimal_persona_has_no_overrides(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The minimal persona has no system_prompt or model set."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "minimal"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_structured.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_chat_minimal_persona_has_no_overrides(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The minimal persona has no system_prompt or model set."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "minimal"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] is None
        assert call_kwargs["model_name_override"] is None

    def test_invoke_sequential_persona_switching(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Switching between personas in successive requests uses the correct one each time."""
        # First call: coder
        invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "code task", "agent": "coder"},
        )
        first_kwargs = mock_agent.run_structured.call_args.kwargs
        assert first_kwargs["system_prompt_override"] == "You are a coding expert."

        mock_agent.run_structured.reset_mock()

        # Second call: writer
        invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "write task", "agent": "writer"},
        )
        second_kwargs = mock_agent.run_structured.call_args.kwargs
        assert second_kwargs["system_prompt_override"] == "You are a creative writing assistant."

        mock_agent.run_structured.reset_mock()

        # Third call: no persona (default)
        invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "general task"},
        )
        third_kwargs = mock_agent.run_structured.call_args.kwargs
        assert third_kwargs["system_prompt_override"] is None

    def test_chat_sequential_persona_switching(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Switching between personas in successive chat requests works correctly."""
        # First call: writer
        chat_client.post(
            "/v1/chat/completions",
            json={"message": "Write", "agent": "writer"},
        )
        first_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert first_kwargs["model_name_override"] == "claude-3-opus-20240229"

        mock_agent.run_conversational.reset_mock()
        mock_agent.run_conversational.return_value = ForgeRunResult(output="ok")

        # Second call: coder
        chat_client.post(
            "/v1/chat/completions",
            json={"message": "Code", "agent": "coder"},
        )
        second_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert second_kwargs["model_name_override"] == "gpt-4o"


# ===========================================================================
# Edge cases
# ===========================================================================


class TestPersonaEdgeCases:
    """Edge cases for persona resolution."""

    def test_invoke_case_sensitive_persona_name(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Persona lookup is case-sensitive: 'Coder' != 'coder'."""
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "Coder"},
        )
        assert response.status_code == 404

    def test_chat_case_sensitive_persona_name(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Persona lookup is case-sensitive: 'CODER' != 'coder'."""
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "CODER"},
        )
        assert response.status_code == 404

    def test_invoke_persona_with_empty_agents_list(self, mock_agent: AsyncMock) -> None:
        """When agents list is empty, any agent name returns 404."""
        app = FastAPI()
        app.include_router(programmatic.router)
        programmatic.set_agent(mock_agent)
        programmatic.set_config(_make_config())  # No personas
        tc = TestClient(app)

        response = tc.post(
            "/v1/agent/invoke",
            json={"intent": "test", "agent": "coder"},
        )
        assert response.status_code == 404

        programmatic.set_agent(None)
        programmatic.set_config(None)

    def test_invoke_response_structure_with_persona(
        self, invoke_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The response structure is the same regardless of persona."""
        mock_agent.run_structured.return_value = _mock_run_result(
            output={"code": "print('hello')"},
            tools_used=["code_exec"],
            model_name="gpt-4o",
        )
        response = invoke_client.post(
            "/v1/agent/invoke",
            json={"intent": "code", "agent": "coder"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == {"code": "print('hello')"}
        assert data["tools_used"] == ["code_exec"]
        assert data["model"] == "gpt-4o"

    def test_chat_response_structure_with_persona(
        self, chat_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """The chat response structure is the same regardless of persona."""
        mock_agent.run_conversational.return_value = ForgeRunResult(
            output="Here is your poem.",
            tools_used=["rhyme_finder"],
            model_name="claude-3-opus-20240229",
        )
        response = chat_client.post(
            "/v1/chat/completions",
            json={"message": "Write a poem", "agent": "writer"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Here is your poem."
        assert data["tools_used"] == ["rhyme_finder"]
        assert data["model"] == "claude-3-opus-20240229"


# ===========================================================================
# _resolve_persona unit tests (direct function testing)
# ===========================================================================


class TestResolvePersonaFunction:
    """Direct unit tests for the _resolve_persona helper in both route modules."""

    def test_programmatic_resolve_none(self, config_with_personas: ForgeConfig) -> None:
        """None agent name returns None (default behaviour)."""
        programmatic.set_config(config_with_personas)
        assert programmatic._resolve_persona(None) is None
        programmatic.set_config(None)

    def test_programmatic_resolve_empty_string(self, config_with_personas: ForgeConfig) -> None:
        """Empty string returns None (default behaviour)."""
        programmatic.set_config(config_with_personas)
        assert programmatic._resolve_persona("") is None
        programmatic.set_config(None)

    def test_programmatic_resolve_valid_name(self, config_with_personas: ForgeConfig) -> None:
        """A valid name returns the matching AgentDef."""
        programmatic.set_config(config_with_personas)
        persona = programmatic._resolve_persona("coder")
        assert persona is not None
        assert persona.name == "coder"
        assert persona.system_prompt == "You are a coding expert."
        assert persona.model == "gpt-4o"
        programmatic.set_config(None)

    def test_programmatic_resolve_unknown_raises_404(
        self, config_with_personas: ForgeConfig
    ) -> None:
        """An unknown name raises HTTPException(404)."""
        programmatic.set_config(config_with_personas)
        with pytest.raises(HTTPException) as exc_info:
            programmatic._resolve_persona("unknown")
        assert exc_info.value.status_code == 404
        programmatic.set_config(None)

    def test_conversational_resolve_none(self, config_with_personas: ForgeConfig) -> None:
        """None agent name returns None (default behaviour)."""
        conversational.set_config(config_with_personas)
        assert conversational._resolve_persona(None) is None
        conversational.set_config(None)

    def test_conversational_resolve_empty_string(self, config_with_personas: ForgeConfig) -> None:
        """Empty string returns None (default behaviour)."""
        conversational.set_config(config_with_personas)
        assert conversational._resolve_persona("") is None
        conversational.set_config(None)

    def test_conversational_resolve_valid_name(self, config_with_personas: ForgeConfig) -> None:
        """A valid name returns the matching AgentDef."""
        conversational.set_config(config_with_personas)
        persona = conversational._resolve_persona("writer")
        assert persona is not None
        assert persona.name == "writer"
        assert persona.system_prompt == "You are a creative writing assistant."
        assert persona.model == "claude-3-opus-20240229"
        conversational.set_config(None)

    def test_conversational_resolve_unknown_raises_404(
        self, config_with_personas: ForgeConfig
    ) -> None:
        """An unknown name raises HTTPException(404)."""
        conversational.set_config(config_with_personas)
        with pytest.raises(HTTPException) as exc_info:
            conversational._resolve_persona("ghost")
        assert exc_info.value.status_code == 404
        conversational.set_config(None)

    def test_resolve_no_config_raises_404(self) -> None:
        """When no config is loaded, any non-empty name raises 404."""
        programmatic.set_config(None)
        with pytest.raises(HTTPException) as exc_info:
            programmatic._resolve_persona("anything")
        assert exc_info.value.status_code == 404
