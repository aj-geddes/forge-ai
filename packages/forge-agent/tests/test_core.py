"""Tests for ForgeAgent core."""

from __future__ import annotations

import pytest
from forge_agent.agent.core import ForgeAgent
from forge_config.schema import (
    ForgeConfig,
    HTTPMethod,
    LLMConfig,
    ManualTool,
    ManualToolAPI,
    ParameterDef,
    ParamType,
    ToolsConfig,
)
from pydantic import BaseModel
from pydantic_ai.models.test import TestModel


def _make_config(
    manual_tools: list[ManualTool] | None = None,
    system_prompt: str | None = None,
) -> ForgeConfig:
    """Create a ForgeConfig for testing."""
    return ForgeConfig(
        llm=LLMConfig(
            default_model="test",
            system_prompt=system_prompt,
        ),
        tools=ToolsConfig(manual=manual_tools or []),
    )


class TestForgeAgentInitialization:
    """Tests for ForgeAgent initialization."""

    @pytest.mark.anyio
    async def test_initialize_creates_agent(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        assert agent._agent is not None

    @pytest.mark.anyio
    async def test_initialize_builds_tools(self) -> None:
        config = _make_config(
            manual_tools=[
                ManualTool(
                    name="greet",
                    description="Greet someone",
                    parameters=[ParameterDef(name="name", type=ParamType.STRING)],
                    api=ManualToolAPI(url="https://api.example.com/greet", method=HTTPMethod.GET),
                ),
            ]
        )
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        assert agent.registry.tool_count == 1

    def test_properties_accessible(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        assert agent.registry is not None
        assert agent.context is not None
        assert agent.llm_router is not None


class TestForgeAgentConversational:
    """Tests for ForgeAgent.run_conversational."""

    @pytest.mark.anyio
    async def test_run_conversational_returns_string(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_conversational("Hello!")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.anyio
    async def test_run_conversational_auto_initializes(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        # Should auto-initialize on first call.
        result = await agent.run_conversational("Hello!")
        assert isinstance(result, str)
        assert agent._agent is not None

    @pytest.mark.anyio
    async def test_run_conversational_with_session(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        # First message in session.
        result1 = await agent.run_conversational("Hello!", session_id="sess1")
        assert isinstance(result1, str)

        # Second message should have context.
        result2 = await agent.run_conversational("Follow up", session_id="sess1")
        assert isinstance(result2, str)

        # Session should have messages stored.
        assert agent.context.message_count("sess1") > 0

    @pytest.mark.anyio
    async def test_run_conversational_stream(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_conversational("Hello!", stream=True)
        # Result should be an async iterator.
        assert hasattr(result, "__aiter__")
        chunks: list[str] = []
        async for chunk in result:
            chunks.append(chunk)

        assert len(chunks) > 0


class TestForgeAgentStructured:
    """Tests for ForgeAgent.run_structured."""

    @pytest.mark.anyio
    async def test_run_structured_with_schema(self) -> None:
        class PersonOutput(BaseModel):
            name: str
            age: int

        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_structured(
            "Create a person",
            output_schema=PersonOutput,
        )
        assert isinstance(result, PersonOutput)

    @pytest.mark.anyio
    async def test_run_structured_without_schema(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_structured("Do something")
        assert isinstance(result, dict)
        assert "result" in result

    @pytest.mark.anyio
    async def test_run_structured_with_params(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_structured(
            "Generate report",
            params={"format": "pdf", "pages": 5},
        )
        assert isinstance(result, dict)

    @pytest.mark.anyio
    async def test_run_structured_auto_initializes(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        await agent.run_structured("Do something")
        assert agent._agent is not None
