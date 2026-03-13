"""Manual tool builder for Forge Agent.

Creates dynamic async Python functions from ManualTool config definitions,
where each function makes HTTP calls via httpx based on ManualToolAPI config.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import httpx
from forge_config.schema import ManualTool, ManualToolAPI, ParamType
from forge_config.secret_resolver import SecretResolver
from pydantic_ai.tools import Tool

from forge_agent.builder.openapi import _resolve_auth_headers

# Mapping from ParamType enum to Python annotation types.
_PARAM_TYPE_MAP: dict[ParamType, type] = {
    ParamType.STRING: str,
    ParamType.INTEGER: int,
    ParamType.NUMBER: float,
    ParamType.BOOLEAN: bool,
    ParamType.ARRAY: list,
    ParamType.OBJECT: dict,
}


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
    ) -> None:
        self._config = tool_config
        self._http_client = http_client
        self._secret_resolver = secret_resolver

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

        return Tool(tool_func, name=tool_name)


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


async def _execute_api_call(
    api_config: ManualToolAPI,
    params: dict[str, Any],
    auth_headers: dict[str, str],
    http_client: httpx.AsyncClient | None = None,
) -> Any:
    """Execute an HTTP API call based on ManualToolAPI configuration.

    Args:
        api_config: The API call configuration.
        params: Parameter values from the tool invocation.
        auth_headers: Pre-resolved authentication headers.
        http_client: Optional pre-configured httpx client.

    Returns:
        The parsed JSON response or raw text.
    """
    url = _resolve_template_string(api_config.resolved_url, params)

    # Start with pre-resolved auth headers, then layer on config headers.
    headers = dict(auth_headers)
    for k, v in api_config.headers.items():
        headers[k] = _resolve_template_string(v, params)

    body = None
    if api_config.body_template is not None:
        body = _resolve_template(api_config.body_template, params)

    method = api_config.method.value

    client = http_client or httpx.AsyncClient()
    should_close = http_client is None

    try:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
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


def _apply_response_mapping(result: Any, api_config: ManualToolAPI) -> Any:
    """Apply response mapping to extract relevant data.

    Args:
        result: The raw API response.
        api_config: Configuration with response mapping rules.

    Returns:
        The mapped result.
    """
    mapping = api_config.response_mapping
    if mapping.result_path == "$" or not isinstance(result, dict):
        return result

    # Simple dot-notation path resolution.
    parts = mapping.result_path.lstrip("$.").split(".")
    current = result
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return result
    return current
