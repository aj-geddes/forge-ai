"""Tests for WorkflowBuilder."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock

import pytest
from forge_agent.builder.workflow import (
    WorkflowBuilder,
    _build_condition_evaluator,
    _default_executor,
    _evaluate_condition,
)
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


# ---------------------------------------------------------------------------
# Test: _evaluate_condition as a real, sandboxed boolean expression evaluator
# ---------------------------------------------------------------------------
#
# docs/user/configuration.md documents `condition` as: "Python-style
# expression that must evaluate to true for the step to execute (e.g.,
# `"contact.city is not None"`)."


class TestEvaluateConditionDocumentedExample:
    """The documented condition example must actually work."""

    def test_documented_example_true_when_city_set(self) -> None:
        """`contact.city is not None` is True when city is set."""
        context = {"contact": {"city": "Springfield"}}
        assert _evaluate_condition("contact.city is not None", context) is True

    def test_documented_example_false_when_city_none(self) -> None:
        """`contact.city is not None` is False when city is None."""
        context = {"contact": {"city": None}}
        assert _evaluate_condition("contact.city is not None", context) is False

    def test_documented_example_false_when_city_missing(self) -> None:
        """`contact.city is not None` is False when contact has no city key."""
        context = {"contact": {}}
        assert _evaluate_condition("contact.city is not None", context) is False


class TestEvaluateConditionComparisons:
    """Comparison expressions should work against the workflow context."""

    def test_greater_than_true(self) -> None:
        assert _evaluate_condition("count > 0", {"count": 5}) is True

    def test_greater_than_false(self) -> None:
        assert _evaluate_condition("count > 0", {"count": 0}) is False

    def test_equality(self) -> None:
        assert _evaluate_condition("status == 'ready'", {"status": "ready"}) is True
        assert _evaluate_condition("status == 'ready'", {"status": "pending"}) is False

    def test_membership_in(self) -> None:
        assert _evaluate_condition("status in ['ok', 'done']", {"status": "done"}) is True
        assert _evaluate_condition("status in ['ok', 'done']", {"status": "error"}) is False

    def test_boolean_and_or_not(self) -> None:
        context = {"a": True, "b": False}
        assert _evaluate_condition("a and not b", context) is True
        assert _evaluate_condition("a or b", context) is True
        assert _evaluate_condition("not a", context) is False


class TestEvaluateConditionBareDottedRef:
    """Backward compat: a bare dotted ref must still be truthy-checked."""

    def test_bare_dotted_ref_true(self) -> None:
        context = {"check_result": {"proceed": True}}
        assert _evaluate_condition("check_result.proceed", context) is True

    def test_bare_dotted_ref_false(self) -> None:
        context = {"check_result": {"proceed": False}}
        assert _evaluate_condition("check_result.proceed", context) is False

    def test_bare_top_level_name_truthy(self) -> None:
        assert _evaluate_condition("weather", {"weather": {"temp": 70}}) is True
        assert _evaluate_condition("weather", {"weather": {}}) is False


class TestEvaluateConditionSandboxing:
    """Condition evaluation must be sandboxed: no function calls, no dunder access."""

    def test_evaluator_rejects_function_calls(self) -> None:
        """The low-level evaluator raises rather than executing any function call."""
        from simpleeval import FunctionNotDefined

        evaluator = _build_condition_evaluator({"contact": {"city": "Boston"}})
        with pytest.raises(FunctionNotDefined):
            evaluator.eval("len(contact)")

    def test_evaluator_rejects_dunder_attribute_access(self) -> None:
        """The low-level evaluator raises rather than exposing __class__/__builtins__."""
        from simpleeval import FeatureNotAvailable

        evaluator = _build_condition_evaluator({"contact": {"city": "Boston"}})
        with pytest.raises(FeatureNotAvailable):
            evaluator.eval("contact.__class__")

    def test_public_wrapper_fails_closed_on_function_call(self) -> None:
        """A condition attempting a function call is rejected, not executed; step is skipped."""
        assert _evaluate_condition("len(contact)", {"contact": {"city": "Boston"}}) is False

    def test_public_wrapper_fails_closed_on_dunder_access(self) -> None:
        """A condition attempting __builtins__-style access is rejected, not executed."""
        condition = "contact.__class__.__mro__"
        assert _evaluate_condition(condition, {"contact": {"city": "Boston"}}) is False

    def test_injected_shell_command_is_never_executed(self, monkeypatch: Any) -> None:
        """A condition trying to shell out must not run the shell command."""
        calls: list[str] = []
        monkeypatch.setattr("os.system", lambda cmd: calls.append(cmd))

        result = _evaluate_condition("__import__('os').system('touch pwned')", {})

        assert result is False
        assert calls == []


class TestWorkflowConditionIntegration:
    """End-to-end: a workflow step's documented condition gates execution."""

    @pytest.mark.anyio
    async def test_step_runs_when_documented_condition_is_true(self) -> None:
        call_log: list[str] = []

        async def mock_executor(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            call_log.append(tool_name)
            if tool_name == "lookup_contact":
                return {"city": "Springfield"}
            return {"forecast": "sunny"}

        workflow = _make_workflow(
            steps=[
                WorkflowStep(tool="lookup_contact", output_as="contact"),
                WorkflowStep(
                    tool="get_weather",
                    condition="contact.city is not None",
                ),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=mock_executor)
        tool = builder.build()
        await tool.function()

        assert call_log == ["lookup_contact", "get_weather"]

    @pytest.mark.anyio
    async def test_step_skipped_when_documented_condition_is_false(self) -> None:
        call_log: list[str] = []

        async def mock_executor(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            call_log.append(tool_name)
            if tool_name == "lookup_contact":
                return {"city": None}
            return {"forecast": "sunny"}

        workflow = _make_workflow(
            steps=[
                WorkflowStep(tool="lookup_contact", output_as="contact"),
                WorkflowStep(
                    tool="get_weather",
                    condition="contact.city is not None",
                ),
            ]
        )

        builder = WorkflowBuilder(workflow, tool_executor=mock_executor)
        tool = builder.build()
        await tool.function()

        assert call_log == ["lookup_contact"]
