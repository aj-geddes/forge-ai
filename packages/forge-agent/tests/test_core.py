"""Tests for ForgeAgent core."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from forge_agent.agent.core import ForgeAgent, ForgeRunResult
from forge_config.schema import (
    AgentDef,
    AgentsConfig,
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
    agents: list[AgentDef] | None = None,
) -> ForgeConfig:
    """Create a ForgeConfig for testing."""
    return ForgeConfig(
        llm=LLMConfig(
            default_model="test",
            system_prompt=system_prompt,
        ),
        tools=ToolsConfig(manual_tools=manual_tools or []),
        agents=AgentsConfig(agents=agents or []),
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
    async def test_run_conversational_returns_forge_run_result(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_conversational("Hello!")
        assert isinstance(result, ForgeRunResult)
        assert isinstance(result.output, str)
        assert len(result.output) > 0
        assert isinstance(result.tools_used, list)

    @pytest.mark.anyio
    async def test_run_conversational_auto_initializes(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        # Should auto-initialize on first call.
        result = await agent.run_conversational("Hello!")
        assert isinstance(result, ForgeRunResult)
        assert agent._agent is not None

    @pytest.mark.anyio
    async def test_run_conversational_with_session(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        # First message in session.
        result1 = await agent.run_conversational("Hello!", session_id="sess1")
        assert isinstance(result1, ForgeRunResult)

        # Second message should have context.
        result2 = await agent.run_conversational("Follow up", session_id="sess1")
        assert isinstance(result2, ForgeRunResult)

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
        assert isinstance(result, ForgeRunResult)
        assert isinstance(result.output, PersonOutput)
        assert isinstance(result.tools_used, list)

    @pytest.mark.anyio
    async def test_run_structured_without_schema(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_structured("Do something")
        assert isinstance(result, ForgeRunResult)
        assert isinstance(result.output, dict)
        assert "result" in result.output

    @pytest.mark.anyio
    async def test_run_structured_with_params(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_structured(
            "Generate report",
            params={"format": "pdf", "pages": 5},
        )
        assert isinstance(result, ForgeRunResult)
        assert isinstance(result.output, dict)

    @pytest.mark.anyio
    async def test_run_structured_auto_initializes(self) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        await agent.run_structured("Do something")
        assert agent._agent is not None


class TestForgeAgentPersonaRouting:
    """Tests for persona lookup and override support."""

    def test_resolve_persona_found(self) -> None:
        """resolve_persona returns the matching AgentDef."""
        config = _make_config(
            agents=[
                AgentDef(name="coder", description="A coding assistant", system_prompt="Code only"),
                AgentDef(name="writer", description="A writing assistant"),
            ]
        )
        agent = ForgeAgent(config, model_override=TestModel())

        persona = agent.resolve_persona("coder")
        assert persona is not None
        assert persona.name == "coder"
        assert persona.system_prompt == "Code only"

    def test_resolve_persona_not_found(self) -> None:
        """resolve_persona returns None for unknown names."""
        config = _make_config(agents=[AgentDef(name="coder", description="A coding assistant")])
        agent = ForgeAgent(config, model_override=TestModel())

        assert agent.resolve_persona("unknown") is None

    def test_resolve_persona_empty_agents_list(self) -> None:
        """resolve_persona returns None when no agents are configured."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        assert agent.resolve_persona("anything") is None

    @pytest.mark.anyio
    async def test_run_conversational_with_system_prompt_override(self) -> None:
        """Persona system_prompt override creates a new agent with that prompt."""
        config = _make_config(system_prompt="Default prompt")
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_conversational(
            "Hello!",
            system_prompt_override="Custom persona prompt",
        )
        assert isinstance(result, ForgeRunResult)
        assert isinstance(result.output, str)

    @pytest.mark.anyio
    async def test_run_structured_with_system_prompt_override(self) -> None:
        """Persona system_prompt override works for structured runs."""
        config = _make_config(system_prompt="Default prompt")
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_structured(
            "Do something",
            system_prompt_override="Custom persona prompt",
        )
        assert isinstance(result, ForgeRunResult)

    @pytest.mark.anyio
    async def test_run_conversational_no_override_uses_default(self) -> None:
        """When no overrides are given, the default cached agent is used."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        default_agent = agent._agent
        result = await agent.run_conversational("Hello!")
        assert isinstance(result, ForgeRunResult)
        # Default agent should not have been replaced.
        assert agent._agent is default_agent

    @pytest.mark.anyio
    async def test_run_conversational_stream_with_override(self) -> None:
        """Streaming with persona overrides works correctly."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_conversational(
            "Hello!",
            stream=True,
            system_prompt_override="Stream persona prompt",
        )
        assert hasattr(result, "__aiter__")
        chunks: list[str] = []
        async for chunk in result:
            chunks.append(chunk)
        assert len(chunks) > 0


class TestForgeAgentMaxTurns:
    """Tests for max_turns_override support in ForgeAgent methods."""

    @pytest.mark.anyio
    async def test_run_conversational_with_max_turns_override(self) -> None:
        """run_conversational passes usage_limits to PydanticAI agent.run."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from pydantic_ai.usage import UsageLimits

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)
        await agent.initialize()

        mock_result = MagicMock()
        mock_result.output = "Hello!"
        mock_result.all_messages.return_value = []
        mock_run = AsyncMock(return_value=mock_result)

        assert agent._agent is not None
        with patch.object(agent._agent, "run", mock_run):
            result = await agent.run_conversational(
                "Hello!",
                max_turns_override=5,
            )

        assert isinstance(result, ForgeRunResult)
        usage_limits = mock_run.call_args.kwargs.get("usage_limits")
        assert isinstance(usage_limits, UsageLimits)
        assert usage_limits.request_limit == 5

    @pytest.mark.anyio
    async def test_run_structured_with_max_turns_override(self) -> None:
        """run_structured passes usage_limits to PydanticAI agent.run."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from pydantic_ai.usage import UsageLimits

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)
        await agent.initialize()

        mock_result = MagicMock()
        mock_result.output = {"result": "done"}
        mock_result.all_messages.return_value = []
        mock_run = AsyncMock(return_value=mock_result)

        assert agent._agent is not None
        with patch.object(agent._agent, "run", mock_run):
            result = await agent.run_structured(
                "Do something",
                max_turns_override=3,
            )

        assert isinstance(result, ForgeRunResult)
        usage_limits = mock_run.call_args.kwargs.get("usage_limits")
        assert isinstance(usage_limits, UsageLimits)
        assert usage_limits.request_limit == 3

    @pytest.mark.anyio
    async def test_run_conversational_without_max_turns_uses_default(
        self,
    ) -> None:
        """Without max_turns_override, no usage_limits kwarg is passed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)
        await agent.initialize()

        mock_result = MagicMock()
        mock_result.output = "Hello!"
        mock_result.all_messages.return_value = []
        mock_run = AsyncMock(return_value=mock_result)

        assert agent._agent is not None
        with patch.object(agent._agent, "run", mock_run):
            result = await agent.run_conversational("Hello!")

        assert isinstance(result, ForgeRunResult)
        call_kwargs = mock_run.call_args.kwargs
        assert "usage_limits" not in call_kwargs

    @pytest.mark.anyio
    async def test_run_structured_without_max_turns_uses_default(
        self,
    ) -> None:
        """Without max_turns_override, no usage_limits kwarg is passed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)
        await agent.initialize()

        mock_result = MagicMock()
        mock_result.output = {"result": "done"}
        mock_result.all_messages.return_value = []
        mock_run = AsyncMock(return_value=mock_result)

        assert agent._agent is not None
        with patch.object(agent._agent, "run", mock_run):
            result = await agent.run_structured("Do something")

        assert isinstance(result, ForgeRunResult)
        call_kwargs = mock_run.call_args.kwargs
        assert "usage_limits" not in call_kwargs


class TestAgentNotInitializedErrors:
    """Assert statements replaced with RuntimeError for uninitialized agent."""

    @pytest.mark.anyio
    async def test_run_conversational_without_init_raises_runtime_error(
        self,
    ) -> None:
        """Calling run_conversational when init fails to set _agent raises RuntimeError."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        # Stub initialize so it completes without setting _agent.
        with patch.object(agent, "initialize", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = None
            with pytest.raises(RuntimeError, match="not initialized"):
                await agent.run_conversational("Hello!")

    @pytest.mark.anyio
    async def test_run_structured_without_init_raises_runtime_error(
        self,
    ) -> None:
        """Calling run_structured when init fails to set _agent raises RuntimeError."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        # Stub initialize so it completes without setting _agent.
        with patch.object(agent, "initialize", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = None
            with pytest.raises(RuntimeError, match="not initialized"):
                await agent.run_structured("Do something")


class TestModelSettingsWiring:
    """Tests that LLMRouter.model_settings are passed to PydanticAI Agent."""

    @pytest.mark.anyio
    async def test_create_agent_passes_model_settings(self) -> None:
        """model_settings from LLMRouter are forwarded to PydanticAI Agent."""
        from unittest.mock import MagicMock

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)

        # Patch the LLM router to return specific model_settings.
        agent._llm_router = MagicMock()
        agent._llm_router.model_settings = {
            "temperature": 0.7,
            "max_tokens": 1000,
        }
        agent._llm_router.model_name = "test-model"
        agent._llm_router.system_prompt = None

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent()

        call_kwargs = mock_agent_cls.call_args.kwargs
        assert "model_settings" in call_kwargs
        assert call_kwargs["model_settings"]["temperature"] == 0.7
        assert call_kwargs["model_settings"]["max_tokens"] == 1000

    @pytest.mark.anyio
    async def test_create_agent_skips_none_model_settings(self) -> None:
        """When model_settings values are all None, Agent gets no model_settings."""
        from unittest.mock import MagicMock

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)

        # Router returns settings where all passthrough values are None.
        agent._llm_router = MagicMock()
        agent._llm_router.model_settings = {
            "temperature": None,
            "max_tokens": None,
        }
        agent._llm_router.model_name = "test-model"
        agent._llm_router.system_prompt = None

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent()

        call_kwargs = mock_agent_cls.call_args.kwargs
        # None values should be filtered out, so no model_settings kwarg.
        assert "model_settings" not in call_kwargs

    @pytest.mark.anyio
    async def test_create_agent_skips_empty_model_settings(self) -> None:
        """When model_settings is empty dict, Agent gets no model_settings."""
        from unittest.mock import MagicMock

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)

        agent._llm_router = MagicMock()
        agent._llm_router.model_settings = {}
        agent._llm_router.model_name = "test-model"
        agent._llm_router.system_prompt = None

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent()

        call_kwargs = mock_agent_cls.call_args.kwargs
        assert "model_settings" not in call_kwargs

    @pytest.mark.anyio
    async def test_model_settings_passes_all_known_keys(self) -> None:
        """All known keys (temperature, max_tokens, api_base) are forwarded."""
        from unittest.mock import MagicMock

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)

        # Simulate sidecar mode: router includes api_base alongside
        # temperature and max_tokens.
        agent._llm_router = MagicMock()
        agent._llm_router.model_settings = {
            "temperature": 0.5,
            "max_tokens": 2048,
            "api_base": "http://localhost:4000",
        }
        agent._llm_router.model_name = "openai/gpt-4o"
        agent._llm_router.system_prompt = None

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent()

        call_kwargs = mock_agent_cls.call_args.kwargs
        assert "model_settings" in call_kwargs
        settings = call_kwargs["model_settings"]
        # temperature, max_tokens, and api_base are all passthrough keys.
        assert settings["temperature"] == 0.5
        assert settings["max_tokens"] == 2048
        assert settings["api_base"] == "http://localhost:4000"

    @pytest.mark.anyio
    async def test_model_settings_wired_through_run_conversational(
        self,
    ) -> None:
        """run_conversational uses an agent created with model_settings."""
        from unittest.mock import MagicMock

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)

        agent._llm_router = MagicMock()
        agent._llm_router.model_settings = {
            "temperature": 0.9,
            "max_tokens": 512,
        }
        agent._llm_router.model_name = "test-model"
        agent._llm_router.system_prompt = "Be helpful"

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_run_result = MagicMock()
            mock_run_result.output = "Hi there"
            mock_run_result.all_messages.return_value = []

            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value=mock_run_result)
            mock_agent_cls.return_value = mock_instance

            result = await agent.run_conversational("Hello!")

        assert isinstance(result, ForgeRunResult)
        call_kwargs = mock_agent_cls.call_args.kwargs
        assert "model_settings" in call_kwargs
        assert call_kwargs["model_settings"]["temperature"] == 0.9
        assert call_kwargs["model_settings"]["max_tokens"] == 512


class TestToolFiltering:
    """Tests for tool_names_filter support in _create_agent."""

    @pytest.mark.anyio
    async def test_create_agent_with_tool_filter(self) -> None:
        """When tool_names_filter is provided, only matching tools are passed."""
        from unittest.mock import MagicMock

        config = _make_config(
            manual_tools=[
                ManualTool(
                    name="search",
                    description="Search the web",
                    parameters=[
                        ParameterDef(name="query", type=ParamType.STRING),
                    ],
                    api=ManualToolAPI(
                        url="https://api.example.com/search",
                        method=HTTPMethod.GET,
                    ),
                ),
                ManualTool(
                    name="calc",
                    description="Calculate math",
                    parameters=[
                        ParameterDef(name="expr", type=ParamType.STRING),
                    ],
                    api=ManualToolAPI(
                        url="https://api.example.com/calc",
                        method=HTTPMethod.GET,
                    ),
                ),
                ManualTool(
                    name="weather",
                    description="Get weather info",
                    parameters=[
                        ParameterDef(name="city", type=ParamType.STRING),
                    ],
                    api=ManualToolAPI(
                        url="https://api.example.com/weather",
                        method=HTTPMethod.GET,
                    ),
                ),
            ]
        )
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        # Registry should have all 3 tools.
        assert agent.registry.tool_count == 3

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent(tool_names_filter=["search", "calc"])

        call_kwargs = mock_agent_cls.call_args.kwargs
        tools_passed = call_kwargs["tools"]
        tool_names = [t.name for t in tools_passed]
        assert sorted(tool_names) == ["calc", "search"]

    @pytest.mark.anyio
    async def test_create_agent_without_tool_filter(self) -> None:
        """When no filter is given, all registry tools are passed."""
        from unittest.mock import MagicMock

        config = _make_config(
            manual_tools=[
                ManualTool(
                    name="search",
                    description="Search",
                    parameters=[],
                    api=ManualToolAPI(
                        url="https://api.example.com/search",
                        method=HTTPMethod.GET,
                    ),
                ),
                ManualTool(
                    name="calc",
                    description="Calculate",
                    parameters=[],
                    api=ManualToolAPI(
                        url="https://api.example.com/calc",
                        method=HTTPMethod.GET,
                    ),
                ),
            ]
        )
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        assert agent.registry.tool_count == 2

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent()

        call_kwargs = mock_agent_cls.call_args.kwargs
        tools_passed = call_kwargs["tools"]
        assert len(tools_passed) == 2

    @pytest.mark.anyio
    async def test_create_agent_empty_tool_filter(self) -> None:
        """An empty filter list means no filtering — use all tools."""
        from unittest.mock import MagicMock

        config = _make_config(
            manual_tools=[
                ManualTool(
                    name="search",
                    description="Search",
                    parameters=[],
                    api=ManualToolAPI(
                        url="https://api.example.com/search",
                        method=HTTPMethod.GET,
                    ),
                ),
                ManualTool(
                    name="calc",
                    description="Calculate",
                    parameters=[],
                    api=ManualToolAPI(
                        url="https://api.example.com/calc",
                        method=HTTPMethod.GET,
                    ),
                ),
            ]
        )
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent(tool_names_filter=[])

        call_kwargs = mock_agent_cls.call_args.kwargs
        tools_passed = call_kwargs["tools"]
        assert len(tools_passed) == 2

    @pytest.mark.anyio
    async def test_tool_filter_with_unknown_names(self) -> None:
        """Filtering with names that match no tools results in empty list."""
        from unittest.mock import MagicMock

        config = _make_config(
            manual_tools=[
                ManualTool(
                    name="search",
                    description="Search",
                    parameters=[],
                    api=ManualToolAPI(
                        url="https://api.example.com/search",
                        method=HTTPMethod.GET,
                    ),
                ),
            ]
        )
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        with patch(
            "forge_agent.agent.core.PydanticAIAgent",
        ) as mock_agent_cls:
            mock_instance = MagicMock()
            mock_agent_cls.return_value = mock_instance
            agent._create_agent(
                tool_names_filter=["nonexistent", "also_missing"],
            )

        call_kwargs = mock_agent_cls.call_args.kwargs
        tools_passed = call_kwargs["tools"]
        assert len(tools_passed) == 0
