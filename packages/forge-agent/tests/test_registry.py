"""Tests for ToolSurfaceRegistry."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from forge_agent.active.gate import ApprovalStatus, ApprovalStore, ToolGate
from forge_agent.builder.registry import ToolSurfaceRegistry
from forge_config.schema import (
    ForgeConfig,
    HTTPMethod,
    ManualTool,
    ManualToolAPI,
    OpenAPISource,
    ToolsConfig,
    Workflow,
    WorkflowStep,
)
from forge_config.versioning import compute_surface_version
from pydantic_ai.tools import Tool

# Minimal OpenAPI spec for registry integration tests.
_MINIMAL_SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test", "version": "1.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/users": {
            "get": {
                "operationId": "list_users",
                "summary": "List users",
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "operationId": "create_user",
                "summary": "Create user",
                "responses": {"201": {"description": "Created"}},
            },
        }
    },
}


def _make_config_with_manual_tools(tools: list[ManualTool]) -> ForgeConfig:
    """Create a ForgeConfig with manual tools."""
    return ForgeConfig(tools=ToolsConfig(manual_tools=tools))


def _make_manual_tool(
    name: str,
    url: str = "https://api.example.com",
    *,
    requires_approval: bool = False,
) -> ManualTool:
    """Create a simple ManualTool."""
    return ManualTool(
        name=name,
        description=f"Tool {name}",
        api=ManualToolAPI(url=url, method=HTTPMethod.GET),
        requires_approval=requires_approval,
    )


class TestToolSurfaceRegistry:
    """Tests for ToolSurfaceRegistry."""

    @pytest.mark.anyio
    async def test_initial_state(self) -> None:
        registry = ToolSurfaceRegistry()
        assert registry.tools == []
        assert registry.version == ""
        assert registry.tool_count == 0

    @pytest.mark.anyio
    async def test_build_and_swap_creates_tools(self) -> None:
        registry = ToolSurfaceRegistry()
        config = _make_config_with_manual_tools(
            [
                _make_manual_tool("tool_a"),
                _make_manual_tool("tool_b"),
            ]
        )

        swapped = await registry.build_and_swap(config)
        assert swapped is True
        assert registry.tool_count == 2

        tool_names = [t.name for t in registry.tools]
        assert "tool_a" in tool_names
        assert "tool_b" in tool_names

    @pytest.mark.anyio
    async def test_build_and_swap_sets_version(self) -> None:
        registry = ToolSurfaceRegistry()
        config = _make_config_with_manual_tools([_make_manual_tool("tool_a")])

        await registry.build_and_swap(config)
        expected_version = compute_surface_version(config)
        assert registry.version == expected_version

    @pytest.mark.anyio
    async def test_build_and_swap_no_change_is_noop(self) -> None:
        registry = ToolSurfaceRegistry()
        config = _make_config_with_manual_tools([_make_manual_tool("tool_a")])

        first_swap = await registry.build_and_swap(config)
        assert first_swap is True

        # Same config again should be a no-op.
        second_swap = await registry.build_and_swap(config)
        assert second_swap is False

    @pytest.mark.anyio
    async def test_build_and_swap_detects_changes(self) -> None:
        registry = ToolSurfaceRegistry()

        config1 = _make_config_with_manual_tools([_make_manual_tool("tool_a")])
        await registry.build_and_swap(config1)
        assert registry.tool_count == 1

        config2 = _make_config_with_manual_tools(
            [
                _make_manual_tool("tool_a"),
                _make_manual_tool("tool_b"),
            ]
        )
        swapped = await registry.build_and_swap(config2)
        assert swapped is True
        assert registry.tool_count == 2

    @pytest.mark.anyio
    async def test_atomic_swap_replaces_all_tools(self) -> None:
        """The swap should be atomic: old tools are fully replaced."""
        registry = ToolSurfaceRegistry()

        config1 = _make_config_with_manual_tools(
            [
                _make_manual_tool("old_tool_1"),
                _make_manual_tool("old_tool_2"),
            ]
        )
        await registry.build_and_swap(config1)

        config2 = _make_config_with_manual_tools(
            [
                _make_manual_tool("new_tool"),
            ]
        )
        await registry.build_and_swap(config2)

        tool_names = [t.name for t in registry.tools]
        assert "old_tool_1" not in tool_names
        assert "old_tool_2" not in tool_names
        assert "new_tool" in tool_names

    @pytest.mark.anyio
    async def test_force_swap(self) -> None:
        registry = ToolSurfaceRegistry()

        async def dummy(**kwargs: Any) -> str:
            return "dummy"

        tools = [Tool(dummy, name="forced_tool")]
        await registry.force_swap(tools, "v1.0")

        assert registry.tool_count == 1
        assert registry.tools[0].name == "forced_tool"
        assert registry.version == "v1.0"

    @pytest.mark.anyio
    async def test_clear(self) -> None:
        registry = ToolSurfaceRegistry()
        config = _make_config_with_manual_tools([_make_manual_tool("tool_a")])
        await registry.build_and_swap(config)
        assert registry.tool_count == 1

        await registry.clear()
        assert registry.tool_count == 0
        assert registry.version == ""

    @pytest.mark.anyio
    async def test_builds_openapi_tools(self) -> None:
        registry = ToolSurfaceRegistry()
        config = ForgeConfig(
            tools=ToolsConfig(
                openapi_sources=[
                    OpenAPISource(
                        name="example",
                        url="https://api.example.com/openapi.json",
                    )
                ]
            )
        )

        with patch(
            "forge_agent.builder.openapi.OpenAPIToolBuilder._fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_MINIMAL_SPEC,
        ):
            await registry.build_and_swap(config)
        assert registry.tool_count == 2

    @pytest.mark.anyio
    async def test_builds_workflow_tools(self) -> None:
        registry = ToolSurfaceRegistry()
        config = ForgeConfig(
            tools=ToolsConfig(
                workflows=[
                    Workflow(
                        name="my_workflow",
                        description="A workflow",
                        steps=[
                            WorkflowStep(tool="step_a"),
                            WorkflowStep(tool="step_b"),
                        ],
                    )
                ]
            )
        )

        await registry.build_and_swap(config)
        assert registry.tool_count == 1
        assert registry.tools[0].name == "my_workflow"

    @pytest.mark.anyio
    async def test_tools_property_returns_copy(self) -> None:
        """The tools property should return a copy, not the internal list."""
        registry = ToolSurfaceRegistry()
        config = _make_config_with_manual_tools([_make_manual_tool("tool_a")])
        await registry.build_and_swap(config)

        tools = registry.tools
        tools.clear()
        assert registry.tool_count == 1  # Internal list unchanged

    @pytest.mark.anyio
    async def test_registry_passes_executor_to_workflow_builder(
        self,
    ) -> None:
        """_build_tools should pass a non-None tool_executor to WorkflowBuilder."""
        registry = ToolSurfaceRegistry()
        config = ForgeConfig(
            tools=ToolsConfig(
                workflows=[
                    Workflow(
                        name="wf_with_executor",
                        description="Workflow that needs an executor",
                        steps=[
                            WorkflowStep(tool="step_a"),
                        ],
                    )
                ]
            )
        )

        with patch(
            "forge_agent.builder.registry.WorkflowBuilder",
            wraps=None,
        ) as mock_wb_cls:
            # Set up the mock to return a Tool-like object.
            mock_builder = MagicMock()
            mock_tool = MagicMock(spec=Tool)
            mock_tool.name = "wf_with_executor"
            mock_builder.build.return_value = mock_tool
            mock_wb_cls.return_value = mock_builder

            await registry.build_and_swap(config)

            mock_wb_cls.assert_called_once()
            call_kwargs = mock_wb_cls.call_args
            # The WorkflowBuilder should receive a tool_executor argument
            # that is not None (i.e., a real executor, not the default).
            if call_kwargs.kwargs:
                executor = call_kwargs.kwargs.get("tool_executor")
            else:
                # Positional: WorkflowBuilder(workflow, tool_executor)
                executor = call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
            assert executor is not None, (
                "_build_tools must pass a non-None tool_executor to WorkflowBuilder"
            )


class TestToolSurfaceRegistryGating:
    """ADR-0005 SS6.2: the registry owns a ToolGate shared by every manual
    tool it builds, so a ``requires_approval: true`` manual tool drafts
    instead of executing through the real tool surface."""

    @pytest.mark.anyio
    async def test_registry_exposes_a_tool_gate_by_default(self) -> None:
        registry = ToolSurfaceRegistry()
        assert isinstance(registry.tool_gate, ToolGate)

    @pytest.mark.anyio
    async def test_registry_accepts_an_explicit_tool_gate(self) -> None:
        gate = ToolGate(ApprovalStore())
        registry = ToolSurfaceRegistry(tool_gate=gate)
        assert registry.tool_gate is gate

    @pytest.mark.anyio
    async def test_gated_manual_tool_drafts_through_the_built_surface(self) -> None:
        gate = ToolGate(ApprovalStore())
        registry = ToolSurfaceRegistry(tool_gate=gate)
        config = _make_config_with_manual_tools(
            [_make_manual_tool("publish_post", requires_approval=True)]
        )

        await registry.build_and_swap(config)
        tool = next(t for t in registry.tools if t.name == "publish_post")

        result = await tool.function()

        assert result["status"] == "drafted"
        approval = await gate.get_approval(result["approval_id"])
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.tool_name == "publish_post"

    @pytest.mark.anyio
    async def test_ungated_manual_tool_is_unaffected_by_the_shared_gate(self) -> None:
        registry = ToolSurfaceRegistry()
        config = _make_config_with_manual_tools([_make_manual_tool("plain_tool")])

        await registry.build_and_swap(config)
        tool = next(t for t in registry.tools if t.name == "plain_tool")

        # Not gated: build() never wraps it, so calling it attempts the
        # real HTTP call (and fails against the fake example.com host --
        # what matters here is that it is NOT a "drafted" dict).
        with pytest.raises(Exception):  # noqa: B017, PT011 -- real network call, any failure is fine
            await tool.function()

    @pytest.mark.anyio
    async def test_gated_openapi_tool_drafts_through_the_built_surface(self) -> None:
        """ADR-0005 SS6.2, security review finding #1: the registry must
        thread its shared ToolGate into OpenAPIToolBuilder exactly as it
        does for ManualToolBuilder, so a gated OpenAPI-sourced operation
        (e.g. a Postiz ``POST /publish``) drafts instead of executing."""
        gate = ToolGate(ApprovalStore())
        registry = ToolSurfaceRegistry(tool_gate=gate)
        config = ForgeConfig(
            tools=ToolsConfig(
                openapi_sources=[
                    OpenAPISource(
                        name="example",
                        url="https://api.example.com/openapi.json",
                        approval_operations=["create_user"],
                    )
                ]
            )
        )

        with patch(
            "forge_agent.builder.openapi.OpenAPIToolBuilder._fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_MINIMAL_SPEC,
        ):
            await registry.build_and_swap(config)

        tool = next(t for t in registry.tools if t.name == "create_user")
        result = await tool.function()

        assert result["status"] == "drafted"
        approval = await gate.get_approval(result["approval_id"])
        assert approval is not None
        assert approval.tool_name == "create_user"

    @pytest.mark.anyio
    async def test_ungated_openapi_tool_is_unaffected_by_the_shared_gate(self) -> None:
        gate = ToolGate(ApprovalStore())
        registry = ToolSurfaceRegistry(tool_gate=gate)
        config = ForgeConfig(
            tools=ToolsConfig(
                openapi_sources=[
                    OpenAPISource(
                        name="example",
                        url="https://api.example.com/openapi.json",
                    )
                ]
            )
        )

        with patch(
            "forge_agent.builder.openapi.OpenAPIToolBuilder._fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_MINIMAL_SPEC,
        ):
            await registry.build_and_swap(config)

        tool = next(t for t in registry.tools if t.name == "list_users")

        # Not gated: calling it attempts the real HTTP call against the
        # fake example.com host -- what matters is it is NOT a drafted dict.
        with pytest.raises(Exception):  # noqa: B017, PT011 -- real network call, any failure is fine
            await tool.function()
