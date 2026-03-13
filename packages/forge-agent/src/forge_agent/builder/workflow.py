"""Workflow tool builder for Forge Agent.

Creates composite tools that execute steps sequentially, with support
for data binding between steps via output_as and template references.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Any

from forge_config.schema import ParamType, Workflow, WorkflowStep
from pydantic_ai.tools import Tool

# Mapping from ParamType enum to Python annotation types.
_PARAM_TYPE_MAP: dict[ParamType, type] = {
    ParamType.STRING: str,
    ParamType.INTEGER: int,
    ParamType.NUMBER: float,
    ParamType.BOOLEAN: bool,
    ParamType.ARRAY: list,
    ParamType.OBJECT: dict,
}

# Type alias for a step executor function.
StepExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


class WorkflowBuilder:
    """Build a composite PydanticAI tool from a Workflow configuration.

    A workflow tool executes multiple steps sequentially, passing data
    between steps using the output_as binding mechanism. Each step
    invokes a named tool with resolved parameters.

    Args:
        workflow: The Workflow configuration.
        tool_executor: An async callable that executes a named tool
            with given parameters. Signature: (tool_name, params) -> result.
            If not provided, a default executor that raises RuntimeError
            is used to prevent silent stub execution.
    """

    def __init__(
        self,
        workflow: Workflow,
        tool_executor: StepExecutor | None = None,
    ) -> None:
        self._workflow = workflow
        self._tool_executor = tool_executor or _default_executor

    def build(self) -> Tool[None]:
        """Build a PydanticAI Tool from the workflow configuration.

        Returns:
            A PydanticAI Tool wrapping the sequential workflow execution.
        """
        workflow = self._workflow
        executor = self._tool_executor

        # Build function signature from workflow parameters.
        params: list[inspect.Parameter] = []
        for pdef in workflow.parameters:
            annotation = _PARAM_TYPE_MAP.get(pdef.type, str)
            default = inspect.Parameter.empty if pdef.required else pdef.default
            params.append(
                inspect.Parameter(
                    pdef.name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=annotation,
                )
            )

        sig = inspect.Signature(parameters=params)

        # Build __annotations__ dict for get_type_hints() compatibility.
        annotations: dict[str, type] = {"return": dict}
        for pdef in workflow.parameters:
            annotations[pdef.name] = _PARAM_TYPE_MAP.get(pdef.type, str)

        async def workflow_func(**kwargs: Any) -> dict[str, Any]:
            return await _execute_workflow(workflow.steps, kwargs, executor)

        workflow_func.__signature__ = sig  # type: ignore[attr-defined]
        workflow_func.__name__ = workflow.name
        workflow_func.__qualname__ = workflow.name
        workflow_func.__doc__ = workflow.description
        workflow_func.__annotations__ = annotations

        return Tool(workflow_func, name=workflow.name)


def _resolve_template_value(value: Any, context: dict[str, Any]) -> Any:
    """Resolve template references like {{step_name.field}} in a value.

    Args:
        value: A string, dict, list, or primitive that may contain references.
        context: The accumulated step outputs and input parameters.

    Returns:
        The resolved value.
    """
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            ref = match.group(1).strip()
            parts = ref.split(".")
            current: Any = context
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return match.group(0)
            return str(current)

        return re.sub(r"\{\{(\s*[\w.]+\s*)\}\}", replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_template_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_template_value(item, context) for item in value]
    return value


def _evaluate_condition(condition: str, context: dict[str, Any]) -> bool:
    """Evaluate a simple condition expression against the workflow context.

    Supports basic truthy checks by resolving the condition as a template
    reference. If the resolved value is truthy, returns True.

    Args:
        condition: A condition expression (e.g., "step1.success").
        context: The accumulated workflow context.

    Returns:
        True if the condition is met.
    """
    resolved = _resolve_template_value("{{" + condition + "}}", context)
    # If not resolved (still contains {{), treat as false.
    if isinstance(resolved, str) and "{{" in resolved:
        return False
    # Truthy check.
    if isinstance(resolved, str):
        return resolved.lower() not in ("", "false", "0", "none")
    return bool(resolved)


async def _execute_workflow(
    steps: list[WorkflowStep],
    input_params: dict[str, Any],
    executor: StepExecutor,
) -> dict[str, Any]:
    """Execute workflow steps sequentially with data binding.

    Args:
        steps: The ordered list of workflow steps.
        input_params: The initial input parameters.
        executor: The function to call for each step's tool.

    Returns:
        Dict containing all step outputs and a 'result' key with the
        final step's output.
    """
    context: dict[str, Any] = dict(input_params)
    results: dict[str, Any] = {}
    last_result: Any = None

    for step in steps:
        # Check condition if specified.
        if step.condition and not _evaluate_condition(step.condition, context):
            continue

        # Resolve parameter templates against current context.
        resolved_params = _resolve_template_value(step.params, context)

        # Execute the step.
        result = await executor(step.tool, resolved_params)
        last_result = result

        # Store result under output_as name if specified.
        if step.output_as:
            context[step.output_as] = result
            results[step.output_as] = result

    results["result"] = last_result
    return results


async def _default_executor(tool_name: str, params: dict[str, Any]) -> Any:
    """Default executor that raises an error when no real executor is wired.

    This should never be reached in production. If it is, it means a
    WorkflowBuilder was created without a tool_executor, which would
    cause workflow steps to silently return fake data.

    Args:
        tool_name: The name of the tool to execute.
        params: The parameters for the tool.

    Raises:
        RuntimeError: Always, to prevent silent stub execution.
    """
    msg = (
        f"No tool_executor provided for workflow step '{tool_name}'. "
        "WorkflowBuilder requires a real tool_executor to invoke tools. "
        "The default stub executor is not intended for production use."
    )
    raise RuntimeError(msg)
