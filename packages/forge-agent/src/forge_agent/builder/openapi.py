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
from urllib.parse import urlsplit

import httpx
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import AuthConfig, AuthType, EgressPolicy, OpenAPISource
from forge_config.secret_resolver import SecretResolver
from forge_security.egress import (
    BoundCredential,
    credential_binding_from_raw_auth,
    enforce_binding,
    make_guarded_client,
)
from pydantic_ai.tools import Tool

from forge_agent.active.gate import ToolGate

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
        tool_gate: ToolGate | None = None,
        *,
        egress_policy: EgressPolicy | None = None,
        requested_by: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._source = source
        self._http_client = http_client
        self._secret_resolver = secret_resolver
        # ADR-0006: SSRF/egress policy threaded from the registry
        # (config.security.egress). ``None`` => a default-safe EgressPolicy()
        # is used at call time (IP-layer deny + require_https, pin-to-origin).
        self._egress_policy = egress_policy
        # ADR-0005 SS6.2 (security review finding #1): OpenAPI-sourced
        # tools must be gate-able exactly like manual tools. ``tool_gate``
        # is the shared gate the registry threads through (mirrors
        # ManualToolBuilder); ``requested_by``/``run_id`` are recorded on
        # any draft this source's gated operations create.
        self._tool_gate = tool_gate
        self._requested_by = requested_by
        self._run_id = run_id

    async def build(self) -> list[Tool[None]]:
        """Build tool definitions from the OpenAPI spec.

        Fetches/reads the spec, resolves local ``$ref`` pointers, parses
        operations, applies filters, and generates PydanticAI Tool objects.
        Auth secrets are resolved at build time so that failures surface
        early.

        Returns:
            List of PydanticAI Tool objects, one per matching operation.

        Raises:
            SecretResolutionError: If auth is configured but the secret
                cannot be resolved.
        """
        spec = await self._load_spec()
        spec = _resolve_spec_refs(spec)
        base_url = self._extract_base_url(spec)
        operations = self._extract_operations(spec)
        filtered = self._filter_operations(operations)
        # ADR-0006: resolve the bound credential once at build time (fail-fast).
        # The credential is pinned to auth.allowed_hosts, or -- when empty -- to
        # the OPERATOR-declared origin host ("pin to where you were pointed"), so
        # a hostile spec ``servers[0].url`` cannot drag it to an attacker host.
        policy = self._egress_policy or EgressPolicy()
        declared_host = self._declared_host(base_url)
        bound = resolve_bound_credential(
            self._source.auth,
            self._secret_resolver,
            declared_host=declared_host,
        )
        return self._build_tools(filtered, base_url, bound, policy)

    def _declared_host(self, base_url: str) -> str | None:
        """Return the OPERATOR-declared origin host used to bind the credential.

        For a REMOTE spec the operator named the host via ``source.url``, so the
        credential binds THERE -- a spec that then advertises a hostile
        ``servers[0].url`` cannot drag the credential to that host (the binding
        check rejects it). For a local/inline spec there is no ``source.url`` and
        the base_url (from the operator-authored spec content) is the declared
        origin.
        """
        if self._source.url:
            return urlsplit(self._source.url).hostname
        return urlsplit(base_url).hostname

    async def _load_spec(self) -> dict[str, Any]:
        """Load the OpenAPI spec from inline content, URL, or local path.

        The documented ``spec`` field accepts either inline JSON/YAML spec
        content, or a URL/path that is auto-detected -- forge-config's
        ``OpenAPISource`` validator already promotes a URL-shaped ``spec``
        value into ``source.url``, and any other ``spec`` value into
        ``source.path`` (for the common "spec is actually a file path"
        case). Inline *content* (rather than a path) is detected here via
        ``_looks_like_inline_spec`` and parsed directly, so it is never
        mistakenly opened as a file.

        Returns:
            The parsed OpenAPI spec as a dict.

        Raises:
            ValueError: If no valid spec source is configured.
            httpx.HTTPStatusError: If fetching a remote spec fails.
        """
        source = self._source

        if source.spec is not None and _looks_like_inline_spec(source.spec):
            return _parse_spec_content(source.spec)

        if source.url:
            return await self._fetch_remote_spec(source.url)

        if source.path:
            return self._read_local_spec(source.path)

        msg = "OpenAPI source has no url or path configured"
        raise ValueError(msg)

    async def _fetch_remote_spec(self, url: str) -> dict[str, Any]:
        """Fetch an OpenAPI spec from a remote URL.

        The docs state the URL may point to a JSON or YAML spec. JSON is
        tried first (the common case); if the body isn't valid JSON, it is
        parsed as YAML.

        Args:
            url: The URL to fetch the spec from.

        Returns:
            The parsed spec dict.
        """
        client = self._http_client or make_guarded_client(policy=self._egress_policy)
        should_close = self._http_client is None

        try:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            try:
                result: dict[str, Any] = response.json()
                return result
            except (json.JSONDecodeError, ValueError):
                return _parse_spec_content(response.text)
        finally:
            if should_close:
                await client.aclose()

    def _read_local_spec(self, path: str) -> dict[str, Any]:
        """Read an OpenAPI spec from a local file (JSON or YAML).

        Args:
            path: Path to the spec file (JSON or YAML).

        Returns:
            The parsed spec dict.
        """
        return _parse_spec_content(Path(path).read_text())

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
        bound: BoundCredential,
        policy: EgressPolicy,
    ) -> list[Tool[None]]:
        """Build PydanticAI Tool objects from parsed operations.

        Args:
            operations: The filtered list of operations.
            base_url: The base URL for API calls.
            bound: The resolved, host-bound credential (ADR-0006).
            policy: The SSRF/egress policy threaded into each tool's client.

        Returns:
            List of PydanticAI Tool objects.
        """
        tools: list[Tool[None]] = []
        route_map = self._source.route_map
        prefix = self._source.prefix
        http_client = self._http_client
        # ADR-0005 SS6.2 (finding #1): operations named in
        # `approval_operations` are gated individually (matched by
        # operationId or the legacy "METHOD /path" key -- same dual-key
        # convention as `route_map`), regardless of `requires_approval`.
        approval_ops = set(self._source.approval_operations)

        for op in operations:
            # Determine the tool name. route_map is documented and canonically
            # keyed by operationId (e.g. "findPetsByStatus: find_pets"); the
            # legacy "METHOD /path" key form is also honored for backward
            # compatibility, with operationId taking precedence when both
            # could match.
            route_key = f"{op['method']} {op['path']}"
            if route_map and op["operation_id"] in route_map:
                tool_name = route_map[op["operation_id"]]
            elif route_map and route_key in route_map:
                tool_name = route_map[route_key]
            else:
                tool_name = op["operation_id"]

            # Apply namespace prefix.
            if prefix:
                tool_name = f"{prefix}_{tool_name}"

            # Build description.
            description = op["summary"] or op["description"] or f"{op['method']} {op['path']}"

            gated = (
                self._source.requires_approval
                or op["operation_id"] in approval_ops
                or route_key in approval_ops
            )
            if gated and self._tool_gate is None:
                # Fail-closed (mirrors ManualToolBuilder): a gated operation
                # must never be built as an ungated tool just because no
                # ToolGate was supplied to this builder.
                msg = (
                    f"OpenAPI source {self._source.name!r} operation "
                    f"{op['operation_id']!r} (tool {tool_name!r}) has "
                    "requires_approval/approval_operations set but no "
                    "ToolGate was supplied to OpenAPIToolBuilder; refusing "
                    "to build it as an ungated tool."
                )
                raise ValueError(msg)

            # Build the tool function.
            tool = _build_tool_function(
                name=tool_name,
                description=description,
                method=op["method"],
                path=op["path"],
                base_url=base_url,
                parameters=op["parameters"],
                request_body=op["request_body"],
                bound=bound,
                egress_policy=policy,
                http_client=http_client,
                tool_gate=self._tool_gate if gated else None,
                requested_by=self._requested_by,
                run_id=self._run_id,
            )
            tools.append(tool)

        return tools


def _looks_like_inline_spec(text: str) -> bool:
    """Heuristically detect inline JSON/YAML spec *content* vs. a path/URL.

    A URL is never mistaken for inline content (checked and rejected up
    front; forge-config's validator already promotes URL-shaped ``spec``
    values into ``source.url`` before this is even consulted). Otherwise,
    content is recognized by syntax no valid file path can contain: a
    JSON object (``{``), any embedded newline (multi-line YAML/JSON --
    YAML dumps commonly sort keys alphabetically, so an ``openapi:`` key
    is not reliably the first line), or a single-line document starting
    with an OpenAPI/Swagger YAML root key (``openapi:``/``swagger:``).

    Args:
        text: The raw ``spec`` field value.

    Returns:
        True if `text` looks like inline spec content rather than a
        file path.
    """
    stripped = text.strip()
    if not stripped or stripped.startswith(("http://", "https://")):
        return False
    if stripped.startswith("{") or "\n" in stripped:
        return True
    return stripped.lower().startswith(("openapi:", "swagger:"))


def _parse_spec_content(content: str) -> dict[str, Any]:
    """Parse spec content that may be JSON or YAML.

    Tries JSON first (stricter, and the common case); falls back to YAML
    (a superset that can also parse JSON documents) so both local files
    and remote/inline content are handled uniformly regardless of a
    ``.yaml``/``.yml`` suffix or content-type.

    Args:
        content: The raw spec text.

    Returns:
        The parsed spec dict.

    Raises:
        ImportError: If the content isn't valid JSON and PyYAML isn't
            installed to fall back to.
    """
    try:
        loaded: dict[str, Any] = json.loads(content)
        return loaded
    except json.JSONDecodeError:
        pass

    try:
        import yaml
    except ImportError:
        msg = "PyYAML is required to parse YAML/inline OpenAPI specs"
        raise ImportError(msg)  # noqa: B904

    parsed: dict[str, Any] = yaml.safe_load(content)
    return parsed


def _resolve_spec_refs(spec: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve local ``$ref`` pointers (``#/components/...``).

    OpenAPI specs (like the Swagger Petstore spec the docs cite) commonly
    factor shared parameters, request bodies, and schemas into
    ``components`` and reference them via ``$ref``. Without resolving
    these, ``$ref``'d parameters/request bodies are silently dropped by
    operation extraction. Only local (in-document, ``#/...``) refs are
    resolved; external file/URL refs are left untouched (out of scope for
    a single-file spec).

    Args:
        spec: The parsed OpenAPI spec, potentially containing ``$ref`` entries.

    Returns:
        A new spec tree with all local ``$ref`` occurrences replaced by
        the objects they point to.
    """
    resolved = _resolve_ref_node(spec, spec, frozenset())
    # The top-level spec document is always a dict; only nested values
    # walked by `_resolve_ref_node` may be non-dict.
    assert isinstance(resolved, dict)
    return resolved


def _resolve_ref_node(node: Any, root: dict[str, Any], seen: frozenset[str]) -> Any:
    """Recursively walk `node`, replacing local ``$ref`` dicts with their targets.

    Args:
        node: The current node being walked (dict, list, or scalar).
        root: The full spec document, used to resolve ``#/...`` pointers.
        seen: The set of ``$ref`` pointers already followed on this path,
            used to break cycles (e.g. a self-referential schema).

    Returns:
        The node with all local ``$ref`` occurrences resolved.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in seen:
                # Cyclic reference: stop recursing to avoid infinite loops.
                return {}
            target = _lookup_json_pointer(root, ref)
            return _resolve_ref_node(target, root, seen | {ref})
        return {key: _resolve_ref_node(value, root, seen) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_ref_node(item, root, seen) for item in node]
    return node


def _lookup_json_pointer(root: dict[str, Any], ref: str) -> Any:
    """Resolve a local JSON pointer such as ``#/components/parameters/PetId``.

    Args:
        root: The full spec document.
        ref: A local ``$ref`` string starting with ``#/``.

    Returns:
        The object at the pointer location.

    Raises:
        ValueError: If any segment of the pointer cannot be resolved.
    """
    parts = ref[2:].split("/")
    current: Any = root
    for raw_part in parts:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            msg = f"Unresolvable $ref: {ref}"
            raise ValueError(msg)
    return current


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
    bound: BoundCredential,
    egress_policy: EgressPolicy,
    http_client: httpx.AsyncClient | None,
    tool_gate: ToolGate | None = None,
    requested_by: str | None = None,
    run_id: str | None = None,
) -> Tool[None]:
    """Build a single PydanticAI Tool for an OpenAPI operation.

    Creates a dynamic async function with proper signature that
    makes real HTTP calls when invoked -- unless `tool_gate` is given
    (ADR-0005 SS6.2, finding #1), in which case the returned tool drafts
    an approval instead of executing, exactly like a gated manual tool.

    Args:
        name: The tool name.
        description: The tool description.
        method: HTTP method (uppercase).
        path: The URL path template (e.g., "/users/{user_id}").
        base_url: The base URL for the API.
        parameters: OpenAPI parameter definitions.
        request_body: OpenAPI requestBody definition or None.
        bound: The resolved, host-bound credential (ADR-0006).
        egress_policy: The SSRF/egress policy for the outbound client.
        http_client: Optional pre-configured httpx client.
        tool_gate: When given, the built tool is gated: invoking it drafts
            an :class:`~forge_agent.active.gate.ApprovalRequest` instead of
            making the real HTTP call.
        requested_by: The requesting agent/persona, recorded on the draft.
        run_id: The autonomous run or session id, when known.

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
            bound=bound,
            egress_policy=egress_policy,
            http_client=http_client,
            call_kwargs=kwargs,
        )

    tool_func.__signature__ = sig  # type: ignore[attr-defined]
    tool_func.__name__ = name
    tool_func.__qualname__ = name
    tool_func.__doc__ = description
    tool_func.__annotations__ = annotations

    final_func: Any = tool_func
    if tool_gate is not None:
        # ADR-0005 SS6.2 (finding #1): wrap exactly like a gated manual
        # tool -- invoking it drafts an ApprovalRequest instead of making
        # the real HTTP call; the real call only fires on approve.
        gated_func = tool_gate.wrap(
            name,
            tool_func,
            requested_by=requested_by,
            run_id=run_id,
        )
        gated_func.__signature__ = sig  # type: ignore[attr-defined]
        gated_func.__name__ = name
        gated_func.__qualname__ = name
        gated_func.__doc__ = description
        gated_func.__annotations__ = annotations
        final_func = gated_func

    return Tool(final_func, name=name)


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


def resolve_bound_credential(
    auth: AuthConfig,
    resolver: SecretResolver | None,
    *,
    declared_host: str | None,
) -> BoundCredential:
    """Resolve auth headers once (fail-fast) and compute the destination host
    set the resulting credential is bound to (ADR-0006).

    The binding RULE itself lives in
    ``forge_security.egress.binding.credential_binding_from_raw_auth`` -- the
    SAME predicate the write-time overlay gate in
    ``forge_gateway.routes.admin`` applies to raw BASE yaml -- so the
    connect-time and write-time answers cannot diverge. In short: if
    ``auth.allowed_hosts`` is set it wins; otherwise the credential is pinned
    to ``declared_host`` -- the BASE-declared destination -- so every existing
    config is safe with no rewrite ("pin to where you were pointed"). A
    no-auth config yields ``BoundCredential.none()``.

    Args:
        auth: The authentication configuration.
        resolver: Secret resolver for looking up secret values.
        declared_host: Host of the BASE-declared destination URL.

    Returns:
        A ``BoundCredential`` pairing the resolved headers with their allowed
        host set and the configured on-violation action.

    Raises:
        SecretResolutionError: If auth requires a secret that cannot resolve.
    """
    headers = _resolve_auth_headers(auth, resolver)
    if not headers:
        return BoundCredential.none()
    _, allowed = credential_binding_from_raw_auth(
        {"type": auth.type.value, "allowed_hosts": list(auth.allowed_hosts)},
        declared_host=declared_host,
    )
    return BoundCredential(
        headers=headers,
        allowed_hosts=allowed,
        action=auth.on_egress_violation,
    )


async def _execute_openapi_call(
    *,
    method: str,
    path: str,
    base_url: str,
    path_params: set[str],
    query_params: set[str],
    header_params: set[str],
    has_body: bool,
    bound: BoundCredential,
    egress_policy: EgressPolicy,
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
        bound: The resolved, host-bound credential (ADR-0006).
        egress_policy: The SSRF/egress policy; guards the outbound client and
            gates the secret->destination binding on the FINAL URL.
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

    # ADR-0006: enforce the secret->destination binding against the FINAL,
    # fully-resolved URL. Raises EgressViolationError (REJECT) or returns the
    # credential minus its headers (DROP) when the destination host is out of the
    # bound set -- this is what stops a hostile spec ``servers[0].url`` from
    # carrying the operator credential to an attacker host. Header params (which
    # are not credential-bound) layer on top.
    policy = egress_policy or EgressPolicy()
    headers: dict[str, str] = dict(enforce_binding(url, bound, policy=policy))
    for param_name in header_params:
        safe_name = _sanitize_name(param_name)
        if safe_name in call_kwargs and call_kwargs[safe_name] is not None:
            headers[param_name] = str(call_kwargs[safe_name])

    # Extract body.
    body = call_kwargs.get("body") if has_body else None

    client = http_client or make_guarded_client(policy=egress_policy)
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
