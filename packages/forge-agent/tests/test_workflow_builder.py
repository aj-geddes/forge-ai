"""Tests for WorkflowBuilder."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock

import pytest
from forge_agent.builder.workflow import WorkflowBuilder, _default_executor
from forge_config.schema import (
    ParameterDef,
    ParamType,
    Workflow,
    WorkflowStep,
)


def _make_workflow(
    name: str = "test_workflow",
    description: str = "A test workflow",
    parameters: list[ParameterDef] | None = None,
    steps: list[WorkflowStep] | None = None,
) -> Workflow:
    """Helper to create a Workflow config for testing."""
    return Workflow(
        name=name,
        description=description,
        parameters=parameters or [],
        steps=steps or [WorkflowStep(tool="noop")],
    )


class TestWorkflowBuilder:
    """Tests for WorkflowBuilder.build()."""

    def test_build_returns_tool_with_correct_name(self) -> None:
        workflow = _make_workflow(name="deploy_pipeline")
        builder = WorkflowBuilder(workflow)
        tool = builder.build()
        assert tool.name == "deploy_pipeline"

    def test_build_creates_function_with_proper_signature(self) -> None:
        workflow = _make_workflow(
            parameters=[
                ParameterDef(name="env", type=ParamType.STRING, description="Target env"),
                ParameterDef(name="dry_run", type=ParamType.BOOLEAN, required=False, default=False),
            ]
        )
        builder = WorkflowBuilder(workflow)
        tool = builder.build()

        sig = inspect.signature(tool.function)
        params = list(sig.parameters.values())

        assert len(params) == 2
        assert params[0].name == "env"
        assert params[0].annotation is str
        assert params[1].name == "dry_run"
        assert params[1].annotation is bool
        assert params[1].default is False

    @pytest.mark.anyio
    async def test_sequential_step_execution(self) -> None:
        """Steps should execute in order, and results accumulate."""
        call_log: list[str] = []

        async def mock_executor(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            call_log.append(tool_name)
            return {"tool": tool_name, "success": True}

        workflow = _make_workflow(
            steps=[
                WorkflowStep(tool="step_a", output_as="a_result"),
                WorkflowStep(tool="step_b", output_as="b_result"),
                WorkflowStep(tool="step_c"),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=mock_executor)
        tool = builder.build()
        result = await tool.function()

        assert call_log == ["step_a", "step_b", "step_c"]
        assert "a_result" in result
        assert "b_result" in result
        assert result["result"] == {"tool": "step_c", "success": True}

    @pytest.mark.anyio
    async def test_data_binding_between_steps(self) -> None:
        """output_as values should be resolvable in subsequent step params."""
        captured_params: list[dict[str, Any]] = []

        async def mock_executor(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            captured_params.append(params)
            if tool_name == "fetch_user":
                return {"user_id": "u123", "name": "Alice"}
            return {"sent": True}

        workflow = _make_workflow(
            steps=[
                WorkflowStep(tool="fetch_user", output_as="user"),
                WorkflowStep(
                    tool="send_email",
                    params={"to": "{{user.name}}", "user_id": "{{user.user_id}}"},
                ),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=mock_executor)
        tool = builder.build()
        await tool.function()

        assert captured_params[1]["to"] == "Alice"
        assert captured_params[1]["user_id"] == "u123"

    @pytest.mark.anyio
    async def test_conditional_step_skipping(self) -> None:
        """Steps with unmet conditions should be skipped."""
        call_log: list[str] = []

        async def mock_executor(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            call_log.append(tool_name)
            if tool_name == "check":
                return {"proceed": False}
            return {"done": True}

        workflow = _make_workflow(
            steps=[
                WorkflowStep(tool="check", output_as="check_result"),
                WorkflowStep(
                    tool="conditional_step",
                    condition="check_result.proceed",
                ),
                WorkflowStep(tool="always_runs"),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=mock_executor)
        tool = builder.build()
        await tool.function()

        # conditional_step should be skipped because check_result.proceed is False
        assert "check" in call_log
        assert "conditional_step" not in call_log
        assert "always_runs" in call_log

    @pytest.mark.anyio
    async def test_default_executor_raises_error(self) -> None:
        """_default_executor should raise RuntimeError, not return stub data."""
        with pytest.raises(RuntimeError, match="No tool_executor provided"):
            await _default_executor("some_tool", {"key": "value"})

    @pytest.mark.anyio
    async def test_workflow_executor_invokes_registered_tool(self) -> None:
        """A registry-provided executor should invoke the registered tool."""
        mock_tool = AsyncMock(return_value={"result": "success"})

        async def registry_executor(tool_name: str, params: dict[str, Any]) -> Any:
            if tool_name == "my_tool":
                return await mock_tool(**params)
            raise RuntimeError(f"Unknown tool: {tool_name}")

        workflow = _make_workflow(
            steps=[
                WorkflowStep(
                    tool="my_tool",
                    params={"x": 1, "y": 2},
                    output_as="step_result",
                ),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=registry_executor)
        tool = builder.build()
        result = await tool.function()

        mock_tool.assert_awaited_once_with(x=1, y=2)
        assert result["step_result"] == {"result": "success"}
        assert result["result"] == {"result": "success"}

    @pytest.mark.anyio
    async def test_workflow_executor_raises_on_unknown_tool(self) -> None:
        """Executing a step that references an unregistered tool should raise."""

        async def strict_executor(tool_name: str, params: dict[str, Any]) -> Any:
            known_tools = {"known_tool"}
            if tool_name not in known_tools:
                raise RuntimeError(f"Tool '{tool_name}' not found in registry")
            return {"ok": True}

        workflow = _make_workflow(
            steps=[
                WorkflowStep(tool="nonexistent_tool", params={"a": "b"}),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=strict_executor)
        tool = builder.build()

        with pytest.raises(RuntimeError, match="not found in registry"):
            await tool.function()

    @pytest.mark.anyio
    async def test_input_params_available_in_context(self) -> None:
        """Workflow input parameters should be resolvable in step params."""
        captured_params: list[dict[str, Any]] = []

        async def mock_executor(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            captured_params.append(params)
            return {"ok": True}

        workflow = _make_workflow(
            parameters=[
                ParameterDef(name="target", type=ParamType.STRING),
            ],
            steps=[
                WorkflowStep(tool="deploy", params={"env": "{{target}}"}),
            ],
        )

        builder = WorkflowBuilder(workflow, tool_executor=mock_executor)
        tool = builder.build()
        await tool.function(target="production")

        assert captured_params[0]["env"] == "production"
