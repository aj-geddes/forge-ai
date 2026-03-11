"""Core ForgeAgent implementation.

Main entry point for the Forge Agent system. Takes ForgeConfig, builds
tools, creates a PydanticAI Agent, and provides conversational and
structured run methods.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from forge_config.schema import ForgeConfig
from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai.models import Model

from forge_agent.agent.context import ConversationContext
from forge_agent.agent.llm import LLMRouter
from forge_agent.builder.registry import ToolSurfaceRegistry


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

    async def initialize(self) -> None:
        """Initialize the agent by building the tool surface and creating the PydanticAI Agent.

        Must be called before run_conversational or run_structured.
        """
        await self._registry.build_and_swap(self._config)
        self._agent = self._create_agent()

    def _create_agent(self, output_type: type | None = None) -> PydanticAIAgent[None]:
        """Create a PydanticAI Agent with current tools and config.

        Args:
            output_type: Optional output type for structured responses.

        Returns:
            A configured PydanticAI Agent.
        """
        model: Any = self._model_override
        if model is None:
            model = self._llm_router.model_name

        system_prompt = self._llm_router.system_prompt or ""
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
    ) -> str | AsyncIterator[str]:
        """Run a conversational interaction with the agent.

        Args:
            message: The user message.
            session_id: Optional session ID for context continuity.
            stream: If True, return an async iterator of text chunks.

        Returns:
            The agent's string response, or an async iterator of chunks
            if stream=True.
        """
        if self._agent is None:
            await self.initialize()

        assert self._agent is not None

        message_history = None
        if session_id:
            message_history = self._context.get_messages(session_id) or None

        if stream:
            return self._make_stream(message, session_id, message_history)

        result = await self._agent.run(
            message,
            message_history=message_history,
        )

        # Store messages in context.
        if session_id:
            self._context.add_messages(session_id, list(result.all_messages()))

        return result.output

    def _make_stream(
        self,
        message: str,
        session_id: str | None,
        message_history: list[Any] | None,
    ) -> AsyncIterator[str]:
        """Create an async iterator that streams the agent response.

        Args:
            message: The user message.
            session_id: Optional session ID.
            message_history: Previous messages for context.

        Returns:
            Async iterator yielding text chunks.
        """
        agent = self._agent
        context = self._context
        assert agent is not None

        async def _generate() -> AsyncIterator[str]:
            async with agent.run_stream(
                message,
                message_history=message_history,
            ) as stream:
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
    ) -> BaseModel | dict[str, Any]:
        """Run a structured interaction that returns typed output.

        Args:
            intent: Description of what the agent should produce.
            params: Optional parameters to include in the prompt.
            output_schema: A Pydantic BaseModel class for the output type.

        Returns:
            An instance of output_schema if provided, otherwise a dict.
        """
        if self._agent is None:
            await self.initialize()

        assert self._agent is not None

        prompt = intent
        if params:
            param_str = ", ".join(f"{k}={v}" for k, v in params.items())
            prompt = f"{intent} (parameters: {param_str})"

        if output_schema is not None:
            structured_result = await self._agent.run(prompt, output_type=output_schema)
            return structured_result.output
        else:
            text_result = await self._agent.run(prompt)
            return {"result": text_result.output}
