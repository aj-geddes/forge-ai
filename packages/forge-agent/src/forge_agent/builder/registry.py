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

from forge_agent.active.gate import ApprovalStore, ToolGate
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

    def __init__(
        self,
        secret_resolver: SecretResolver | None = None,
        *,
        workload_identity: Any | None = None,
        tool_gate: ToolGate | None = None,
    ) -> None:
        self._tools: list[Tool[None]] = []
        self._version: str = ""
        self._lock = asyncio.Lock()
        self._secret_resolver = secret_resolver
        # ADR-0004 SS6: the workload plane's identity provider, when the
        # agent was built with one, is threaded through to PeerCaller so
        # outbound peer calls use mTLS. ``None`` (the default) preserves
        # the pre-ADR-0004 plain-httpx behavior.
        self._workload_identity = workload_identity
        # ADR-0005 SS6.2: a single ToolGate is shared by every manual tool
        # this registry builds, so gated tools (``ManualTool.requires_approval``)
        # draft-instead-of-execute through the real tool surface. Always
        # present (built with a fresh in-memory ApprovalStore when the
        # caller doesn't supply one) so ManualToolBuilder can always thread
        # it through -- it is simply unused by tools that don't opt in.
        self._tool_gate = tool_gate or ToolGate(ApprovalStore())

    @property
    def tools(self) -> list[Tool[None]]:
        """The current set of registered tools."""
        return list(self._tools)

    @property
    def tool_gate(self) -> ToolGate:
        """The shared ToolGate used to gate approval-required manual tools
        (ADR-0005 SS6.2). Exposed so the gateway's admin approvals routes
        can list/approve/reject requests drafted through this registry."""
        return self._tool_gate

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
        # Every source is built with the shared ToolGate (ADR-0005 SS6.2,
        # security review finding #1) -- mirrors manual tools below; only
        # operations opted into `requires_approval`/`approval_operations`
        # are actually wrapped by it (see OpenAPIToolBuilder._build_tools).
        for source in config.tools.openapi_sources:
            openapi_builder = OpenAPIToolBuilder(
                source,
                secret_resolver=resolver,
                tool_gate=self._tool_gate,
                # ADR-0006: thread the SSRF/egress policy so both the spec fetch
                # and operation calls are guarded and the credential is host-bound.
                egress_policy=config.security.egress,
            )
            tools.extend(await openapi_builder.build())

        # Build manual tools. Every manual tool is built with the shared
        # ToolGate (ADR-0005 SS6.2); only tools with `requires_approval:
        # true` are actually wrapped by it (see ManualToolBuilder.build).
        for manual in config.tools.manual_tools:
            manual_builder = ManualToolBuilder(
                manual,
                secret_resolver=resolver,
                tool_gate=self._tool_gate,
                # ADR-0006: thread the SSRF/egress policy so the manual sink's
                # outbound client is guarded and its credential is host-bound.
                egress_policy=config.security.egress,
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
                identity=self._workload_identity,
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
