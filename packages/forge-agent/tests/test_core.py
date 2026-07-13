"""Tests for ForgeAgent core."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from forge_agent.agent.core import ForgeAgent, ForgeRunResult, ToolCallRecord
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import (
    AgentDef,
    AgentsConfig,
    AuthConfig,
    AuthType,
    ForgeConfig,
    HTTPMethod,
    LLMConfig,
    ManualTool,
    ManualToolAPI,
    ParameterDef,
    ParamType,
    SecretRef,
    SecretSource,
    ToolsConfig,
)
from forge_config.secret_resolver import SecretResolver
from pydantic import BaseModel
from pydantic_ai.models.test import TestModel


class FakeSecretResolver:
    """A fake SecretResolver returning predefined values, for testing."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}

    def resolve(self, ref: SecretRef) -> str:
        if ref.name not in self._secrets:
            msg = f"Secret '{ref.name}' not found"
            raise SecretResolutionError(msg)
        return self._secrets[ref.name]


_check: SecretResolver = FakeSecretResolver()


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


def _make_api_key_manual_tool(env_var_name: str) -> ManualTool:
    """Create a ManualTool whose API call requires api_key auth from an env secret."""
    return ManualTool(
        name="get_weather",
        description="Get current weather for a location",
        api=_build_api_key_auth_api(env_var_name),
    )


def _build_api_key_auth_api(env_var_name: str) -> ManualToolAPI:
    """Build a ManualToolAPI requiring api_key auth resolved from an env var."""
    return ManualToolAPI(
        url="https://api.weatherapi.com/v1/current.json",
        method=HTTPMethod.GET,
        auth=_build_api_key_auth_config(env_var_name),
    )


def _build_api_key_auth_config(env_var_name: str) -> AuthConfig:
    """Build an AuthConfig for api_key auth resolved from an env var."""
    return AuthConfig(
        type=AuthType.API_KEY,
        token=SecretRef(source=SecretSource.ENV, name=env_var_name),
    )


def _make_k8s_secret_api_key_manual_tool(secret_name: str, secret_key: str) -> ManualTool:
    """Create a ManualTool whose API call requires api_key auth from a k8s_secret."""
    return ManualTool(
        name="get_weather",
        description="Get current weather for a location",
        api=ManualToolAPI(
            url="https://api.weatherapi.com/v1/current.json",
            method=HTTPMethod.GET,
            auth=AuthConfig(
                type=AuthType.API_KEY,
                token=SecretRef(source=SecretSource.K8S_SECRET, name=secret_name, key=secret_key),
            ),
        ),
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


class TestForgeAgentSecretResolverWiring:
    """Tests that ForgeAgent threads a SecretResolver through to the registry."""

    @pytest.mark.anyio
    async def test_initialize_resolves_api_key_auth_via_default_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A manual tool with api_key auth builds when no resolver is passed explicitly.

        ForgeAgent must fall back to a working default SecretResolver (e.g. one
        that resolves env-sourced secrets) so that gateway callers who only pass
        a ForgeConfig -- ``ForgeAgent(config)`` -- still get a working agent for
        configs shaped like ``forge.yaml.example`` (manual tool, api_key auth,
        env secret source).
        """
        monkeypatch.setenv("WEATHER_API_KEY", "shh-its-a-secret")
        config = _make_config(
            manual_tools=[_make_api_key_manual_tool("WEATHER_API_KEY")],
        )
        agent = ForgeAgent(config, model_override=TestModel())

        await agent.initialize()

        assert agent.registry.tool_count == 1

    @pytest.mark.anyio
    async def test_initialize_still_raises_when_env_secret_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default resolver is real -- a genuinely missing secret still fails."""
        monkeypatch.delenv("UNSET_WEATHER_API_KEY", raising=False)
        config = _make_config(
            manual_tools=[_make_api_key_manual_tool("UNSET_WEATHER_API_KEY")],
        )
        agent = ForgeAgent(config, model_override=TestModel())

        with pytest.raises(SecretResolutionError, match="not set"):
            await agent.initialize()

    @pytest.mark.anyio
    async def test_explicit_secret_resolver_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit ``secret_resolver`` kwarg is used instead of the default.

        Even when the underlying env var is unset, a caller-supplied resolver
        (e.g. wired to Vault or another backend) must still be honored.
        """
        monkeypatch.delenv("VAULT_ONLY_KEY", raising=False)
        fake_resolver = FakeSecretResolver({"VAULT_ONLY_KEY": "vault-value"})
        config = _make_config(
            manual_tools=[_make_api_key_manual_tool("VAULT_ONLY_KEY")],
        )
        agent = ForgeAgent(
            config,
            model_override=TestModel(),
            secret_resolver=fake_resolver,
        )

        await agent.initialize()

        assert agent.registry.tool_count == 1


class TestForgeAgentK8sSecretResolverWiring:
    """Tests that the default SecretResolver also resolves ``k8s_secret``
    refs (WS-6): a working ``K8sSecretResolver`` (forge_security) exists
    but was never registered into the resolver ForgeAgent builds by
    default, so a spec-compliant ``{source: k8s_secret, ...}`` ref always
    failed with "No resolver registered for source". These tests assert
    the registration exists and that env-only configs are unaffected.
    """

    @pytest.mark.anyio
    async def test_initialize_resolves_k8s_secret_via_default_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A manual tool with a k8s_secret auth ref builds with no resolver
        passed explicitly, using a fake K8sSecretResolver in place of real
        filesystem/volume access (mocks the k8s secret fetch)."""
        import forge_agent.agent.core as core_module

        class FakeK8sSecretResolver:
            """Stand-in for forge_security.secrets.K8sSecretResolver."""

            def resolve(self, ref: SecretRef) -> str:
                assert ref.source == SecretSource.K8S_SECRET
                return f"k8s-value:{ref.name}/{ref.key}"

        monkeypatch.setattr(core_module, "K8sSecretResolver", FakeK8sSecretResolver)

        config = _make_config(
            manual_tools=[_make_k8s_secret_api_key_manual_tool("forge-secrets", "weather-api-key")],
        )
        agent = ForgeAgent(config, model_override=TestModel())

        await agent.initialize()

        assert agent.registry.tool_count == 1

    @pytest.mark.anyio
    async def test_env_only_config_still_resolves_as_before_no_regression(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Registering a k8s_secret resolver alongside env must not change
        env-sourced resolution: a config using only ``source: env`` refs
        resolves exactly as it did before the k8s_secret wiring fix."""
        monkeypatch.setenv("WEATHER_API_KEY", "shh-its-a-secret")
        config = _make_config(
            manual_tools=[_make_api_key_manual_tool("WEATHER_API_KEY")],
        )
        agent = ForgeAgent(config, model_override=TestModel())

        await agent.initialize()

        assert agent.registry.tool_count == 1

    @pytest.mark.anyio
    async def test_k8s_secret_resolution_degrades_gracefully_without_k8s_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outside a cluster (no service account / mounted volume), the real
        K8sSecretResolver is registered but simply fails to find the secret
        file -- a clear SecretResolutionError, not a crash and not the old
        "No resolver registered for source" error. Env resolution for other
        tools in the same run is completely unaffected."""
        monkeypatch.delenv("MISSING_K8S_SECRET_DIR_MARKER", raising=False)
        config = _make_config(
            manual_tools=[
                _make_k8s_secret_api_key_manual_tool(
                    "definitely-not-a-real-secret", "definitely-not-a-real-key"
                )
            ],
        )
        agent = ForgeAgent(config, model_override=TestModel())

        with pytest.raises(SecretResolutionError) as exc_info:
            await agent.initialize()

        # Must be the K8sSecretResolver's own graceful error (file not
        # found), never the CompositeSecretResolver's "no resolver
        # registered" error that indicated the bug.
        assert "No resolver registered" not in str(exc_info.value)

    @pytest.mark.anyio
    async def test_explicit_secret_resolver_still_overrides_default_for_k8s(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit ``secret_resolver`` kwarg is honored for k8s_secret
        refs too, same as for env refs."""
        fake_resolver = FakeSecretResolver({"forge-secrets": "vault-value"})
        config = _make_config(
            manual_tools=[_make_k8s_secret_api_key_manual_tool("forge-secrets", "vault-secret")],
        )
        agent = ForgeAgent(
            config,
            model_override=TestModel(),
            secret_resolver=fake_resolver,
        )

        await agent.initialize()

        assert agent.registry.tool_count == 1


class TestBuildDefaultSecretResolver:
    """Direct unit tests for ``build_default_secret_resolver``."""

    def test_resolves_env_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from forge_agent.agent.core import build_default_secret_resolver

        monkeypatch.setenv("SOME_ENV_SECRET", "value-123")
        resolver = build_default_secret_resolver()

        value = resolver.resolve(SecretRef(source=SecretSource.ENV, name="SOME_ENV_SECRET"))

        assert value == "value-123"

    def test_k8s_secret_source_is_registered_not_unregistered(self) -> None:
        """Resolving a k8s_secret ref must reach the K8sSecretResolver (and
        fail with its own file-not-found error) rather than the composite
        resolver's "no resolver registered for source" error -- this is
        the exact bug WS-6 fixes."""
        from forge_agent.agent.core import build_default_secret_resolver

        resolver = build_default_secret_resolver()

        with pytest.raises(SecretResolutionError) as exc_info:
            resolver.resolve(
                SecretRef(
                    source=SecretSource.K8S_SECRET,
                    name="definitely-not-a-real-secret",
                    key="definitely-not-a-real-key",
                )
            )

        assert "No resolver registered" not in str(exc_info.value)


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


class TestForgeAgentToolCallRecords:
    """Tests that ForgeRunResult exposes structured per-tool-call records
    (name/arguments/result), not just the flat ``tools_used`` name list --
    this is what the gateway/UI need to render tool call details (WS-9).
    """

    @pytest.mark.anyio
    async def test_run_conversational_returns_tool_call_records(self) -> None:
        """A run that invokes a tool exposes name/arguments/result on tool_calls."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()
        assert agent._agent is not None

        @agent._agent.tool_plain
        def get_weather(city: str) -> str:
            return f"sunny in {city}"

        result = await agent.run_conversational("What's the weather in SF?")

        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert isinstance(call, ToolCallRecord)
        assert call.name == "get_weather"
        assert call.arguments == {"city": "a"}
        assert call.result == "sunny in a"

    @pytest.mark.anyio
    async def test_run_conversational_no_tools_called_yields_empty_tool_calls(self) -> None:
        """When no tool is invoked, tool_calls is an empty list."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())

        result = await agent.run_conversational("Hello!")

        assert result.tool_calls == []

    @pytest.mark.anyio
    async def test_run_structured_returns_tool_call_records(self) -> None:
        """run_structured also exposes tool_calls on its ForgeRunResult."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()
        assert agent._agent is not None

        @agent._agent.tool_plain
        def get_weather(city: str) -> str:
            return f"sunny in {city}"

        result = await agent.run_structured("What's the weather?")

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].result == "sunny in a"


class TestForgeAgentStreamingDeltas:
    """Tests that stream=True yields incremental text DELTAS, not the
    cumulative snapshots PydanticAI's ``stream_output`` produces -- joining
    all yielded chunks must equal the final message exactly once, with no
    duplication (docs/developer/api-reference.md's SSE frame example reads
    as deltas, e.g. "The current " -> "weather in SF ").
    """

    @pytest.mark.anyio
    async def test_stream_chunks_concatenate_to_final_message_without_duplication(
        self,
    ) -> None:
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()

        non_stream_result = await agent.run_conversational("Hello!")
        expected_text = non_stream_result.output
        assert isinstance(expected_text, str)
        assert len(expected_text) > 0

        stream = await agent.run_conversational("Hello!", stream=True)
        text_chunks = [item async for item in stream if isinstance(item, str)]

        assert len(text_chunks) > 1  # TestModel streams in multiple increments
        assert "".join(text_chunks) == expected_text

    @pytest.mark.anyio
    async def test_stream_includes_tool_call_record_before_text_deltas(self) -> None:
        """Tool calls resolve before the final text streams; the stream
        surfaces them as ToolCallRecord items distinct from text chunks."""
        config = _make_config()
        agent = ForgeAgent(config, model_override=TestModel())
        await agent.initialize()
        assert agent._agent is not None

        @agent._agent.tool_plain
        def get_weather(city: str) -> str:
            return f"sunny in {city}"

        stream = await agent.run_conversational("weather?", stream=True)
        items = [item async for item in stream]

        tool_records = [i for i in items if isinstance(i, ToolCallRecord)]
        text_chunks = [i for i in items if isinstance(i, str)]

        assert len(tool_records) == 1
        assert tool_records[0].name == "get_weather"
        assert tool_records[0].arguments == {"city": "a"}
        assert tool_records[0].result == "sunny in a"
        assert "".join(text_chunks) == '{"get_weather":"sunny in a"}'


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
        """All known keys (temperature, max_tokens) are forwarded.

        ``api_base`` is intentionally NOT a known ModelSettings passthrough
        key: PydanticAI's ModelSettings has no such field, so stuffing it in
        here would be a silent no-op. Endpoint routing for sidecar/external
        modes is instead applied via LLMRouter.resolve_model, which builds a
        Model/Provider with the real ``base_url`` -- see test_llm.py.
        """
        from unittest.mock import MagicMock

        config = _make_config()
        test_model = TestModel()
        agent = ForgeAgent(config, model_override=test_model)

        agent._llm_router = MagicMock()
        agent._llm_router.model_settings = {
            "temperature": 0.5,
            "max_tokens": 2048,
        }
        agent._llm_router.resolve_model.return_value = "openai:gpt-4o"
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
        assert settings["temperature"] == 0.5
        assert settings["max_tokens"] == 2048
        assert "api_base" not in settings

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
