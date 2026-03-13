"""OpenAPI-based tool builder for Forge Agent.

Generates PydanticAI Tool objects from OpenAPI 3.x specifications.
Fetches specs from URLs or reads from local paths, parses operations,
applies filtering and renaming, and creates async tool functions that
make real HTTP calls via httpx.
"""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import AuthConfig, AuthType, OpenAPISource
from forge_config.secret_resolver import SecretResolver
from pydantic_ai.tools import Tool

logger = logging.getLogger(__name__)

# Mapping from OpenAPI/JSON Schema types to Python types.
_OPENAPI_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class OpenAPIToolBuilder:
    """Build PydanticAI tools from an OpenAPI 3.x specification.

    Takes an OpenAPISource config, loads and parses the OpenAPI spec,
    then generates async tool functions that make real HTTP calls.
    Supports filtering by tags/operations and renaming via route_map.
    """

    def __init__(
        self,
        source: OpenAPISource,
        http_client: httpx.AsyncClient | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self._source = source
        self._http_client = http_client
        self._secret_resolver = secret_resolver

    async def build(self) -> list[Tool[None]]:
        """Build tool definitions from the OpenAPI spec.

        Fetches/reads the spec, parses operations, applies filters,
        and generates PydanticAI Tool objects. Auth secrets are resolved
        at build time so that failures surface early.

        Returns:
            List of PydanticAI Tool objects, one per matching operation.

        Raises:
            SecretResolutionError: If auth is configured but the secret
                cannot be resolved.
        """
        spec = await self._load_spec()
        base_url = self._extract_base_url(spec)
        operations = self._extract_operations(spec)
        filtered = self._filter_operations(operations)
        # Resolve auth headers once at build time (fail-fast).
        auth_headers = _resolve_auth_headers(self._source.auth, self._secret_resolver)
        return self._build_tools(filtered, base_url, auth_headers)

    async def _load_spec(self) -> dict[str, Any]:
        """Load the OpenAPI spec from URL, local path, or inline spec.

        Returns:
            The parsed OpenAPI spec as a dict.

        Raises:
            ValueError: If no valid spec source is configured.
            httpx.HTTPStatusError: If fetching a remote spec fails.
        """
        source = self._source

        if source.url:
            return await self._fetch_remote_spec(source.url)

        if source.path:
            return self._read_local_spec(source.path)

        msg = "OpenAPI source has no url or path configured"
        raise ValueError(msg)

    async def _fetch_remote_spec(self, url: str) -> dict[str, Any]:
        """Fetch an OpenAPI spec from a remote URL.

        Args:
            url: The URL to fetch the spec from.

        Returns:
            The parsed spec dict.
        """
        client = self._http_client or httpx.AsyncClient()
        should_close = self._http_client is None

        try:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        finally:
            if should_close:
                await client.aclose()

    def _read_local_spec(self, path: str) -> dict[str, Any]:
        """Read an OpenAPI spec from a local file.

        Args:
            path: Path to the spec file (JSON or YAML).

        Returns:
            The parsed spec dict.
        """
        file_path = Path(path)
        content = file_path.read_text()

        if file_path.suffix in (".yaml", ".yml"):
            try:
                import yaml

                parsed: dict[str, Any] = yaml.safe_load(content)
                return parsed
            except ImportError:
                msg = "PyYAML is required to parse YAML specs"
                raise ImportError(msg)  # noqa: B904
        loaded: dict[str, Any] = json.loads(content)
        return loaded

    def _extract_base_url(self, spec: dict[str, Any]) -> str:
        """Extract the base URL from the spec's servers list.

        Falls back to the source URL (minus the spec path) or
        an empty string if no server is found.

        Args:
            spec: The parsed OpenAPI spec.

        Returns:
            The base URL string.
        """
        servers = spec.get("servers", [])
        if servers and isinstance(servers[0], dict):
            server_url: str = servers[0].get("url", "")
            return server_url.rstrip("/")

        # Fall back to source URL without the spec file portion.
        if self._source.url:
            url = self._source.url
            # Remove common spec file suffixes.
            for suffix in ("/openapi.json", "/openapi.yaml", "/swagger.json"):
                if url.endswith(suffix):
                    return url[: -len(suffix)]
            return url.rstrip("/")

        return ""

    def _extract_operations(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract all operations from the OpenAPI paths.

        Args:
            spec: The parsed OpenAPI spec.

        Returns:
            List of operation dicts, each containing:
                - operation_id: str
                - method: str (uppercase)
                - path: str
                - summary: str
                - description: str
                - tags: list[str]
                - parameters: list of param dicts
                - request_body: dict or None
        """
        operations: list[dict[str, Any]] = []
        paths = spec.get("paths", {})
        http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            # Collect path-level parameters.
            path_params = path_item.get("parameters", [])

            for method in http_methods:
                if method not in path_item:
                    continue

                op = path_item[method]
                if not isinstance(op, dict):
                    continue

                operation_id = op.get("operationId", f"{method}_{path}")
                # Sanitize operation_id to be a valid Python identifier.
                operation_id = _sanitize_name(operation_id)

                # Merge path-level and operation-level parameters.
                op_params = list(path_params) + op.get("parameters", [])

                operations.append(
                    {
                        "operation_id": operation_id,
                        "method": method.upper(),
                        "path": path,
                        "summary": op.get("summary", ""),
                        "description": op.get("description", ""),
                        "tags": op.get("tags", []),
                        "parameters": op_params,
                        "request_body": op.get("requestBody"),
                    }
                )

        return operations

    def _filter_operations(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter operations based on include_tags and include_operations.

        If both filters are empty, all operations are included.
        If include_tags is set, only operations with matching tags are kept.
        If include_operations is set, only operations with matching IDs are kept.
        If both are set, operations matching either filter are kept.

        Args:
            operations: The full list of extracted operations.

        Returns:
            The filtered list of operations.
        """
        tags = set(self._source.include_tags)
        op_ids = set(self._source.include_operations)

        if not tags and not op_ids:
            return operations

        filtered: list[dict[str, Any]] = []
        for op in operations:
            if tags and set(op["tags"]) & tags or op_ids and op["operation_id"] in op_ids:
                filtered.append(op)
        return filtered

    def _build_tools(
        self,
        operations: list[dict[str, Any]],
        base_url: str,
        auth_headers: dict[str, str],
    ) -> list[Tool[None]]:
        """Build PydanticAI Tool objects from parsed operations.

        Args:
            operations: The filtered list of operations.
            base_url: The base URL for API calls.
            auth_headers: Pre-resolved authentication headers.

        Returns:
            List of PydanticAI Tool objects.
        """
        tools: list[Tool[None]] = []
        route_map = self._source.route_map
        prefix = self._source.prefix
        http_client = self._http_client

        for op in operations:
            # Determine the tool name.
            route_key = f"{op['method']} {op['path']}"
            if route_map and route_key in route_map:
                tool_name = route_map[route_key]
            else:
                tool_name = op["operation_id"]

            # Apply namespace prefix.
            if prefix:
                tool_name = f"{prefix}_{tool_name}"

            # Build description.
            description = op["summary"] or op["description"] or f"{op['method']} {op['path']}"

            # Build the tool function.
            tool = _build_tool_function(
                name=tool_name,
                description=description,
                method=op["method"],
                path=op["path"],
                base_url=base_url,
                parameters=op["parameters"],
                request_body=op["request_body"],
                auth_headers=auth_headers,
                http_client=http_client,
            )
            tools.append(tool)

        return tools


def _sanitize_name(name: str) -> str:
    """Sanitize a string to be a valid Python identifier.

    Replaces non-alphanumeric characters with underscores,
    strips leading/trailing underscores.

    Args:
        name: The raw name string.

    Returns:
        A sanitized name suitable for use as a Python identifier.
    """
    result = ""
    for ch in name:
        result += ch if ch.isalnum() or ch == "_" else "_"
    return result.strip("_")


def _build_tool_function(
    *,
    name: str,
    description: str,
    method: str,
    path: str,
    base_url: str,
    parameters: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
    auth_headers: dict[str, str],
    http_client: httpx.AsyncClient | None,
) -> Tool[None]:
    """Build a single PydanticAI Tool for an OpenAPI operation.

    Creates a dynamic async function with proper signature that
    makes real HTTP calls when invoked.

    Args:
        name: The tool name.
        description: The tool description.
        method: HTTP method (uppercase).
        path: The URL path template (e.g., "/users/{user_id}").
        base_url: The base URL for the API.
        parameters: OpenAPI parameter definitions.
        request_body: OpenAPI requestBody definition or None.
        auth_headers: Pre-resolved authentication headers.
        http_client: Optional pre-configured httpx client.

    Returns:
        A PydanticAI Tool wrapping an async HTTP-calling function.
    """
    # Parse parameters into signature components.
    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, type] = {"return": Any}
    path_param_names: set[str] = set()
    query_param_names: set[str] = set()
    header_param_names: set[str] = set()

    for param in parameters:
        if not isinstance(param, dict):
            continue
        param_name = param.get("name", "")
        if not param_name:
            continue

        location = param.get("in", "query")
        schema = param.get("schema", {})
        param_type = _OPENAPI_TYPE_MAP.get(schema.get("type", "string"), str)
        required = param.get("required", False)

        # Track parameter location.
        if location == "path":
            path_param_names.add(param_name)
            required = True  # Path params are always required.
        elif location == "query":
            query_param_names.add(param_name)
        elif location == "header":
            header_param_names.add(param_name)
        else:
            continue  # Skip cookie params, etc.

        safe_name = _sanitize_name(param_name)
        default = inspect.Parameter.empty if required else schema.get("default")
        sig_params.append(
            inspect.Parameter(
                safe_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=param_type,
            )
        )
        annotations[safe_name] = param_type

    # Add body parameter if there is a request body.
    has_body = False
    if request_body and isinstance(request_body, dict):
        content = request_body.get("content", {})
        if "application/json" in content:
            has_body = True
            sig_params.append(
                inspect.Parameter(
                    "body",
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=inspect.Parameter.empty
                    if request_body.get("required", False)
                    else None,
                    annotation=dict,
                )
            )
            annotations["body"] = dict

    sig = inspect.Signature(parameters=sig_params)

    async def tool_func(**kwargs: Any) -> Any:
        return await _execute_openapi_call(
            method=method,
            path=path,
            base_url=base_url,
            path_params=path_param_names,
            query_params=query_param_names,
            header_params=header_param_names,
            has_body=has_body,
            auth_headers=auth_headers,
            http_client=http_client,
            call_kwargs=kwargs,
        )

    tool_func.__signature__ = sig  # type: ignore[attr-defined]
    tool_func.__name__ = name
    tool_func.__qualname__ = name
    tool_func.__doc__ = description
    tool_func.__annotations__ = annotations

    return Tool(tool_func, name=name)


def _resolve_auth_headers(
    auth: AuthConfig,
    resolver: SecretResolver | None,
) -> dict[str, str]:
    """Resolve authentication secrets and return headers to apply.

    Secrets are resolved once at build time so that missing or
    misconfigured secrets surface immediately rather than at
    request time.

    Args:
        auth: The authentication configuration.
        resolver: Secret resolver for looking up secret values.

    Returns:
        A dict of header-name to header-value for authentication.

    Raises:
        SecretResolutionError: If auth requires a secret but no
            resolver is provided, or if the secret cannot be resolved.
    """
    if auth.type == AuthType.NONE:
        return {}

    if resolver is None:
        msg = f"Auth type '{auth.type.value}' requires a SecretResolver, but none was provided"
        raise SecretResolutionError(msg)

    if auth.type == AuthType.BEARER:
        return _resolve_bearer_headers(auth, resolver)
    if auth.type == AuthType.API_KEY:
        return _resolve_api_key_headers(auth, resolver)
    if auth.type == AuthType.BASIC:
        return _resolve_basic_headers(auth, resolver)

    return {}


def _resolve_bearer_headers(auth: AuthConfig, resolver: SecretResolver) -> dict[str, str]:
    """Resolve bearer token auth into headers."""
    if auth.token is None:
        msg = "Bearer auth requires a token SecretRef"
        raise SecretResolutionError(msg)
    token = resolver.resolve(auth.token)
    return {auth.header_name: f"Bearer {token}"}


def _resolve_api_key_headers(auth: AuthConfig, resolver: SecretResolver) -> dict[str, str]:
    """Resolve API key auth into headers."""
    if auth.token is None:
        msg = "API key auth requires a token SecretRef"
        raise SecretResolutionError(msg)
    api_key = resolver.resolve(auth.token)
    return {auth.header_name: api_key}


def _resolve_basic_headers(auth: AuthConfig, resolver: SecretResolver) -> dict[str, str]:
    """Resolve basic auth credentials into headers."""
    import base64

    if auth.username is None or auth.password is None:
        msg = "Basic auth requires both username and password SecretRefs"
        raise SecretResolutionError(msg)
    username = resolver.resolve(auth.username)
    password = resolver.resolve(auth.password)
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {auth.header_name: f"Basic {creds}"}


async def _execute_openapi_call(
    *,
    method: str,
    path: str,
    base_url: str,
    path_params: set[str],
    query_params: set[str],
    header_params: set[str],
    has_body: bool,
    auth_headers: dict[str, str],
    http_client: httpx.AsyncClient | None,
    call_kwargs: dict[str, Any],
) -> Any:
    """Execute an HTTP call for an OpenAPI operation.

    Args:
        method: HTTP method (uppercase).
        path: URL path template.
        base_url: Base URL for the API.
        path_params: Set of path parameter names.
        query_params: Set of query parameter names.
        header_params: Set of header parameter names.
        has_body: Whether the operation expects a JSON body.
        auth_headers: Pre-resolved authentication headers.
        http_client: Optional pre-configured httpx client.
        call_kwargs: The keyword arguments from the tool invocation.

    Returns:
        The parsed JSON response or raw text.
    """
    # Resolve path parameters.
    resolved_path = path
    for param_name in path_params:
        safe_name = _sanitize_name(param_name)
        if safe_name in call_kwargs:
            resolved_path = resolved_path.replace(f"{{{param_name}}}", str(call_kwargs[safe_name]))

    url = f"{base_url}{resolved_path}"

    # Collect query parameters.
    query: dict[str, Any] = {}
    for param_name in query_params:
        safe_name = _sanitize_name(param_name)
        if safe_name in call_kwargs and call_kwargs[safe_name] is not None:
            query[param_name] = call_kwargs[safe_name]

    # Collect header parameters — start with pre-resolved auth headers.
    headers: dict[str, str] = dict(auth_headers)
    for param_name in header_params:
        safe_name = _sanitize_name(param_name)
        if safe_name in call_kwargs and call_kwargs[safe_name] is not None:
            headers[param_name] = str(call_kwargs[safe_name])

    # Extract body.
    body = call_kwargs.get("body") if has_body else None

    client = http_client or httpx.AsyncClient()
    should_close = http_client is None

    try:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=query or None,
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()

        try:
            return response.json()
        except Exception:
            return response.text
    finally:
        if should_close:
            await client.aclose()
