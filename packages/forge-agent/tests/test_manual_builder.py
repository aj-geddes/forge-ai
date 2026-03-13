"""Tests for ManualToolBuilder."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from forge_agent.builder.manual import ManualToolBuilder
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
