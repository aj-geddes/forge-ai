"""Tests for ManualToolBuilder."""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from forge_agent.active.gate import ApprovalStatus, ApprovalStore, ToolGate
from forge_agent.builder import manual as manual_module
from forge_agent.builder.manual import ManualToolBuilder, ToolResponseError
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import (
    AuthConfig,
    AuthType,
    HTTPMethod,
    ManualTool,
    ManualToolAPI,
    ParameterDef,
    ParamType,
    ResponseMapping,
    SecretRef,
    SecretSource,
)


def _make_manual_tool(
    name: str = "test_tool",
    description: str = "A test tool",
    url: str = "https://api.example.com/test",
    method: HTTPMethod = HTTPMethod.GET,
    parameters: list[ParameterDef] | None = None,
    body_template: dict[str, Any] | None = None,
    response_mapping: ResponseMapping | None = None,
    requires_approval: bool = False,
) -> ManualTool:
    """Helper to create a ManualTool config for testing."""
    return ManualTool(
        name=name,
        description=description,
        parameters=parameters or [],
        api=ManualToolAPI(
            url=url,
            method=method,
            body_template=body_template,
            response_mapping=response_mapping or ResponseMapping(),
        ),
        requires_approval=requires_approval,
    )


class TestManualToolBuilder:
    """Tests for ManualToolBuilder.build()."""

    def test_build_returns_tool_with_correct_name(self) -> None:
        config = _make_manual_tool(name="my_tool", description="Does something")
        builder = ManualToolBuilder(config)
        tool = builder.build()
        assert tool.name == "my_tool"

    def test_build_creates_function_with_proper_signature(self) -> None:
        config = _make_manual_tool(
            parameters=[
                ParameterDef(name="city", type=ParamType.STRING, description="City name"),
                ParameterDef(
                    name="count",
                    type=ParamType.INTEGER,
                    description="Count",
                    required=False,
                    default=10,
                ),
            ]
        )
        builder = ManualToolBuilder(config)
        tool = builder.build()

        # Access the underlying function's signature.
        func = tool.function
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        assert len(params) == 2
        assert params[0].name == "city"
        assert params[0].annotation is str
        assert params[0].default is inspect.Parameter.empty

        assert params[1].name == "count"
        assert params[1].annotation is int
        assert params[1].default == 10

    def test_build_creates_function_with_correct_doc(self) -> None:
        config = _make_manual_tool(description="Fetches weather data")
        builder = ManualToolBuilder(config)
        tool = builder.build()
        assert tool.function.__doc__ == "Fetches weather data"

    @pytest.mark.anyio
    async def test_tool_function_makes_http_get_call(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/data/{{item_id}}",
            method=HTTPMethod.GET,
            parameters=[
                ParameterDef(name="item_id", type=ParamType.STRING),
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "123", "name": "Test Item"}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function(item_id="123")

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["url"] == "https://api.example.com/data/123"
        assert call_kwargs.kwargs["method"] == "GET"
        assert result == {"id": "123", "name": "Test Item"}

    @pytest.mark.anyio
    async def test_tool_function_makes_http_post_with_body(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/items",
            method=HTTPMethod.POST,
            parameters=[
                ParameterDef(name="name", type=ParamType.STRING),
                ParameterDef(name="value", type=ParamType.NUMBER),
            ],
            body_template={"item_name": "{{name}}", "item_value": "{{value}}"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"created": True}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(name="Widget", value=42.5)

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["method"] == "POST"
        assert call_kwargs.kwargs["json"] == {"item_name": "Widget", "item_value": "42.5"}

    @pytest.mark.anyio
    async def test_tool_function_applies_response_mapping(self) -> None:
        config = _make_manual_tool(
            response_mapping=ResponseMapping(result_path="$.data.items"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"items": [{"id": 1}, {"id": 2}]},
            "meta": {"total": 2},
        }
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function()
        assert result == [{"id": 1}, {"id": 2}]

    @pytest.mark.anyio
    async def test_tool_function_forwards_params_as_query_string_for_get(self) -> None:
        """Params not consumed by the URL template must be sent as GET query params.

        Mirrors the canonical get_weather manual tool in forge.yaml.example:
        a static endpoint (no {{placeholders}}) with two declared parameters
        that must be forwarded as HTTP query parameters.
        """
        config = _make_manual_tool(
            url="https://api.weatherapi.com/v1/current.json",
            method=HTTPMethod.GET,
            parameters=[
                ParameterDef(name="location", type=ParamType.STRING, required=True),
                ParameterDef(
                    name="units",
                    type=ParamType.STRING,
                    required=False,
                    default="metric",
                ),
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"current": {"temp_c": 18.5}}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(location="Paris", units="metric")

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["url"] == "https://api.weatherapi.com/v1/current.json"
        assert call_kwargs.kwargs["params"] == {"location": "Paris", "units": "metric"}

    @pytest.mark.anyio
    async def test_tool_function_does_not_duplicate_path_params_as_query(self) -> None:
        """A param consumed by the URL template should not also be sent as a query param."""
        config = _make_manual_tool(
            url="https://api.example.com/data/{{item_id}}",
            method=HTTPMethod.GET,
            parameters=[ParameterDef(name="item_id", type=ParamType.STRING)],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "123"}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(item_id="123")

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["url"] == "https://api.example.com/data/123"
        assert not call_kwargs.kwargs["params"]

    @pytest.mark.anyio
    async def test_tool_function_forwards_extra_params_as_body_for_post(self) -> None:
        """Params not covered by an explicit body_template on a body method go in the body."""
        config = _make_manual_tool(
            url="https://api.example.com/items",
            method=HTTPMethod.POST,
            parameters=[
                ParameterDef(name="name", type=ParamType.STRING),
                ParameterDef(name="value", type=ParamType.NUMBER),
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"created": True}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(name="Widget", value=42.5)

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["json"] == {"name": "Widget", "value": 42.5}
        assert not call_kwargs.kwargs["params"]

    @pytest.mark.anyio
    async def test_tool_function_applies_field_map(self) -> None:
        """field_map should rename/extract fields from the (result_path-extracted) response.

        Mirrors the canonical get_weather manual tool response_mapping in
        forge.yaml.example: result_path extracts a sub-object, then field_map
        renames dot-path fields within it into the final output keys.
        """
        config = _make_manual_tool(
            response_mapping=ResponseMapping(
                result_path="$.current",
                field_map={
                    "temperature": "temp_c",
                    "condition": "condition.text",
                    "humidity": "humidity",
                },
            ),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "current": {
                "temp_c": 18.5,
                "condition": {"text": "Partly cloudy"},
                "humidity": 71,
            }
        }
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function()
        assert result == {
            "temperature": 18.5,
            "condition": "Partly cloudy",
            "humidity": 71,
        }

    def test_all_param_types_mapped(self) -> None:
        config = _make_manual_tool(
            parameters=[
                ParameterDef(name="s", type=ParamType.STRING),
                ParameterDef(name="i", type=ParamType.INTEGER),
                ParameterDef(name="n", type=ParamType.NUMBER),
                ParameterDef(name="b", type=ParamType.BOOLEAN),
                ParameterDef(name="a", type=ParamType.ARRAY),
                ParameterDef(name="o", type=ParamType.OBJECT),
            ]
        )
        builder = ManualToolBuilder(config)
        tool = builder.build()

        sig = inspect.signature(tool.function)
        params = list(sig.parameters.values())
        annotations = [p.annotation for p in params]
        assert annotations == [str, int, float, bool, list, dict]


# ---------------------------------------------------------------------------
# Test: ManualToolBuilder secret resolution
# ---------------------------------------------------------------------------


class _StubResolver:
    """Test double that returns predictable values for secret refs."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def resolve(self, ref: SecretRef) -> str:
        if ref.name in self._secrets:
            return self._secrets[ref.name]
        msg = f"Secret '{ref.name}' not found"
        raise SecretResolutionError(msg)


class TestManualToolBuilderAuth:
    """Tests for ManualToolBuilder secret resolution."""

    @pytest.mark.anyio
    async def test_bearer_auth_resolved_at_build_time(self) -> None:
        """Bearer auth should resolve the token and include it in requests."""
        config = ManualTool(
            name="authed_tool",
            description="Tool with bearer auth",
            api=ManualToolAPI(
                url="https://api.example.com/protected",
                auth=AuthConfig(
                    type=AuthType.BEARER,
                    token=SecretRef(source=SecretSource.ENV, name="TOKEN"),
                ),
            ),
        )
        resolver = _StubResolver({"TOKEN": "real-token-value"})

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()
        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["Authorization"] == "Bearer real-token-value"

    @pytest.mark.anyio
    async def test_api_key_auth_resolved_at_build_time(self) -> None:
        """API key auth should resolve the key and include it in requests."""
        config = ManualTool(
            name="key_tool",
            description="Tool with API key auth",
            api=ManualToolAPI(
                url="https://api.example.com/data",
                auth=AuthConfig(
                    type=AuthType.API_KEY,
                    token=SecretRef(source=SecretSource.ENV, name="API_KEY"),
                    header_name="X-Api-Key",
                ),
            ),
        )
        resolver = _StubResolver({"API_KEY": "key-12345"})

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()
        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["X-Api-Key"] == "key-12345"

    def test_auth_without_resolver_raises_at_build_time(self) -> None:
        """Auth configured without a resolver should fail fast at build time."""
        config = ManualTool(
            name="fail_tool",
            description="Should fail",
            api=ManualToolAPI(
                url="https://api.example.com/x",
                auth=AuthConfig(
                    type=AuthType.BEARER,
                    token=SecretRef(source=SecretSource.ENV, name="TOKEN"),
                ),
            ),
        )
        builder = ManualToolBuilder(config)  # No resolver

        with pytest.raises(SecretResolutionError, match="SecretResolver"):
            builder.build()

    def test_missing_secret_raises_at_build_time(self) -> None:
        """A resolver that fails should propagate the error at build time."""
        config = ManualTool(
            name="fail_tool",
            description="Should fail",
            api=ManualToolAPI(
                url="https://api.example.com/x",
                auth=AuthConfig(
                    type=AuthType.API_KEY,
                    token=SecretRef(source=SecretSource.ENV, name="MISSING"),
                    header_name="X-Key",
                ),
            ),
        )
        resolver = _StubResolver({})

        builder = ManualToolBuilder(config, secret_resolver=resolver)

        with pytest.raises(SecretResolutionError, match="MISSING"):
            builder.build()

    def test_no_auth_works_without_resolver(self) -> None:
        """AuthType.NONE should work fine without a resolver."""
        config = _make_manual_tool()
        builder = ManualToolBuilder(config)  # No resolver, no auth
        tool = builder.build()
        assert tool.name == "test_tool"


# ---------------------------------------------------------------------------
# Test: response_mapping.status_field / error_path
# ---------------------------------------------------------------------------
#
# Semantics implemented (docs + ResponseMapping schema in forge-config):
#   - status_field: a dot-path into the raw response whose value indicates
#     success/failure. Known failure markers: the strings "error", "fail",
#     "failed", "failure" (case-insensitive) or the boolean False. Any other
#     value (including the field being absent) is treated as success.
#   - error_path: a dot-path into the raw response holding an error message.
#     If it resolves to a truthy value, the call is treated as failed and
#     that value (stringified) becomes the error message.
#   - When either indicates failure, ToolResponseError is raised instead of
#     returning a (misleadingly "successful") mapped result.
#   - When neither is configured, or both indicate success, the response is
#     mapped and returned exactly as before (no behavior change).


class TestResponseMappingStatusFieldAndErrorPath:
    """Tests for status_field/error_path wiring in _apply_response_mapping."""

    @pytest.mark.anyio
    async def test_status_field_failure_value_raises_tool_response_error(self) -> None:
        config = _make_manual_tool(
            response_mapping=ResponseMapping(status_field="status"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "error", "data": {"id": 1}}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        with pytest.raises(ToolResponseError):
            await tool.function()

    @pytest.mark.anyio
    async def test_status_field_success_value_passes_through(self) -> None:
        config = _make_manual_tool(
            response_mapping=ResponseMapping(status_field="status"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok", "data": {"id": 1}}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function()
        assert result == {"status": "ok", "data": {"id": 1}}

    @pytest.mark.anyio
    async def test_error_path_with_message_raises_tool_response_error(self) -> None:
        config = _make_manual_tool(
            response_mapping=ResponseMapping(error_path="error.message"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": {"message": "invalid API key"},
            "data": None,
        }
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        with pytest.raises(ToolResponseError, match="invalid API key"):
            await tool.function()

    @pytest.mark.anyio
    async def test_error_path_absent_or_empty_passes_through(self) -> None:
        config = _make_manual_tool(
            response_mapping=ResponseMapping(error_path="error.message"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"error": {"message": ""}, "data": {"id": 1}}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function()
        assert result == {"error": {"message": ""}, "data": {"id": 1}}

    @pytest.mark.anyio
    async def test_no_status_field_or_error_path_configured_behaves_as_before(self) -> None:
        """Default ResponseMapping (neither field set) must not change behavior."""
        config = _make_manual_tool(
            response_mapping=ResponseMapping(result_path="$.data"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "error", "data": {"id": 1}}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function()
        assert result == {"id": 1}

    @pytest.mark.anyio
    async def test_status_field_and_error_path_both_indicate_success(self) -> None:
        config = _make_manual_tool(
            response_mapping=ResponseMapping(
                status_field="status",
                error_path="error",
                result_path="$.data",
            ),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "error": None,
            "data": {"id": 7},
        }
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        result = await tool.function()
        assert result == {"id": 7}


class TestManualToolGating:
    """ADR-0005 SS6.2: a manual tool opts into the human-approval gate via
    ``requires_approval: true``. Wiring a ``ToolGate`` into the builder
    makes the built tool draft-instead-of-execute; the real HTTP call
    only fires once a human approves the draft."""

    @pytest.mark.anyio
    async def test_gated_tool_drafts_instead_of_calling_http(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/publish",
            method=HTTPMethod.POST,
            parameters=[ParameterDef(name="text", type=ParamType.STRING)],
            requires_approval=True,
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock()
        gate = ToolGate(ApprovalStore())

        builder = ManualToolBuilder(config, http_client=mock_client, tool_gate=gate)
        tool = builder.build()

        result = await tool.function(text="hello world")

        mock_client.request.assert_not_called()
        assert result["status"] == "drafted"
        assert "approval_id" in result

    @pytest.mark.anyio
    async def test_gated_tool_executes_real_call_exactly_once_on_approve(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/publish",
            method=HTTPMethod.POST,
            parameters=[ParameterDef(name="text", type=ParamType.STRING)],
            requires_approval=True,
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"published": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)
        gate = ToolGate(ApprovalStore())

        builder = ManualToolBuilder(config, http_client=mock_client, tool_gate=gate)
        tool = builder.build()

        draft = await tool.function(text="hello world")
        result = await gate.approve(draft["approval_id"])

        mock_client.request.assert_called_once()
        assert result == {"published": True}

        approval = await gate.get_approval(draft["approval_id"])
        assert approval is not None
        assert approval.status == ApprovalStatus.APPROVED

    @pytest.mark.anyio
    async def test_gated_tool_never_calls_http_on_reject(self) -> None:
        config = _make_manual_tool(requires_approval=True)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock()
        gate = ToolGate(ApprovalStore())

        builder = ManualToolBuilder(config, http_client=mock_client, tool_gate=gate)
        tool = builder.build()

        draft = await tool.function()
        await gate.reject(draft["approval_id"])

        mock_client.request.assert_not_called()

    def test_requires_approval_without_gate_raises_at_build_time(self) -> None:
        """Fail-fast (matching the secret-resolution fail-fast convention
        in this module): a gated tool built with no ``ToolGate`` supplied
        cannot silently execute ungated -- it must not be buildable."""
        config = _make_manual_tool(requires_approval=True)
        builder = ManualToolBuilder(config)

        with pytest.raises(ValueError, match="requires_approval"):
            builder.build()

    @pytest.mark.anyio
    async def test_non_gated_tool_ignores_a_supplied_tool_gate(self) -> None:
        """A tool_gate may always be threaded through (e.g. by the
        registry, for every manual tool); tools that don't opt in via
        ``requires_approval`` execute immediately, unaffected."""
        config = _make_manual_tool(requires_approval=False)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)
        gate = ToolGate(ApprovalStore())

        builder = ManualToolBuilder(config, http_client=mock_client, tool_gate=gate)
        tool = builder.build()

        result = await tool.function()

        mock_client.request.assert_called_once()
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Test: reserved `{{__now__}}` template token
# ---------------------------------------------------------------------------
#
# `__now__` is a reserved, request-time-resolved template token (see
# `_RESERVED_TEMPLATE_RESOLVERS` in forge_agent.builder.manual). It resolves
# to a fresh ISO-8601 UTC timestamp with millisecond precision and a
# trailing "Z" (e.g. "2026-07-14T10:22:04.123Z") -- the format some APIs
# (e.g. Postiz's `POST /posts` `date` field) require. It must:
#   - be computed fresh at request/execution time, never at build time
#   - work in url, headers, and body_template alike
#   - take precedence over any identically named declared param
#   - never be forwarded as a stray query/body param


_ISO8601_MILLIS_Z_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")


class TestReservedNowTemplateToken:
    """Tests for the reserved `{{__now__}}` template token."""

    @pytest.mark.anyio
    async def test_now_token_resolves_to_valid_iso8601_utc_in_body_template(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/posts",
            method=HTTPMethod.POST,
            body_template={"date": "{{__now__}}"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        before = datetime.now(UTC)
        await tool.function()
        after = datetime.now(UTC)

        call_kwargs = mock_client.request.call_args
        date_value = call_kwargs.kwargs["json"]["date"]

        assert _ISO8601_MILLIS_Z_RE.match(date_value)

        parsed = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert before - timedelta(seconds=2) <= parsed <= after + timedelta(seconds=2)

    @pytest.mark.anyio
    async def test_now_token_resolves_in_url(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/events/{{__now__}}",
            method=HTTPMethod.GET,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        url = call_kwargs.kwargs["url"]
        assert url.startswith("https://api.example.com/events/")
        timestamp = url.removeprefix("https://api.example.com/events/")
        assert _ISO8601_MILLIS_Z_RE.match(timestamp)

    @pytest.mark.anyio
    async def test_now_token_resolves_in_headers_and_tolerates_whitespace(self) -> None:
        config = ManualTool(
            name="timestamped_header_tool",
            description="Sends a timestamp header",
            api=ManualToolAPI(
                url="https://api.example.com/ping",
                method=HTTPMethod.GET,
                headers={"X-Requested-At": "{{ __now__ }}"},
            ),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        header_value = call_kwargs.kwargs["headers"]["X-Requested-At"]
        assert _ISO8601_MILLIS_Z_RE.match(header_value)

    @pytest.mark.anyio
    async def test_now_token_is_freshly_computed_on_each_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two resolutions must differ -- proving `__now__` is computed at
        request time, not cached from build time. The clock is monkeypatched
        (rather than sleeping) so the test is deterministic and fast."""
        fixed_times = iter(
            [
                datetime(2026, 7, 14, 10, 22, 4, 123000, tzinfo=UTC),
                datetime(2026, 7, 14, 10, 22, 9, 456000, tzinfo=UTC),
            ]
        )
        monkeypatch.setattr(manual_module, "_utc_now", lambda: next(fixed_times))

        config = _make_manual_tool(
            url="https://api.example.com/posts",
            method=HTTPMethod.POST,
            body_template={"date": "{{__now__}}"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function()
        first = mock_client.request.call_args.kwargs["json"]["date"]

        await tool.function()
        second = mock_client.request.call_args.kwargs["json"]["date"]

        assert first == "2026-07-14T10:22:04.123Z"
        assert second == "2026-07-14T10:22:09.456Z"
        assert first != second

    @pytest.mark.anyio
    async def test_real_param_substitutes_normally_alongside_now_token(self) -> None:
        config = _make_manual_tool(
            url="https://api.example.com/posts",
            method=HTTPMethod.POST,
            parameters=[ParameterDef(name="content", type=ParamType.STRING)],
            body_template={"content": "{{content}}", "date": "{{__now__}}"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(content="hello world")

        call_kwargs = mock_client.request.call_args
        body = call_kwargs.kwargs["json"]
        assert body["content"] == "hello world"
        assert _ISO8601_MILLIS_Z_RE.match(body["date"])

    @pytest.mark.anyio
    async def test_now_token_not_forwarded_as_extra_query_param(self) -> None:
        """A declared param literally named `__now__` must never leak into
        the query string: the reserved token always wins and is never
        treated as a forwardable declared parameter."""
        config = _make_manual_tool(
            url="https://api.example.com/items",
            method=HTTPMethod.GET,
            parameters=[
                ParameterDef(
                    name="__now__",
                    type=ParamType.STRING,
                    required=False,
                    default="user-supplied",
                ),
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(__now__="user-supplied")

        call_kwargs = mock_client.request.call_args
        assert not call_kwargs.kwargs["params"]

    @pytest.mark.anyio
    async def test_now_token_not_forwarded_as_extra_body_param(self) -> None:
        """Same leak-prevention guarantee for POST/body forwarding: a
        declared param named `__now__` must never end up as a stray body
        field even when there is no explicit body_template."""
        config = _make_manual_tool(
            url="https://api.example.com/items",
            method=HTTPMethod.POST,
            parameters=[
                ParameterDef(
                    name="__now__",
                    type=ParamType.STRING,
                    required=False,
                    default="user-supplied",
                ),
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=mock_response)

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function(__now__="user-supplied")

        call_kwargs = mock_client.request.call_args
        assert not call_kwargs.kwargs["json"]
