"""Tests for tool builder auth with SecretResolver integration.

Verifies that OpenAPIToolBuilder and ManualToolBuilder correctly resolve
SecretRef values via SecretResolver for authentication headers, instead
of using hardcoded placeholder strings.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from forge_agent.builder.manual import ManualToolBuilder
from forge_agent.builder.openapi import OpenAPIToolBuilder, _resolve_auth_headers
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import (
    AuthConfig,
    AuthType,
    HTTPMethod,
    ManualTool,
    ManualToolAPI,
    OpenAPISource,
    ParameterDef,
    SecretRef,
    SecretSource,
)
from forge_config.secret_resolver import SecretResolver

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


class FakeSecretResolver:
    """A fake SecretResolver that returns predefined values for testing."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}

    def resolve(self, ref: SecretRef) -> str:
        key = ref.name
        if key not in self._secrets:
            msg = f"Secret '{key}' not found"
            raise SecretResolutionError(msg)
        value = self._secrets[key]
        if not value:
            msg = f"Secret '{key}' resolved to empty value"
            raise SecretResolutionError(msg)
        return value


# Verify FakeSecretResolver satisfies the protocol.
_check: SecretResolver = FakeSecretResolver()


def _make_secret_ref(
    name: str = "MY_TOKEN",
    source: SecretSource = SecretSource.ENV,
    key: str | None = None,
) -> SecretRef:
    """Create a SecretRef for testing."""
    return SecretRef(source=source, name=name, key=key)


def _make_bearer_auth(
    token_name: str = "API_TOKEN",  # noqa: S107
    header_name: str = "Authorization",
) -> AuthConfig:
    """Create a bearer AuthConfig with a SecretRef."""
    return AuthConfig(
        type=AuthType.BEARER,
        token=_make_secret_ref(name=token_name),
        header_name=header_name,
    )


def _make_api_key_auth(
    token_name: str = "MY_API_KEY",  # noqa: S107
    header_name: str = "X-API-Key",
) -> AuthConfig:
    """Create an API key AuthConfig with a SecretRef."""
    return AuthConfig(
        type=AuthType.API_KEY,
        token=_make_secret_ref(name=token_name),
        header_name=header_name,
    )


def _make_basic_auth(
    username_name: str = "SVC_USER",
    password_name: str = "SVC_PASS",  # noqa: S107
) -> AuthConfig:
    """Create a basic AuthConfig with SecretRef credentials."""
    return AuthConfig(
        type=AuthType.BASIC,
        username=_make_secret_ref(name=username_name),
        password=_make_secret_ref(name=password_name),
    )


def _make_openapi_source(
    auth: AuthConfig | None = None,
    url: str = "https://api.example.com/openapi.json",
) -> OpenAPISource:
    """Create an OpenAPISource for testing."""
    return OpenAPISource(
        name="test_api",
        url=url,
        auth=auth or AuthConfig(),
    )


def _make_manual_tool(
    auth: AuthConfig | None = None,
    url: str = "https://api.example.com/data",
    method: HTTPMethod = HTTPMethod.GET,
    parameters: list[ParameterDef] | None = None,
) -> ManualTool:
    """Create a ManualTool config for testing."""
    return ManualTool(
        name="test_tool",
        description="A test tool",
        parameters=parameters or [],
        api=ManualToolAPI(
            url=url,
            method=method,
            auth=auth or AuthConfig(),
        ),
    )


def _minimal_openapi_spec() -> dict[str, Any]:
    """A minimal OpenAPI spec with one GET operation."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "summary": "List items",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


def _multi_endpoint_spec() -> dict[str, Any]:
    """An OpenAPI spec with multiple endpoints for multi-tool tests."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Multi", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "summary": "List items",
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "summary": "Get item",
                    "parameters": [
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/users": {
                "get": {
                    "operationId": "listUsers",
                    "summary": "List users",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }


def _mock_http_response(body: Any = None) -> MagicMock:
    """Create a mock httpx Response."""
    mock = MagicMock()
    mock.json.return_value = body if body is not None else {}
    mock.raise_for_status.return_value = None
    return mock


def _mock_http_client(response: MagicMock | None = None) -> AsyncMock:
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock()
    client.request.return_value = response or _mock_http_response()
    client.aclose.return_value = None
    return client


# ===========================================================================
# Test: _resolve_auth_headers unit tests
# ===========================================================================


class TestResolveAuthHeadersNone:
    """AuthType.NONE should produce no headers and require no resolver."""

    def test_none_returns_empty_dict(self) -> None:
        auth = AuthConfig(type=AuthType.NONE)
        result = _resolve_auth_headers(auth, None)
        assert result == {}

    def test_none_with_resolver_still_empty(self) -> None:
        auth = AuthConfig(type=AuthType.NONE)
        resolver = FakeSecretResolver({"anything": "value"})
        result = _resolve_auth_headers(auth, resolver)
        assert result == {}


class TestResolveAuthHeadersBearer:
    """Bearer auth resolves token via SecretResolver."""

    def test_bearer_resolves_token(self) -> None:
        auth = _make_bearer_auth(token_name="API_TOKEN")
        resolver = FakeSecretResolver({"API_TOKEN": "my-secret-token-123"})
        result = _resolve_auth_headers(auth, resolver)

        assert result == {"Authorization": "Bearer my-secret-token-123"}

    def test_bearer_no_placeholder_in_value(self) -> None:
        auth = _make_bearer_auth(token_name="TOKEN")
        resolver = FakeSecretResolver({"TOKEN": "real-value"})
        result = _resolve_auth_headers(auth, resolver)

        assert "<resolved-token>" not in result["Authorization"]
        assert "placeholder" not in result["Authorization"].lower()

    def test_bearer_custom_header_name(self) -> None:
        auth = AuthConfig(
            type=AuthType.BEARER,
            token=_make_secret_ref(name="TOKEN"),
            header_name="X-Auth-Token",
        )
        resolver = FakeSecretResolver({"TOKEN": "tok-xyz"})
        result = _resolve_auth_headers(auth, resolver)

        assert "X-Auth-Token" in result
        assert result["X-Auth-Token"] == "Bearer tok-xyz"
        assert "Authorization" not in result

    def test_bearer_without_resolver_raises(self) -> None:
        auth = _make_bearer_auth()
        with pytest.raises(SecretResolutionError, match="requires a SecretResolver"):
            _resolve_auth_headers(auth, None)

    def test_bearer_missing_secret_raises(self) -> None:
        auth = _make_bearer_auth(token_name="NONEXISTENT")
        resolver = FakeSecretResolver({})
        with pytest.raises(SecretResolutionError, match="not found"):
            _resolve_auth_headers(auth, resolver)


class TestResolveAuthHeadersApiKey:
    """API key auth resolves key via SecretResolver."""

    def test_api_key_resolves_value(self) -> None:
        auth = _make_api_key_auth(token_name="MY_KEY", header_name="X-API-Key")
        resolver = FakeSecretResolver({"MY_KEY": "key-abc-123"})
        result = _resolve_auth_headers(auth, resolver)

        assert result == {"X-API-Key": "key-abc-123"}

    def test_api_key_no_placeholder_in_value(self) -> None:
        auth = _make_api_key_auth(token_name="KEY")
        resolver = FakeSecretResolver({"KEY": "real-key"})
        result = _resolve_auth_headers(auth, resolver)

        assert "<resolved-api-key>" not in result["X-API-Key"]

    def test_api_key_uses_authorization_header(self) -> None:
        auth = AuthConfig(
            type=AuthType.API_KEY,
            token=_make_secret_ref(name="KEY"),
            header_name="Authorization",
        )
        resolver = FakeSecretResolver({"KEY": "my-api-key"})
        result = _resolve_auth_headers(auth, resolver)

        assert result == {"Authorization": "my-api-key"}

    def test_api_key_without_resolver_raises(self) -> None:
        auth = _make_api_key_auth()
        with pytest.raises(SecretResolutionError, match="requires a SecretResolver"):
            _resolve_auth_headers(auth, None)

    def test_api_key_missing_secret_raises(self) -> None:
        auth = _make_api_key_auth(token_name="MISSING_KEY")
        resolver = FakeSecretResolver({})
        with pytest.raises(SecretResolutionError, match="not found"):
            _resolve_auth_headers(auth, resolver)


class TestResolveAuthHeadersBasic:
    """Basic auth resolves username and password via SecretResolver."""

    def test_basic_resolves_credentials(self) -> None:
        auth = _make_basic_auth(username_name="USER", password_name="PASS")
        resolver = FakeSecretResolver({"USER": "admin", "PASS": "s3cret"})
        result = _resolve_auth_headers(auth, resolver)

        expected_creds = base64.b64encode(b"admin:s3cret").decode()
        assert result == {"Authorization": f"Basic {expected_creds}"}

    def test_basic_no_placeholder_in_value(self) -> None:
        auth = _make_basic_auth()
        resolver = FakeSecretResolver({"SVC_USER": "user", "SVC_PASS": "pass"})
        result = _resolve_auth_headers(auth, resolver)

        assert "<user>" not in result["Authorization"]
        assert "<pass>" not in result["Authorization"]

    def test_basic_without_resolver_raises(self) -> None:
        auth = _make_basic_auth()
        with pytest.raises(SecretResolutionError, match="requires a SecretResolver"):
            _resolve_auth_headers(auth, None)

    def test_basic_missing_username_raises(self) -> None:
        auth = _make_basic_auth(username_name="MISSING_USER", password_name="PASS")
        resolver = FakeSecretResolver({"PASS": "password"})
        with pytest.raises(SecretResolutionError, match="not found"):
            _resolve_auth_headers(auth, resolver)

    def test_basic_missing_password_raises(self) -> None:
        auth = _make_basic_auth(username_name="USER", password_name="MISSING_PASS")
        resolver = FakeSecretResolver({"USER": "admin"})
        with pytest.raises(SecretResolutionError, match="not found"):
            _resolve_auth_headers(auth, resolver)


# ===========================================================================
# Test: OpenAPI builder — Bearer token auth (end-to-end)
# ===========================================================================


class TestOpenAPIBearerAuth:
    """OpenAPI builder resolves bearer token via SecretRef at build time."""

    @pytest.mark.anyio
    async def test_bearer_token_resolved_in_http_call(self) -> None:
        """Resolved bearer token should appear in HTTP request headers."""
        auth = _make_bearer_auth(token_name="API_TOKEN")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"API_TOKEN": "live-token-xyz"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["Authorization"] == "Bearer live-token-xyz"

    @pytest.mark.anyio
    async def test_bearer_no_placeholder_in_header(self) -> None:
        """The Authorization header must not contain placeholder text."""
        auth = _make_bearer_auth(token_name="TOK")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"TOK": "real-value"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "<resolved-token>" not in headers["Authorization"]

    @pytest.mark.anyio
    async def test_bearer_custom_header_name(self) -> None:
        """Bearer auth with a custom header_name should use that header."""
        auth = AuthConfig(
            type=AuthType.BEARER,
            token=_make_secret_ref(name="TOK"),
            header_name="X-Auth-Token",
        )
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"TOK": "abc"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "X-Auth-Token" in headers
        assert headers["X-Auth-Token"] == "Bearer abc"


# ===========================================================================
# Test: OpenAPI builder — API key auth (end-to-end)
# ===========================================================================


class TestOpenAPIApiKeyAuth:
    """OpenAPI builder resolves API key via SecretRef at build time."""

    @pytest.mark.anyio
    async def test_api_key_resolved_in_http_call(self) -> None:
        """Resolved API key should appear in HTTP request headers."""
        auth = _make_api_key_auth(token_name="MY_KEY", header_name="X-API-Key")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"MY_KEY": "key-abc-123"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["X-API-Key"] == "key-abc-123"

    @pytest.mark.anyio
    async def test_api_key_no_placeholder(self) -> None:
        """API key header must not contain placeholder text."""
        auth = _make_api_key_auth(token_name="KEY")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"KEY": "real-key"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "<resolved-api-key>" not in headers["X-API-Key"]


# ===========================================================================
# Test: OpenAPI builder — No auth
# ===========================================================================


class TestOpenAPINoAuth:
    """No auth configured means no auth headers added."""

    @pytest.mark.anyio
    async def test_no_auth_no_headers(self) -> None:
        """When auth type is NONE, no auth headers should be added."""
        source = _make_openapi_source(auth=AuthConfig(type=AuthType.NONE))
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "Authorization" not in headers
        assert "X-API-Key" not in headers

    @pytest.mark.anyio
    async def test_default_auth_is_none(self) -> None:
        """Default AuthConfig should result in no auth headers."""
        source = _make_openapi_source()  # uses default AuthConfig()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "Authorization" not in headers

    @pytest.mark.anyio
    async def test_no_resolver_needed_for_none_auth(self) -> None:
        """AuthType.NONE should not require a SecretResolver."""
        source = _make_openapi_source(auth=AuthConfig(type=AuthType.NONE))
        # No secret_resolver passed.
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()  # Should not raise.

        assert len(tools) == 1


# ===========================================================================
# Test: OpenAPI builder — Secret resolution failure
# ===========================================================================


class TestOpenAPIAuthResolutionFailure:
    """Secret resolution failures should raise clear errors at build time."""

    @pytest.mark.anyio
    async def test_bearer_without_resolver_raises_at_build(self) -> None:
        """Bearer auth with no resolver should raise SecretResolutionError."""
        auth = _make_bearer_auth(token_name="TOKEN")
        source = _make_openapi_source(auth=auth)
        builder = OpenAPIToolBuilder(source)  # No secret_resolver

        with (
            patch.object(
                builder,
                "_fetch_remote_spec",
                new_callable=AsyncMock,
                return_value=_minimal_openapi_spec(),
            ),
            pytest.raises(SecretResolutionError, match="requires a SecretResolver"),
        ):
            await builder.build()

    @pytest.mark.anyio
    async def test_api_key_without_resolver_raises_at_build(self) -> None:
        """API key auth with no resolver should raise SecretResolutionError."""
        auth = _make_api_key_auth(token_name="KEY")
        source = _make_openapi_source(auth=auth)
        builder = OpenAPIToolBuilder(source)

        with (
            patch.object(
                builder,
                "_fetch_remote_spec",
                new_callable=AsyncMock,
                return_value=_minimal_openapi_spec(),
            ),
            pytest.raises(SecretResolutionError, match="requires a SecretResolver"),
        ):
            await builder.build()

    @pytest.mark.anyio
    async def test_missing_secret_raises_at_build(self) -> None:
        """A SecretRef pointing to a nonexistent secret should raise."""
        auth = _make_bearer_auth(token_name="NONEXISTENT_VAR")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({})  # Empty — no secrets available
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with (
            patch.object(
                builder,
                "_fetch_remote_spec",
                new_callable=AsyncMock,
                return_value=_minimal_openapi_spec(),
            ),
            pytest.raises(SecretResolutionError),
        ):
            await builder.build()

    @pytest.mark.anyio
    async def test_basic_without_resolver_raises_at_build(self) -> None:
        """Basic auth with no resolver should raise SecretResolutionError."""
        auth = _make_basic_auth()
        source = _make_openapi_source(auth=auth)
        builder = OpenAPIToolBuilder(source)

        with (
            patch.object(
                builder,
                "_fetch_remote_spec",
                new_callable=AsyncMock,
                return_value=_minimal_openapi_spec(),
            ),
            pytest.raises(SecretResolutionError, match="requires a SecretResolver"),
        ):
            await builder.build()


# ===========================================================================
# Test: Manual builder — Bearer token auth (end-to-end)
# ===========================================================================


class TestManualBearerAuth:
    """Manual builder resolves bearer token via SecretRef at build time."""

    @pytest.mark.anyio
    async def test_bearer_token_resolved_in_http_call(self) -> None:
        """Resolved bearer token should appear in HTTP request headers."""
        auth = _make_bearer_auth(token_name="MY_TOKEN")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"MY_TOKEN": "manual-token-456"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["Authorization"] == "Bearer manual-token-456"

    @pytest.mark.anyio
    async def test_bearer_no_placeholder_in_header(self) -> None:
        """The Authorization header must not contain placeholder text."""
        auth = _make_bearer_auth(token_name="TOK")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"TOK": "actual-value"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "<resolved-token>" not in headers["Authorization"]

    @pytest.mark.anyio
    async def test_bearer_custom_header(self) -> None:
        """Bearer auth with custom header_name should use that header."""
        auth = AuthConfig(
            type=AuthType.BEARER,
            token=_make_secret_ref(name="TOKEN"),
            header_name="X-Bearer",
        )
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"TOKEN": "val"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "X-Bearer" in headers
        assert headers["X-Bearer"] == "Bearer val"


# ===========================================================================
# Test: Manual builder — API key auth (end-to-end)
# ===========================================================================


class TestManualApiKeyAuth:
    """Manual builder resolves API key via SecretRef at build time."""

    @pytest.mark.anyio
    async def test_api_key_resolved_in_http_call(self) -> None:
        """Resolved API key should appear in HTTP request headers."""
        auth = _make_api_key_auth(token_name="SERVICE_KEY", header_name="X-API-Key")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"SERVICE_KEY": "svc-key-789"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["X-API-Key"] == "svc-key-789"

    @pytest.mark.anyio
    async def test_api_key_no_placeholder(self) -> None:
        """API key header must not contain placeholder text."""
        auth = _make_api_key_auth(token_name="KEY")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"KEY": "real-key"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "<resolved-api-key>" not in headers["X-API-Key"]

    @pytest.mark.anyio
    async def test_api_key_custom_header(self) -> None:
        """API key should use the configured custom header name."""
        auth = _make_api_key_auth(header_name="X-Service-Token")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"MY_API_KEY": "custom-val"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "X-Service-Token" in headers
        assert "Authorization" not in headers


# ===========================================================================
# Test: Manual builder — No auth
# ===========================================================================


class TestManualNoAuth:
    """No auth configured means no auth headers added."""

    @pytest.mark.anyio
    async def test_no_auth_no_headers(self) -> None:
        """When auth type is NONE, no auth headers should be present."""
        config = _make_manual_tool(auth=AuthConfig(type=AuthType.NONE))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "Authorization" not in headers
        assert "X-API-Key" not in headers

    @pytest.mark.anyio
    async def test_default_auth_no_headers(self) -> None:
        """Default ManualToolAPI auth (NONE) should produce no auth headers."""
        config = _make_manual_tool()  # uses default AuthConfig()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "Authorization" not in headers


# ===========================================================================
# Test: Manual builder — Secret resolution failure
# ===========================================================================


class TestManualAuthResolutionFailure:
    """Secret resolution failures should raise clear errors at build time."""

    def test_bearer_without_resolver_raises_at_build(self) -> None:
        """Bearer auth with no resolver should raise SecretResolutionError."""
        auth = _make_bearer_auth(token_name="TOKEN")
        config = _make_manual_tool(auth=auth)
        builder = ManualToolBuilder(config)  # No secret_resolver

        with pytest.raises(SecretResolutionError, match="requires a SecretResolver"):
            builder.build()

    def test_api_key_without_resolver_raises_at_build(self) -> None:
        """API key auth with no resolver should raise SecretResolutionError."""
        auth = _make_api_key_auth()
        config = _make_manual_tool(auth=auth)
        builder = ManualToolBuilder(config)

        with pytest.raises(SecretResolutionError, match="requires a SecretResolver"):
            builder.build()

    def test_missing_secret_raises_at_build(self) -> None:
        """A SecretRef pointing to a nonexistent secret should raise."""
        auth = _make_bearer_auth(token_name="MISSING")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({})
        builder = ManualToolBuilder(config, secret_resolver=resolver)

        with pytest.raises(SecretResolutionError):
            builder.build()

    def test_basic_without_resolver_raises_at_build(self) -> None:
        """Basic auth with no resolver should raise SecretResolutionError."""
        auth = _make_basic_auth()
        config = _make_manual_tool(auth=auth)
        builder = ManualToolBuilder(config)

        with pytest.raises(SecretResolutionError, match="requires a SecretResolver"):
            builder.build()


# ===========================================================================
# Test: Multiple tools with different auth configs
# ===========================================================================


class TestMultipleToolsDifferentAuth:
    """Multiple tools with different auth configs should each resolve correctly."""

    @pytest.mark.anyio
    async def test_openapi_all_tools_get_same_auth(self) -> None:
        """All tools from one OpenAPI source share the same resolved auth."""
        auth = _make_bearer_auth(token_name="SHARED_TOKEN")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"SHARED_TOKEN": "shared-value"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_multi_endpoint_spec(),
        ):
            tools = await builder.build()

        assert len(tools) == 3

        for tool in tools:
            mock_client = _mock_http_client()
            with patch(
                "forge_agent.builder.openapi.httpx.AsyncClient",
                return_value=mock_client,
            ):
                if tool.name == "getItem":
                    await tool.function(itemId="42")
                else:
                    await tool.function()

            call_kwargs = mock_client.request.call_args
            headers = call_kwargs.kwargs["headers"]
            assert headers["Authorization"] == "Bearer shared-value", (
                f"Tool {tool.name} has wrong auth header"
            )

    @pytest.mark.anyio
    async def test_manual_tools_independent_auth(self) -> None:
        """Different manual tools can have different auth configurations."""
        resolver = FakeSecretResolver(
            {
                "TOKEN_A": "bearer-a",
                "KEY_B": "apikey-b",
            }
        )

        bearer_tool_config = ManualTool(
            name="bearer_tool",
            description="Uses bearer auth",
            api=ManualToolAPI(
                url="https://api.example.com/a",
                auth=_make_bearer_auth(token_name="TOKEN_A"),
            ),
        )
        apikey_tool_config = ManualTool(
            name="apikey_tool",
            description="Uses API key auth",
            api=ManualToolAPI(
                url="https://api.example.com/b",
                auth=_make_api_key_auth(token_name="KEY_B", header_name="X-API-Key"),
            ),
        )
        noauth_tool_config = ManualTool(
            name="noauth_tool",
            description="No auth",
            api=ManualToolAPI(
                url="https://api.example.com/c",
                auth=AuthConfig(type=AuthType.NONE),
            ),
        )

        # Bearer tool.
        mock_client_a = AsyncMock(spec=httpx.AsyncClient)
        mock_client_a.request = AsyncMock(return_value=_mock_http_response())
        tool_a = ManualToolBuilder(
            bearer_tool_config, http_client=mock_client_a, secret_resolver=resolver
        ).build()
        await tool_a.function()
        headers_a = mock_client_a.request.call_args.kwargs["headers"]
        assert headers_a["Authorization"] == "Bearer bearer-a"

        # API key tool.
        mock_client_b = AsyncMock(spec=httpx.AsyncClient)
        mock_client_b.request = AsyncMock(return_value=_mock_http_response())
        tool_b = ManualToolBuilder(
            apikey_tool_config, http_client=mock_client_b, secret_resolver=resolver
        ).build()
        await tool_b.function()
        headers_b = mock_client_b.request.call_args.kwargs["headers"]
        assert headers_b["X-API-Key"] == "apikey-b"

        # No auth tool.
        mock_client_c = AsyncMock(spec=httpx.AsyncClient)
        mock_client_c.request = AsyncMock(return_value=_mock_http_response())
        tool_c = ManualToolBuilder(noauth_tool_config, http_client=mock_client_c).build()
        await tool_c.function()
        headers_c = mock_client_c.request.call_args.kwargs["headers"]
        assert "Authorization" not in headers_c
        assert "X-API-Key" not in headers_c


# ===========================================================================
# Test: Edge cases
# ===========================================================================


class TestAuthEdgeCases:
    """Edge cases for auth configuration and secret resolution."""

    def test_empty_secret_value_raises(self) -> None:
        """An empty-string resolved secret should raise SecretResolutionError."""
        auth = _make_bearer_auth(token_name="EMPTY_TOKEN")
        resolver = FakeSecretResolver({"EMPTY_TOKEN": ""})

        with pytest.raises(SecretResolutionError, match="empty"):
            _resolve_auth_headers(auth, resolver)

    def test_auth_config_bearer_requires_token_field(self) -> None:
        """AuthConfig with BEARER type but no token should fail validation."""
        with pytest.raises(ValueError, match="token is required"):
            AuthConfig(type=AuthType.BEARER, token=None)

    def test_auth_config_api_key_requires_token_field(self) -> None:
        """AuthConfig with API_KEY type but no token should fail validation."""
        with pytest.raises(ValueError, match="token is required"):
            AuthConfig(type=AuthType.API_KEY, token=None)

    def test_auth_config_basic_requires_both_credentials(self) -> None:
        """AuthConfig with BASIC type requires both username and password."""
        with pytest.raises(ValueError, match="username and password are required"):
            AuthConfig(type=AuthType.BASIC, username=None, password=None)

    def test_auth_config_basic_requires_password(self) -> None:
        """AuthConfig with BASIC type and only username should fail."""
        with pytest.raises(ValueError, match="username and password are required"):
            AuthConfig(
                type=AuthType.BASIC,
                username=_make_secret_ref(name="USER"),
                password=None,
            )

    def test_secret_ref_env_source_valid(self) -> None:
        """A SecretRef with ENV source should be creatable without a key."""
        ref = SecretRef(source=SecretSource.ENV, name="MY_VAR")
        assert ref.source == SecretSource.ENV
        assert ref.name == "MY_VAR"
        assert ref.key is None

    def test_secret_ref_k8s_requires_key(self) -> None:
        """A SecretRef with K8S_SECRET source requires a key field."""
        with pytest.raises(ValueError, match="key is required"):
            SecretRef(source=SecretSource.K8S_SECRET, name="my-secret")

    def test_secret_ref_k8s_with_key_valid(self) -> None:
        """A SecretRef with K8S_SECRET source and key should be valid."""
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="my-secret", key="api-key")
        assert ref.source == SecretSource.K8S_SECRET
        assert ref.key == "api-key"

    @pytest.mark.anyio
    async def test_auth_headers_coexist_with_explicit_headers(self) -> None:
        """Auth headers should be added alongside explicit header parameters."""
        auth = _make_bearer_auth(token_name="TOKEN")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"TOKEN": "tok"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        spec_with_header = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "summary": "List items",
                        "parameters": [
                            {
                                "name": "X-Request-Id",
                                "in": "header",
                                "required": False,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=spec_with_header,
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function(X_Request_Id="req-123")

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        # Both the explicit header param and auth header should be present.
        assert headers["X-Request-Id"] == "req-123"
        assert headers["Authorization"] == "Bearer tok"

    @pytest.mark.anyio
    async def test_manual_auth_headers_coexist_with_config_headers(self) -> None:
        """Auth headers should coexist with headers from ManualToolAPI config."""
        auth = _make_api_key_auth(token_name="KEY", header_name="X-API-Key")
        config = ManualTool(
            name="test",
            description="Test tool",
            api=ManualToolAPI(
                url="https://api.example.com/test",
                auth=auth,
                headers={"X-Custom": "custom-value", "Accept": "application/json"},
            ),
        )
        resolver = FakeSecretResolver({"KEY": "my-key"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["X-API-Key"] == "my-key"
        assert headers["X-Custom"] == "custom-value"
        assert headers["Accept"] == "application/json"

    def test_resolve_auth_headers_returns_dict(self) -> None:
        """_resolve_auth_headers should always return a dict."""
        # NONE
        result = _resolve_auth_headers(AuthConfig(type=AuthType.NONE), None)
        assert isinstance(result, dict)

        # BEARER
        resolver = FakeSecretResolver({"T": "v"})
        result = _resolve_auth_headers(_make_bearer_auth(token_name="T"), resolver)
        assert isinstance(result, dict)

        # API_KEY
        result = _resolve_auth_headers(_make_api_key_auth(token_name="T"), resolver)
        assert isinstance(result, dict)

    @pytest.mark.anyio
    async def test_basic_auth_end_to_end_openapi(self) -> None:
        """Basic auth should produce correct Base64-encoded headers."""
        auth = _make_basic_auth(username_name="USER", password_name="PASS")
        source = _make_openapi_source(auth=auth)
        resolver = FakeSecretResolver({"USER": "admin", "PASS": "s3cret"})
        builder = OpenAPIToolBuilder(source, secret_resolver=resolver)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_minimal_openapi_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        mock_client = _mock_http_client()

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        expected_creds = base64.b64encode(b"admin:s3cret").decode()
        assert headers["Authorization"] == f"Basic {expected_creds}"

    @pytest.mark.anyio
    async def test_basic_auth_end_to_end_manual(self) -> None:
        """Basic auth in manual builder should produce correct Base64 headers."""
        auth = _make_basic_auth(username_name="U", password_name="P")
        config = _make_manual_tool(auth=auth)
        resolver = FakeSecretResolver({"U": "user1", "P": "pass1"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_http_response())

        builder = ManualToolBuilder(config, http_client=mock_client, secret_resolver=resolver)
        tool = builder.build()

        await tool.function()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        expected_creds = base64.b64encode(b"user1:pass1").decode()
        assert headers["Authorization"] == f"Basic {expected_creds}"
        assert "<user>" not in headers["Authorization"]
        assert "<pass>" not in headers["Authorization"]
