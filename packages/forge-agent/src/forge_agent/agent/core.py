"""Core ForgeAgent implementation.

Main entry point for the Forge Agent system. Takes ForgeConfig, builds
tools, creates a PydanticAI Agent, and provides conversational and
structured run methods.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from forge_config.schema import AgentDef, ForgeConfig
from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from forge_agent.agent.context import ConversationContext
from forge_agent.agent.llm import LLMRouter
from forge_agent.builder.registry import ToolSurfaceRegistry


@dataclass
class ForgeRunResult:
    """Wraps agent output with metadata about the run.

    Attributes:
        output: The agent's output (string for conversational, dict/BaseModel for structured).
        tools_used: List of tool names invoked during the run.
        model_name: The LLM model identifier used for the run.
    """

    output: Any
    tools_used: list[str] = field(default_factory=list)
    model_name: str | None = None


def _extract_tools_used(messages: list[ModelMessage]) -> list[str]:
    """Extract unique tool names from PydanticAI message history, preserving call order."""
    seen: set[str] = set()
    tools: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name not in seen:
                    seen.add(part.tool_name)
                    tools.append(part.tool_name)
    return tools


def _extract_model_name(messages: list[ModelMessage]) -> str | None:
    """Extract the model name from the last ModelResponse in the message history."""
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse) and msg.model_name:
            return msg.model_name
    return None


def _build_usage_limits(max_turns: int | None) -> UsageLimits | None:
    """Convert a max_turns integer into PydanticAI ``UsageLimits``.

    Args:
        max_turns: Maximum number of LLM request turns, or None to
            leave unlimited (PydanticAI defaults apply).

    Returns:
        A ``UsageLimits`` with ``request_limit`` set, or None when
        no limit is requested.
    """
    if max_turns is None:
        return None
    return UsageLimits(request_limit=max_turns)


class ForgeAgent:
    """Main Forge Agent orchestrator.

    Takes a ForgeConfig, builds the tool surface, configures the LLM
    via LiteLLM, and provides methods for running conversational and
    structured agent interactions via PydanticAI.

    Args:
        config: The ForgeConfig defining tools, LLM, and agent settings.
        model_override: Optional PydanticAI Model to use instead of the
            configured LLM (useful for testing with TestModel).
    """

    def __init__(
        self,
        config: ForgeConfig,
        model_override: Model | None = None,
    ) -> None:
        self._config = config
        self._llm_router = LLMRouter(config.llm)
        self._registry = ToolSurfaceRegistry()
        self._context = ConversationContext()
        self._model_override = model_override
        self._agent: PydanticAIAgent[None] | None = None

    @property
    def registry(self) -> ToolSurfaceRegistry:
        """The tool surface registry."""
        return self._registry

    @property
    def context(self) -> ConversationContext:
        """The conversation context manager."""
        return self._context

    @property
    def llm_router(self) -> LLMRouter:
        """The LLM router."""
        return self._llm_router

    def resolve_persona(self, name: str) -> AgentDef | None:
        """Look up a named agent persona from the config's agents list.

        Args:
            name: The persona name to look up (case-sensitive).

        Returns:
            The matching AgentDef, or None if not found.
        """
        for agent_def in self._config.agents.agents:
            if agent_def.name == name:
                return agent_def
        return None

    async def initialize(self) -> None:
        """Initialize the agent by building the tool surface and creating the PydanticAI Agent.

        Must be called before run_conversational or run_structured.
        """
        await self._registry.build_and_swap(self._config)
        self._agent = self._create_agent()

    # Keys from LLMRouter.model_settings that should be forwarded to
    # PydanticAI ModelSettings.  ``temperature`` and ``max_tokens`` are
    # first-class ModelSettings fields; ``api_base`` is an extra key that
    # PydanticAI forwards to the underlying LiteLLM provider for
    # sidecar/external proxy modes.
    _PASSTHROUGH_SETTINGS: frozenset[str] = frozenset({"temperature", "max_tokens", "api_base"})

    def _build_model_settings(self) -> ModelSettings | None:
        """Build a PydanticAI ``ModelSettings`` from the LLM router config.

        Only includes settings that are not ``None`` so that PydanticAI
        defaults are preserved for unset values.

        Returns:
            A ``ModelSettings`` dict, or ``None`` when no settings apply.
        """
        raw = self._llm_router.model_settings
        filtered: dict[str, Any] = {
            k: v for k, v in raw.items() if k in self._PASSTHROUGH_SETTINGS and v is not None
        }
        if not filtered:
            return None
        # ModelSettings is a TypedDict; cast via dict unpacking.
        return ModelSettings(**filtered)  # type: ignore[typeddict-item, no-any-return]

    @staticmethod
    def _filter_tools(
        tools: list[Any],
        tool_names_filter: list[str] | None,
    ) -> list[Any]:
        """Filter tools to only those whose name is in the allow-list.

        Args:
            tools: Full list of PydanticAI Tool objects.
            tool_names_filter: Tool names to keep. When None or
                empty, all tools are returned unfiltered.

        Returns:
            Filtered (or original) tool list.
        """
        if not tool_names_filter:
            return tools
        allowed = set(tool_names_filter)
        return [t for t in tools if t.name in allowed]

    def _create_agent(
        self,
        output_type: type | None = None,
        *,
        system_prompt_override: str | None = None,
        model_name_override: str | None = None,
        tool_names_filter: list[str] | None = None,
    ) -> PydanticAIAgent[None]:
        """Create a PydanticAI Agent with current tools and config.

        Args:
            output_type: Optional output type for structured responses.
            system_prompt_override: If set, replaces the default system prompt.
            model_name_override: If set, replaces the default model name.
            tool_names_filter: If set and non-empty, only include tools
                whose name appears in this list.

        Returns:
            A configured PydanticAI Agent.
        """
        model: Any = self._model_override
        if model is None:
            model = model_name_override or self._llm_router.model_name

        system_prompt = system_prompt_override or self._llm_router.system_prompt or ""
        tools = self._filter_tools(self._registry.tools, tool_names_filter)

        kwargs: dict[str, Any] = {
            "model": model,
            "tools": tools,
            "defer_model_check": True,
        }

        if system_prompt:
            kwargs["system_prompt"] = system_prompt

        if output_type is not None:
            kwargs["output_type"] = output_type

        pydantic_model_settings = self._build_model_settings()
        if pydantic_model_settings:
            kwargs["model_settings"] = pydantic_model_settings

        return PydanticAIAgent(**kwargs)

    async def run_conversational(
        self,
        message: str,
        session_id: str | None = None,
        stream: bool = False,
        *,
        system_prompt_override: str | None = None,
        model_name_override: str | None = None,
        max_turns_override: int | None = None,
        tool_names_filter: list[str] | None = None,
    ) -> ForgeRunResult | AsyncIterator[str]:
        """Run a conversational interaction with the agent.

        Args:
            message: The user message.
            session_id: Optional session ID for context continuity.
            stream: If True, return an async iterator of text chunks.
            system_prompt_override: If set, replaces the default system prompt.
            model_name_override: If set, replaces the default model name.
            max_turns_override: If set, limits the number of LLM request
                turns via PydanticAI's ``usage_limits``.
            tool_names_filter: If set and non-empty, only include tools
                whose name appears in this list.

        Returns:
            A ForgeRunResult containing the response and metadata,
            or an async iterator of chunks if stream=True.
        """
        has_overrides = (
            system_prompt_override is not None
            or model_name_override is not None
            or bool(tool_names_filter)
        )

        if has_overrides:
            agent = self._create_agent(
                system_prompt_override=system_prompt_override,
                model_name_override=model_name_override,
                tool_names_filter=tool_names_filter,
            )
        else:
            if self._agent is None:
                await self.initialize()
            if self._agent is None:
                raise RuntimeError("Agent not initialized; call initialize() first")
            agent = self._agent

        message_history = None
        if session_id:
            message_history = self._context.get_messages(session_id) or None

        usage_limits = _build_usage_limits(max_turns_override)

        if stream:
            return self._make_stream(
                message,
                session_id,
                message_history,
                agent_override=agent,
                usage_limits=usage_limits,
            )

        run_kwargs: dict[str, Any] = {
            "message_history": message_history,
        }
        if usage_limits is not None:
            run_kwargs["usage_limits"] = usage_limits

        result = await agent.run(message, **run_kwargs)

        all_msgs = list(result.all_messages())

        # Store messages in context.
        if session_id:
            self._context.add_messages(session_id, all_msgs)

        return ForgeRunResult(
            output=result.output,
            tools_used=_extract_tools_used(all_msgs),
            model_name=_extract_model_name(all_msgs),
        )

    def _make_stream(
        self,
        message: str,
        session_id: str | None,
        message_history: list[Any] | None,
        *,
        agent_override: PydanticAIAgent[None] | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> AsyncIterator[str]:
        """Create an async iterator that streams the agent response.

        Args:
            message: The user message.
            session_id: Optional session ID.
            message_history: Previous messages for context.
            agent_override: If set, use this agent instead of the default.
            usage_limits: Optional limits on model request count.

        Returns:
            Async iterator yielding text chunks.
        """
        agent = agent_override or self._agent
        context = self._context
        if agent is None:
            raise RuntimeError("Agent not initialized; call initialize() first")

        stream_kwargs: dict[str, Any] = {
            "message_history": message_history,
        }
        if usage_limits is not None:
            stream_kwargs["usage_limits"] = usage_limits

        async def _generate() -> AsyncIterator[str]:
            async with agent.run_stream(message, **stream_kwargs) as stream:
                async for text in stream.stream_output(debounce_by=None):
                    yield text

                if session_id:
                    all_msgs = stream.all_messages()
                    context.add_messages(session_id, list(all_msgs))

        return _generate()

    @staticmethod
    def _merge_tool_filters(
        persona_tools: list[str] | None,
        tool_hints: list[str] | None,
    ) -> list[str] | None:
        """Merge persona-level and request-level tool filters.

        When both are provided, returns their intersection so that only
        tools allowed by the persona *and* requested by the caller are
        included. When only one is provided, returns that one. When
        neither is provided, returns None (no filtering).

        Args:
            persona_tools: Tool names allowed by the agent persona.
            tool_hints: Tool names requested by the API caller.

        Returns:
            Merged filter list, or None when no filtering applies.
        """
        has_persona = bool(persona_tools)
        has_hints = bool(tool_hints)
        if has_persona and has_hints:
            return list(set(persona_tools) & set(tool_hints))  # type: ignore[arg-type]
        if has_persona:
            return persona_tools
        if has_hints:
            return tool_hints
        return None

    async def run_structured(
        self,
        intent: str,
        params: dict[str, Any] | None = None,
        output_schema: type[BaseModel] | None = None,
        *,
        system_prompt_override: str | None = None,
        model_name_override: str | None = None,
        max_turns_override: int | None = None,
        tool_names_filter: list[str] | None = None,
        tool_hints_filter: list[str] | None = None,
    ) -> ForgeRunResult:
        """Run a structured interaction that returns typed output.

        Args:
            intent: Description of what the agent should produce.
            params: Optional parameters to include in the prompt.
            output_schema: A Pydantic BaseModel class for the output type.
            system_prompt_override: If set, replaces the default system prompt.
            model_name_override: If set, replaces the default model name.
            max_turns_override: If set, limits the number of LLM request
                turns via PydanticAI's ``usage_limits``.
            tool_names_filter: If set and non-empty, only include tools
                whose name appears in this list (persona-level).
            tool_hints_filter: If set and non-empty, request-level tool
                filter from the API caller. Intersected with
                ``tool_names_filter`` when both are present.

        Returns:
            A ForgeRunResult containing the output and run metadata.
        """
        effective_filter = self._merge_tool_filters(
            tool_names_filter,
            tool_hints_filter,
        )
        has_overrides = (
            system_prompt_override is not None
            or model_name_override is not None
            or bool(effective_filter)
        )

        if has_overrides:
            agent = self._create_agent(
                output_type=output_schema,
                system_prompt_override=system_prompt_override,
                model_name_override=model_name_override,
                tool_names_filter=effective_filter,
            )
        else:
            if self._agent is None:
                await self.initialize()
            if self._agent is None:
                raise RuntimeError("Agent not initialized; call initialize() first")
            agent = self._agent

        prompt = intent
        if params:
            param_str = ", ".join(f"{k}={v}" for k, v in params.items())
            prompt = f"{intent} (parameters: {param_str})"

        usage_limits = _build_usage_limits(max_turns_override)
        run_kwargs: dict[str, Any] = {}
        if usage_limits is not None:
            run_kwargs["usage_limits"] = usage_limits

        if output_schema is not None:
            structured_result = await agent.run(prompt, output_type=output_schema, **run_kwargs)
            output: Any = structured_result.output
            all_msgs = list(structured_result.all_messages())
        else:
            text_result = await agent.run(prompt, **run_kwargs)
            output = {"result": text_result.output}
            all_msgs = list(text_result.all_messages())

        return ForgeRunResult(
            output=output,
            tools_used=_extract_tools_used(all_msgs),
            model_name=_extract_model_name(all_msgs),
        )
