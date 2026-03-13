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

    def _create_agent(
        self,
        output_type: type | None = None,
        *,
        system_prompt_override: str | None = None,
        model_name_override: str | None = None,
    ) -> PydanticAIAgent[None]:
        """Create a PydanticAI Agent with current tools and config.

        Args:
            output_type: Optional output type for structured responses.
            system_prompt_override: If set, replaces the default system prompt.
            model_name_override: If set, replaces the default model name.

        Returns:
            A configured PydanticAI Agent.
        """
        model: Any = self._model_override
        if model is None:
            model = model_name_override or self._llm_router.model_name

        system_prompt = system_prompt_override or self._llm_router.system_prompt or ""
        tools = self._registry.tools

        kwargs: dict[str, Any] = {
            "model": model,
            "tools": tools,
            "defer_model_check": True,
        }

        if system_prompt:
            kwargs["system_prompt"] = system_prompt

        if output_type is not None:
            kwargs["output_type"] = output_type

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

        Returns:
            A ForgeRunResult containing the response and metadata,
            or an async iterator of chunks if stream=True.
        """
        has_overrides = system_prompt_override is not None or model_name_override is not None

        if has_overrides:
            agent = self._create_agent(
                system_prompt_override=system_prompt_override,
                model_name_override=model_name_override,
            )
        else:
            if self._agent is None:
                await self.initialize()
            assert self._agent is not None
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
        assert agent is not None

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

    async def run_structured(
        self,
        intent: str,
        params: dict[str, Any] | None = None,
        output_schema: type[BaseModel] | None = None,
        *,
        system_prompt_override: str | None = None,
        model_name_override: str | None = None,
        max_turns_override: int | None = None,
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

        Returns:
            A ForgeRunResult containing the output and run metadata.
        """
        has_overrides = system_prompt_override is not None or model_name_override is not None

        if has_overrides:
            agent = self._create_agent(
                output_type=output_schema,
                system_prompt_override=system_prompt_override,
                model_name_override=model_name_override,
            )
        else:
            if self._agent is None:
                await self.initialize()
            assert self._agent is not None
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
