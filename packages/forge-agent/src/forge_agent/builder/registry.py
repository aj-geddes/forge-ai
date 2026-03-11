"""Tool surface registry for Forge Agent.

Maintains the current set of tools and supports atomic swap of the
entire tool surface when configuration changes are detected. Uses
asyncio.Lock for thread safety and forge-config versioning for
change detection.
"""

from __future__ import annotations

import asyncio

from forge_config.schema import ForgeConfig
from forge_config.versioning import compute_surface_version
from pydantic_ai.tools import Tool

from forge_agent.agent.peers import PeerCaller
from forge_agent.builder.manual import ManualToolBuilder
from forge_agent.builder.openapi import OpenAPIToolBuilder
from forge_agent.builder.workflow import WorkflowBuilder


class ToolSurfaceRegistry:
    """Registry that maintains the current set of PydanticAI tools.

    Supports atomic swap of the tool surface: builds a new complete set
    of tools from config, then swaps them in atomically. Version tracking
    via content hashing prevents unnecessary rebuilds.
    """

    def __init__(self) -> None:
        self._tools: list[Tool[None]] = []
        self._version: str = ""
        self._lock = asyncio.Lock()

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

        # Build OpenAPI tools (async since specs may be fetched remotely).
        for source in config.tools.openapi_sources:
            openapi_builder = OpenAPIToolBuilder(source)
            tools.extend(await openapi_builder.build())

        # Build manual tools.
        for manual in config.tools.manual_tools:
            manual_builder = ManualToolBuilder(manual)
            tools.append(manual_builder.build())

        # Build workflow tools.
        for workflow in config.tools.workflows:
            workflow_builder = WorkflowBuilder(workflow)
            tools.append(workflow_builder.build())

        # Build peer agent tools.
        if config.agents.peers:
            peer_caller = PeerCaller(
                peers=config.agents.peers,
                caller_id=config.metadata.name,
            )
            tools.extend(peer_caller.build_tools())

        return tools
