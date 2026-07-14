"""Manual tool builder for Forge Agent.

Creates dynamic async Python functions from ManualTool config definitions,
where each function makes HTTP calls via httpx based on ManualToolAPI config.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import httpx
from forge_config.schema import HTTPMethod, ManualTool, ManualToolAPI, ParamType, ResponseMapping
from forge_config.secret_resolver import SecretResolver
from pydantic_ai.tools import Tool

from forge_agent.active.gate import ToolGate
from forge_agent.builder.openapi import _resolve_auth_headers


class ToolResponseError(Exception):
    """Raised when a manual tool's API response indicates a failure.

    Signaled by ``response_mapping.status_field`` resolving to a known
    failure marker, or ``response_mapping.error_path`` resolving to a
    truthy error value. See ``_check_response_for_error`` for the exact
    semantics.
    """

    def __init__(self, message: str, *, response: Any = None) -> None:
        super().__init__(message)
        self.response = response


# Mapping from ParamType enum to Python annotation types.
_PARAM_TYPE_MAP: dict[ParamType, type] = {
    ParamType.STRING: str,
    ParamType.INTEGER: int,
    ParamType.NUMBER: float,
    ParamType.BOOLEAN: bool,
    ParamType.ARRAY: list,
    ParamType.OBJECT: dict,
}

# String values at `status_field` that indicate the call failed, matched
# case-insensitively. A boolean `False` at `status_field` is also treated
# as failure (see `_is_failure_status`).
_FAILURE_STATUS_VALUES = frozenset({"error", "fail", "failed", "failure"})


class ManualToolBuilder:
    """Build PydanticAI tools from manually defined tool configurations.

    Takes a ManualTool config and creates a dynamic async function that
    makes HTTP calls via httpx, with proper function signatures built
    using inspect.Parameter.
    """

    def __init__(
        self,
        tool_config: ManualTool,
        http_client: httpx.AsyncClient | None = None,
        secret_resolver: SecretResolver | None = None,
        tool_gate: ToolGate | None = None,
        *,
        requested_by: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._config = tool_config
        self._http_client = http_client
        self._secret_resolver = secret_resolver
        self._tool_gate = tool_gate
        self._requested_by = requested_by
        self._run_id = run_id

    def build(self) -> Tool[None]:
        """Build a PydanticAI Tool from the manual tool configuration.

        Auth secrets are resolved at build time so that failures
        surface immediately rather than at request time.

        Returns:
            A PydanticAI Tool wrapping an async HTTP-calling function.

        Raises:
            SecretResolutionError: If auth is configured but the secret
                cannot be resolved.
        """
        api_config = self._config.api
        tool_name = self._config.name
        tool_description = self._config.description
        param_defs = self._config.parameters
        http_client = self._http_client

        # Resolve auth headers once at build time (fail-fast).
        auth_headers = _resolve_auth_headers(api_config.auth, self._secret_resolver)

        # Build the parameter list for the function signature.
        params: list[inspect.Parameter] = []
        for pdef in param_defs:
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
        annotations: dict[str, type] = {"return": Any}
        for pdef in param_defs:
            annotations[pdef.name] = _PARAM_TYPE_MAP.get(pdef.type, str)

        async def tool_func(**kwargs: Any) -> Any:
            return await _execute_api_call(api_config, kwargs, auth_headers, http_client)

        # Assign the constructed signature and metadata.
        tool_func.__signature__ = sig  # type: ignore[attr-defined]
        tool_func.__name__ = tool_name
        tool_func.__qualname__ = tool_name
        tool_func.__doc__ = tool_description
        tool_func.__annotations__ = annotations

        final_func: Any = tool_func
        if self._config.requires_approval:
            # ADR-0005 SS6.2: gated tools must never be buildable without a
            # gate to draft/approve them -- fail fast at build time rather
            # than silently executing the real side effect ungated.
            if self._tool_gate is None:
                msg = (
                    f"Manual tool {tool_name!r} has requires_approval=True but no "
                    "ToolGate was supplied to ManualToolBuilder; refusing to build "
                    "it as an ungated tool."
                )
                raise ValueError(msg)
            gated_func = self._tool_gate.wrap(
                tool_name,
                tool_func,
                requested_by=self._requested_by,
                run_id=self._run_id,
            )
            gated_func.__signature__ = sig  # type: ignore[attr-defined]
            gated_func.__name__ = tool_name
            gated_func.__qualname__ = tool_name
            gated_func.__doc__ = tool_description
            gated_func.__annotations__ = annotations
            final_func = gated_func

        return Tool(final_func, name=tool_name)


def _resolve_template_string(template: str, params: dict[str, Any]) -> str:
    """Resolve {{param}} placeholders in a template string.

    Args:
        template: String with {{param}} placeholders.
        params: Parameter values to substitute.

    Returns:
        The resolved string.
    """

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return str(params.get(key, match.group(0)))

    return re.sub(r"\{\{(\s*\w+\s*)\}\}", replacer, template)


def _collect_template_param_names(template: Any) -> set[str]:
    """Recursively collect all {{param}} placeholder names referenced in a template.

    Args:
        template: A dict, list, string, or primitive that may contain
            {{param}} placeholders.

    Returns:
        The set of parameter names referenced anywhere in the template.
    """
    names: set[str] = set()
    if isinstance(template, str):
        names.update(m.group(1).strip() for m in re.finditer(r"\{\{(\s*\w+\s*)\}\}", template))
    elif isinstance(template, dict):
        for value in template.values():
            names.update(_collect_template_param_names(value))
    elif isinstance(template, list):
        for item in template:
            names.update(_collect_template_param_names(item))
    return names


def _resolve_template(template: Any, params: dict[str, Any]) -> Any:
    """Recursively resolve {{param}} placeholders in a template structure.

    Args:
        template: A dict, list, string, or primitive with potential placeholders.
        params: Parameter values to substitute.

    Returns:
        The resolved structure.
    """
    if isinstance(template, str):
        return _resolve_template_string(template, params)
    if isinstance(template, dict):
        return {k: _resolve_template(v, params) for k, v in template.items()}
    if isinstance(template, list):
        return [_resolve_template(item, params) for item in template]
    return template


# HTTP methods for which unconsumed declared parameters are forwarded as
# a query string rather than merged into the request body.
_QUERY_METHODS = frozenset({HTTPMethod.GET.value, HTTPMethod.DELETE.value})


async def _execute_api_call(
    api_config: ManualToolAPI,
    params: dict[str, Any],
    auth_headers: dict[str, str],
    http_client: httpx.AsyncClient | None = None,
) -> Any:
    """Execute an HTTP API call based on ManualToolAPI configuration.

    Any declared parameter that is not consumed by a {{placeholder}} in the
    URL, headers, or body_template is forwarded on the outgoing request:
    as a query string parameter for GET/DELETE, or merged into the JSON
    body for methods that carry a body (POST/PUT/PATCH) when no explicit
    body_template already claims it.

    Args:
        api_config: The API call configuration.
        params: Parameter values from the tool invocation.
        auth_headers: Pre-resolved authentication headers.
        http_client: Optional pre-configured httpx client.

    Returns:
        The parsed JSON response or raw text.
    """
    url = _resolve_template_string(api_config.resolved_url, params)
    consumed = _collect_template_param_names(api_config.resolved_url)

    # Start with pre-resolved auth headers, then layer on config headers.
    headers = dict(auth_headers)
    for k, v in api_config.headers.items():
        headers[k] = _resolve_template_string(v, params)
        consumed |= _collect_template_param_names(v)

    body = None
    if api_config.body_template is not None:
        body = _resolve_template(api_config.body_template, params)
        consumed |= _collect_template_param_names(api_config.body_template)

    method = api_config.method.value

    # Forward any declared parameters not already consumed by a template.
    remaining = {k: v for k, v in params.items() if k not in consumed}
    query: dict[str, Any] | None = None
    if remaining:
        if method in _QUERY_METHODS:
            query = remaining
        elif body is None:
            # No explicit body_template: build the body from the
            # remaining declared parameters.
            body = remaining
        # else: body_template already explicitly defines the body shape;
        # leave any additional declared params unforwarded.

    client = http_client or httpx.AsyncClient()
    should_close = http_client is None

    try:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=query,
            json=body,
            timeout=api_config.timeout,
        )
        response.raise_for_status()

        try:
            result = response.json()
        except Exception:
            result = response.text

        return _apply_response_mapping(result, api_config)

    finally:
        if should_close:
            await client.aclose()


def _resolve_dot_path(obj: Any, path: str) -> Any:
    """Resolve a dot-notation path within a (possibly nested) structure.

    Args:
        obj: The object to traverse.
        path: A dot-separated path, e.g. "condition.text".

    Returns:
        The resolved value, or None if any segment of the path is missing.
    """
    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _is_failure_status(value: Any) -> bool:
    """Return True if a `status_field` value is a known failure marker.

    A boolean ``False`` is a failure marker (common for `{"success": false}`
    style envelopes). A string is a failure marker if, case-insensitively,
    it is one of "error", "fail", "failed", "failure" (common for
    `{"status": "error"}` style envelopes). Anything else -- including the
    field being absent (``None``) -- is treated as success, so tools whose
    APIs don't use this convention are unaffected.

    Args:
        value: The value resolved at `response_mapping.status_field`.

    Returns:
        True if the value indicates failure.
    """
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in _FAILURE_STATUS_VALUES
    return False


def _check_response_for_error(result: Any, mapping: ResponseMapping) -> None:
    """Raise ToolResponseError if the response indicates failure.

    Semantics:
      - ``status_field``: a dot-path into the raw response whose value
        indicates success/failure (see `_is_failure_status`).
      - ``error_path``: a dot-path into the raw response holding an error
        message. If it resolves to a truthy value, the call is treated as
        failed and that value (stringified) becomes the error message.
      - If both are configured and both indicate failure, the `error_path`
        message takes precedence (it is the more specific signal).
      - When neither is configured, or both resolve to non-failure values,
        this is a no-op and the response is mapped/returned as usual.

    Args:
        result: The raw API response (prior to result_path extraction).
        mapping: The response mapping configuration.

    Raises:
        ToolResponseError: If status_field or error_path indicates failure.
    """
    if not isinstance(result, dict):
        return

    error_message: str | None = None
    if mapping.error_path is not None:
        error_value = _resolve_dot_path(result, mapping.error_path)
        if error_value:
            error_message = str(error_value)

    status_failed = False
    status_value: Any = None
    if mapping.status_field is not None:
        status_value = _resolve_dot_path(result, mapping.status_field)
        status_failed = _is_failure_status(status_value)

    if error_message is not None:
        raise ToolResponseError(error_message, response=result)
    if status_failed:
        msg = (
            f"Tool call failed: response field '{mapping.status_field}' "
            f"indicated failure ({status_value!r})"
        )
        raise ToolResponseError(msg, response=result)


def _apply_response_mapping(result: Any, api_config: ManualToolAPI) -> Any:
    """Apply response mapping to extract and reshape relevant data.

    First checks ``status_field``/``error_path`` for a failure signal (see
    `_check_response_for_error`) and raises `ToolResponseError` if found.
    Otherwise, applies ``result_path`` (a "$"-rooted dot-notation JSONPath)
    to extract a sub-object from the raw response. Then, if ``field_map``
    is set, builds a new dict whose keys are the field_map's output names
    and whose values are resolved via dot-notation from the extracted
    result.

    Args:
        result: The raw API response.
        api_config: Configuration with response mapping rules.

    Returns:
        The mapped result.

    Raises:
        ToolResponseError: If the response indicates failure via
            status_field or error_path.
    """
    mapping = api_config.response_mapping
    _check_response_for_error(result, mapping)
    current = result

    if mapping.result_path != "$" and isinstance(result, dict):
        path = mapping.result_path
        if path.startswith("$."):
            path = path[2:]
        elif path.startswith("$"):
            path = path[1:]

        resolved = result
        for part in path.split("."):
            if isinstance(resolved, dict) and part in resolved:
                resolved = resolved[part]
            else:
                # Path segment not found: fall back to the raw response.
                resolved = result
                break
        current = resolved

    if mapping.field_map and isinstance(current, dict):
        return {
            output_key: _resolve_dot_path(current, source_path)
            for output_key, source_path in mapping.field_map.items()
        }

    return current
