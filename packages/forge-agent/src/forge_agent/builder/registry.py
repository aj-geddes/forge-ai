"""Tool surface registry for Forge Agent.

Maintains the current set of tools and supports atomic swap of the
entire tool surface when configuration changes are detected. Uses
asyncio.Lock for thread safety and forge-config versioning for
change detection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from forge_config.schema import ForgeConfig
from forge_config.secret_resolver import SecretResolver
from forge_config.versioning import compute_surface_version
from pydantic_ai.tools import Tool

from forge_agent.agent.peers import PeerCaller
from forge_agent.builder.manual import ManualToolBuilder
from forge_agent.builder.openapi import OpenAPIToolBuilder
from forge_agent.builder.workflow import StepExecutor, WorkflowBuilder

logger = logging.getLogger(__name__)


class ToolSurfaceRegistry:
    """Registry that maintains the current set of PydanticAI tools.

    Supports atomic swap of the tool surface: builds a new complete set
    of tools from config, then swaps them in atomically. Version tracking
    via content hashing prevents unnecessary rebuilds.
    """

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._tools: list[Tool[None]] = []
        self._version: str = ""
        self._lock = asyncio.Lock()
        self._secret_resolver = secret_resolver

    @property
    def tools(self) -> list[Tool[None]]:
        """The current set of registered tools."""
        return list(self._tools)

    @property
    def version(self) -> str:
        """The current surface version hash."""
        return self._version

    @property
    def tool_count(self) -> int:
        """Number of currently registered tools."""
        return len(self._tools)

    async def build_and_swap(self, config: ForgeConfig) -> bool:
        """Build a new tool surface from config and swap it in atomically.

        If the config version matches the current version, this is a no-op.

        Args:
            config: The ForgeConfig to build tools from.

        Returns:
            True if the surface was swapped, False if no change detected.
        """
        new_version = compute_surface_version(config)

        async with self._lock:
            if new_version == self._version:
                return False

            new_tools = await self._build_tools(config)
            self._tools = new_tools
            self._version = new_version
            return True

    async def force_swap(self, tools: list[Tool[None]], version: str) -> None:
        """Force-swap the tool surface with an explicit set of tools.

        Args:
            tools: The new tool set.
            version: The version string to associate.
        """
        async with self._lock:
            self._tools = list(tools)
            self._version = version

    async def clear(self) -> None:
        """Remove all tools from the registry."""
        async with self._lock:
            self._tools = []
            self._version = ""

    async def _build_tools(self, config: ForgeConfig) -> list[Tool[None]]:
        """Build all tools from a ForgeConfig.

        Args:
            config: The configuration to build from.

        Returns:
            Complete list of built tools.
        """
        tools: list[Tool[None]] = []
        resolver = self._secret_resolver

        # Build OpenAPI tools (async since specs may be fetched remotely).
        for source in config.tools.openapi_sources:
            openapi_builder = OpenAPIToolBuilder(
                source,
                secret_resolver=resolver,
            )
            tools.extend(await openapi_builder.build())

        # Build manual tools.
        for manual in config.tools.manual_tools:
            manual_builder = ManualToolBuilder(
                manual,
                secret_resolver=resolver,
            )
            tools.append(manual_builder.build())

        # Build workflow tools with a real executor that can look up
        # and invoke any tool in this registry by name. Uses late
        # binding via closure over the `tools` list so that workflows
        # can reference tools built in the same _build_tools() call.
        executor = _make_registry_executor(tools)
        for workflow in config.tools.workflows:
            workflow_builder = WorkflowBuilder(
                workflow,
                tool_executor=executor,
            )
            tools.append(workflow_builder.build())

        # Build peer agent tools.
        if config.agents.peers:
            peer_caller = PeerCaller(
                peers=config.agents.peers,
                caller_id=config.metadata.name,
            )
            tools.extend(peer_caller.build_tools())

        return tools


def _make_registry_executor(
    tools: list[Tool[None]],
) -> StepExecutor:
    """Create a tool executor that looks up and invokes tools by name.

    Returns an async callable matching the StepExecutor protocol:
    ``async def executor(tool_name: str, params: dict) -> Any``.

    Uses late binding via closure over the ``tools`` list reference,
    so that tools appended after this function returns (e.g. other
    workflows or peer tools built later in ``_build_tools``) are
    still visible at execution time.

    Args:
        tools: The mutable list of tools being built. The executor
            captures the reference (not a snapshot), so tools added
            after creation are available at invocation time.

    Returns:
        An async callable suitable for ``WorkflowBuilder.tool_executor``.
    """

    async def executor(tool_name: str, params: dict[str, Any]) -> Any:
        """Invoke a registered tool by name with the given params.

        Args:
            tool_name: The name of the tool to invoke.
            params: Keyword arguments to pass to the tool function.

        Returns:
            The result of the tool invocation.

        Raises:
            RuntimeError: If no tool with the given name is found.
        """
        for tool in tools:
            if tool.name == tool_name:
                # All tools in this registry use takes_ctx=False,
                # so the function accepts only keyword args.
                return await tool.function(**params)  # type: ignore[call-arg]

        available = [t.name for t in tools]
        msg = f"Workflow step references unknown tool '{tool_name}'. Available tools: {available}"
        raise RuntimeError(msg)

    return executor
