"""Tests for OpenAPIToolBuilder."""

from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from forge_agent.builder.openapi import (
    OpenAPIToolBuilder,
    _resolve_auth_headers,
    _sanitize_name,
)
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import AuthConfig, AuthType, OpenAPISource, SecretRef, SecretSource

# ---------------------------------------------------------------------------
# Shared test spec fixtures
# ---------------------------------------------------------------------------


def _make_petstore_spec() -> dict[str, Any]:
    """A minimal petstore-like OpenAPI 3.0 spec for testing."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Petstore", "version": "1.0.0"},
        "servers": [{"url": "https://petstore.example.com/v1"}],
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "summary": "List all pets",
                    "tags": ["pets"],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "default": 20},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "operationId": "createPet",
                    "summary": "Create a pet",
                    "tags": ["pets"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "summary": "Get a pet by ID",
                    "tags": ["pets"],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "operationId": "deletePet",
                    "summary": "Delete a pet",
                    "tags": ["pets", "admin"],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/users": {
                "get": {
                    "operationId": "listUsers",
                    "summary": "List all users",
                    "tags": ["users"],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/health": {
                "get": {
                    "operationId": "healthCheck",
                    "summary": "Health check",
                    "tags": ["system"],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }


def _make_source(
    *,
    url: str = "https://petstore.example.com/openapi.json",
    prefix: str | None = None,
    namespace: str | None = None,
    include_tags: list[str] | None = None,
    include_operations: list[str] | None = None,
    route_map: dict[str, str] | None = None,
    auth: AuthConfig | None = None,
) -> OpenAPISource:
    """Create an OpenAPISource for testing."""
    return OpenAPISource(
        name="test_api",
        url=url,
        prefix=prefix,
        namespace=namespace,
        include_tags=include_tags or [],
        include_operations=include_operations or [],
        route_map=route_map or {},
        auth=auth or AuthConfig(),
    )


# ---------------------------------------------------------------------------
# Test: building tools from a spec (mock the fetch)
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderBasic:
    """Tests for basic tool building from an inline spec."""

    @pytest.mark.anyio
    async def test_build_from_spec_creates_tools(self) -> None:
        """Build tools from a spec and verify correct count."""
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        # 6 operations: listPets, createPet, getPet, deletePet,
        # listUsers, healthCheck.
        assert len(tools) == 6

    @pytest.mark.anyio
    async def test_tool_names_from_operation_ids(self) -> None:
        """Tool names should come from operationId."""
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert "listPets" in names
        assert "createPet" in names
        assert "getPet" in names
        assert "deletePet" in names
        assert "listUsers" in names
        assert "healthCheck" in names

    @pytest.mark.anyio
    async def test_tool_descriptions_from_summary(self) -> None:
        """Tool descriptions should use the operation summary."""
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tools_by_name = {t.name: t for t in tools}
        # Check the underlying function's docstring.
        list_pets_fn = tools_by_name["listPets"].function
        assert list_pets_fn.__doc__ == "List all pets"


# ---------------------------------------------------------------------------
# Test: namespace/prefix
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderPrefix:
    """Tests for namespace/prefix application."""

    @pytest.mark.anyio
    async def test_prefix_applied_to_tool_names(self) -> None:
        """Tool names should be prefixed with the namespace/prefix."""
        source = _make_source(prefix="petstore")
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert "petstore_listPets" in names
        assert "petstore_createPet" in names
        assert "petstore_getPet" in names

    @pytest.mark.anyio
    async def test_namespace_syncs_to_prefix(self) -> None:
        """Namespace should be used as prefix."""
        source = _make_source(namespace="ns")
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert all(name.startswith("ns_") for name in names)


# ---------------------------------------------------------------------------
# Test: include_tags filtering
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderTagFilter:
    """Tests for tag-based filtering."""

    @pytest.mark.anyio
    async def test_include_tags_filters_operations(self) -> None:
        """Only operations with matching tags should be included."""
        source = _make_source(include_tags=["pets"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        # 4 ops tagged "pets": listPets, createPet, getPet, deletePet.
        assert len(tools) == 4
        assert "listPets" in names
        assert "createPet" in names
        assert "getPet" in names
        assert "deletePet" in names
        # listUsers (tagged "users") and healthCheck (tagged "system")
        # should be excluded.
        assert "listUsers" not in names
        assert "healthCheck" not in names

    @pytest.mark.anyio
    async def test_include_tags_multiple(self) -> None:
        """Multiple tags should match any operation having at least one."""
        source = _make_source(include_tags=["users", "system"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert "listUsers" in names
        assert "healthCheck" in names
        assert len(tools) == 2

    @pytest.mark.anyio
    async def test_include_tags_admin_includes_multi_tagged_ops(self) -> None:
        """An operation tagged with both 'pets' and 'admin' should match 'admin'."""
        source = _make_source(include_tags=["admin"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert "deletePet" in names
        assert len(tools) == 1


# ---------------------------------------------------------------------------
# Test: include_operations filtering
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderOperationFilter:
    """Tests for operation ID-based filtering."""

    @pytest.mark.anyio
    async def test_include_operations_filters(self) -> None:
        """Only operations with matching IDs should be included."""
        source = _make_source(include_operations=["listPets", "getPet"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert names == {"listPets", "getPet"}

    @pytest.mark.anyio
    async def test_include_operations_single(self) -> None:
        """Single operation filter should return exactly one tool."""
        source = _make_source(include_operations=["healthCheck"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        assert len(tools) == 1
        assert tools[0].name == "healthCheck"

    @pytest.mark.anyio
    async def test_include_tags_and_operations_union(self) -> None:
        """When both are set, operations matching either should be included."""
        source = _make_source(
            include_tags=["system"],
            include_operations=["listPets"],
        )
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert "healthCheck" in names  # matched by tag
        assert "listPets" in names  # matched by operation ID
        assert len(tools) == 2


# ---------------------------------------------------------------------------
# Test: route_map renaming
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderRouteMap:
    """Tests for route_map-based tool renaming."""

    @pytest.mark.anyio
    async def test_route_map_renames_tools(self) -> None:
        """Operations matching route_map entries should be renamed."""
        source = _make_source(
            route_map={
                "GET /pets": "fetch_all_pets",
                "POST /pets": "add_pet",
            },
        )
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        # Renamed operations.
        assert "fetch_all_pets" in names
        assert "add_pet" in names
        # Operations not in route_map keep their operationId.
        assert "getPet" in names
        assert "deletePet" in names
        assert "listUsers" in names
        assert "healthCheck" in names
        # Original names should NOT appear for renamed ones.
        assert "listPets" not in names
        assert "createPet" not in names

    @pytest.mark.anyio
    async def test_route_map_with_prefix(self) -> None:
        """Route map renaming should work together with prefix."""
        source = _make_source(
            prefix="api",
            route_map={"GET /pets": "all_pets"},
        )
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        names = {t.name for t in tools}
        assert "api_all_pets" in names
        assert "api_getPet" in names


# ---------------------------------------------------------------------------
# Test: generated tool signatures
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderSignatures:
    """Tests for function signature generation."""

    @pytest.mark.anyio
    async def test_query_parameter_in_signature(self) -> None:
        """Query params should appear as optional keyword-only params."""
        source = _make_source(include_operations=["listPets"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        sig = inspect.signature(tool.function)
        assert "limit" in sig.parameters
        param = sig.parameters["limit"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.annotation is int
        assert param.default == 20  # from schema default

    @pytest.mark.anyio
    async def test_path_parameter_in_signature(self) -> None:
        """Path params should appear as required keyword-only params."""
        source = _make_source(include_operations=["getPet"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        sig = inspect.signature(tool.function)
        assert "petId" in sig.parameters
        param = sig.parameters["petId"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.annotation is int
        assert param.default is inspect.Parameter.empty  # required

    @pytest.mark.anyio
    async def test_request_body_in_signature(self) -> None:
        """Operations with requestBody should have a 'body' param."""
        source = _make_source(include_operations=["createPet"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        sig = inspect.signature(tool.function)
        assert "body" in sig.parameters
        param = sig.parameters["body"]
        assert param.annotation is dict
        assert param.default is inspect.Parameter.empty  # required body

    @pytest.mark.anyio
    async def test_no_body_when_not_specified(self) -> None:
        """GET operations should not have a 'body' param."""
        source = _make_source(include_operations=["listPets"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        sig = inspect.signature(tool.function)
        assert "body" not in sig.parameters


# ---------------------------------------------------------------------------
# Test: HTTP calls with auth
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderHTTPCalls:
    """Tests that generated tools make proper HTTP calls."""

    @pytest.mark.anyio
    async def test_tool_makes_get_call(self) -> None:
        """A generated GET tool should make an HTTP GET request."""
        source = _make_source(include_operations=["listPets"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        fn = tool.function

        # Use MagicMock for response (httpx Response.json() is sync).
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": 1, "name": "Fido"}]
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            result = await fn(limit=10)

        assert result == [{"id": 1, "name": "Fido"}]
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["method"] == "GET"
        assert "petstore.example.com/v1/pets" in call_kwargs.kwargs["url"]
        assert call_kwargs.kwargs["params"] == {"limit": 10}

    @pytest.mark.anyio
    async def test_tool_makes_post_call_with_body(self) -> None:
        """A POST tool should send the body as JSON."""
        source = _make_source(include_operations=["createPet"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        fn = tool.function

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 42, "name": "Rex"}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            result = await fn(body={"name": "Rex"})

        assert result["name"] == "Rex"
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["method"] == "POST"
        assert call_kwargs.kwargs["json"] == {"name": "Rex"}

    @pytest.mark.anyio
    async def test_tool_resolves_path_params(self) -> None:
        """Path parameters should be substituted into the URL."""
        source = _make_source(include_operations=["getPet"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        fn = tool.function

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 7, "name": "Whiskers"}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            result = await fn(petId=7)

        assert result["id"] == 7
        call_kwargs = mock_client.request.call_args
        assert "/pets/7" in call_kwargs.kwargs["url"]

    @pytest.mark.anyio
    async def test_bearer_auth_headers_applied(self) -> None:
        """Bearer auth should resolve the token and add an Authorization header."""
        source = _make_source(
            include_operations=["listPets"],
            auth=AuthConfig(
                type=AuthType.BEARER,
                token=SecretRef(source=SecretSource.ENV, name="API_TOKEN"),
            ),
        )

        class StubResolver:
            def resolve(self, ref: SecretRef) -> str:
                return "my-secret-token"

        builder = OpenAPIToolBuilder(source, secret_resolver=StubResolver())

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        fn = tool.function

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await fn()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer my-secret-token"

    @pytest.mark.anyio
    async def test_api_key_auth_headers_applied(self) -> None:
        """API key auth should resolve the key and add the configured header."""
        source = _make_source(
            include_operations=["listPets"],
            auth=AuthConfig(
                type=AuthType.API_KEY,
                token=SecretRef(source=SecretSource.ENV, name="MY_KEY"),
                header_name="X-API-Key",
            ),
        )

        class StubResolver:
            def resolve(self, ref: SecretRef) -> str:
                return "my-api-key-value"

        builder = OpenAPIToolBuilder(source, secret_resolver=StubResolver())

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        tool = tools[0]
        fn = tool.function

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await fn()

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "X-API-Key" in headers
        assert headers["X-API-Key"] == "my-api-key-value"

    @pytest.mark.anyio
    async def test_auth_without_resolver_raises(self) -> None:
        """Auth configured without a SecretResolver should fail fast at build time."""
        source = _make_source(
            include_operations=["listPets"],
            auth=AuthConfig(
                type=AuthType.BEARER,
                token=SecretRef(source=SecretSource.ENV, name="API_TOKEN"),
            ),
        )
        builder = OpenAPIToolBuilder(source)  # No resolver

        with (
            patch.object(
                builder,
                "_fetch_remote_spec",
                new_callable=AsyncMock,
                return_value=_make_petstore_spec(),
            ),
            pytest.raises(SecretResolutionError, match="SecretResolver"),
        ):
            await builder.build()

    @pytest.mark.anyio
    async def test_auth_resolution_failure_raises(self) -> None:
        """A resolver that fails should propagate the error at build time."""
        source = _make_source(
            include_operations=["listPets"],
            auth=AuthConfig(
                type=AuthType.BEARER,
                token=SecretRef(source=SecretSource.ENV, name="MISSING_TOKEN"),
            ),
        )

        class FailingResolver:
            def resolve(self, ref: SecretRef) -> str:
                msg = f"Secret '{ref.name}' not found"
                raise SecretResolutionError(msg)

        builder = OpenAPIToolBuilder(source, secret_resolver=FailingResolver())

        with (
            patch.object(
                builder,
                "_fetch_remote_spec",
                new_callable=AsyncMock,
                return_value=_make_petstore_spec(),
            ),
            pytest.raises(SecretResolutionError, match="MISSING_TOKEN"),
        ):
            await builder.build()

    @pytest.mark.anyio
    async def test_injected_http_client_used(self) -> None:
        """An injected httpx client should be used instead of creating one."""
        source = _make_source(include_operations=["listPets"])

        # For spec fetch: MagicMock response (json() is sync in httpx).
        mock_spec_response = MagicMock()
        mock_spec_response.json.return_value = _make_petstore_spec()
        mock_spec_response.raise_for_status.return_value = None

        # For tool call: MagicMock response.
        mock_call_response = MagicMock()
        mock_call_response.json.return_value = []
        mock_call_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_spec_response
        mock_client.request.return_value = mock_call_response

        builder = OpenAPIToolBuilder(source, http_client=mock_client)
        tools = await builder.build()
        tool = tools[0]
        await tool.function()

        # The injected client should have been used for the request.
        mock_client.request.assert_called_once()


# ---------------------------------------------------------------------------
# Test: base URL extraction
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderBaseURL:
    """Tests for base URL extraction logic."""

    @pytest.mark.anyio
    async def test_base_url_from_servers(self) -> None:
        """Base URL should come from the spec's servers list."""
        source = _make_source(include_operations=["listPets"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        fn = tools[0].function
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await fn()

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["url"].startswith("https://petstore.example.com/v1")

    @pytest.mark.anyio
    async def test_base_url_fallback_to_source_url(self) -> None:
        """Without servers, base URL should fall back to the source URL."""
        spec_no_servers = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
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
        source = _make_source(url="https://myapi.com/openapi.json")
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=spec_no_servers,
        ):
            tools = await builder.build()

        fn = tools[0].function
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.aclose.return_value = None

        with patch("forge_agent.builder.openapi.httpx.AsyncClient", return_value=mock_client):
            await fn()

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["url"].startswith("https://myapi.com")


# ---------------------------------------------------------------------------
# Test: sanitize_name utility
# ---------------------------------------------------------------------------


class TestSanitizeName:
    """Tests for the _sanitize_name helper."""

    def test_basic(self) -> None:
        assert _sanitize_name("listPets") == "listPets"

    def test_hyphens_to_underscores(self) -> None:
        assert _sanitize_name("list-pets") == "list_pets"

    def test_slashes_to_underscores(self) -> None:
        assert _sanitize_name("get_/users/{id}") == "get__users__id"

    def test_strips_leading_trailing_underscores(self) -> None:
        assert _sanitize_name("__hello__") == "hello"


# ---------------------------------------------------------------------------
# Test: local file loading
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderLocalFile:
    """Tests for loading specs from local files."""

    @pytest.mark.anyio
    async def test_load_local_json_spec(self, tmp_path: Any) -> None:
        """Should load and parse a local JSON spec file."""
        spec = _make_petstore_spec()
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec))

        source = OpenAPISource(
            name="local_test",
            path=str(spec_file),
        )
        builder = OpenAPIToolBuilder(source)
        tools = await builder.build()

        assert len(tools) == 6
        names = {t.name for t in tools}
        assert "listPets" in names

    @pytest.mark.anyio
    async def test_load_local_yaml_spec(self, tmp_path: Any) -> None:
        """Should load and parse a local YAML spec file."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        spec = _make_petstore_spec()
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec))

        source = OpenAPISource(
            name="local_yaml",
            path=str(spec_file),
        )
        builder = OpenAPIToolBuilder(source)
        tools = await builder.build()

        assert len(tools) == 6


# ---------------------------------------------------------------------------
# Test: empty/edge cases
# ---------------------------------------------------------------------------


class TestOpenAPIToolBuilderEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.anyio
    async def test_empty_paths_returns_no_tools(self) -> None:
        """A spec with no paths should yield zero tools."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Empty", "version": "1.0"},
            "paths": {},
        }
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=spec,
        ):
            tools = await builder.build()

        assert len(tools) == 0

    @pytest.mark.anyio
    async def test_no_source_raises(self) -> None:
        """An OpenAPI source without url or path should raise ValueError."""
        # We can't create an invalid OpenAPISource due to the validator,
        # but we can test _load_spec directly.
        source = _make_source()
        builder = OpenAPIToolBuilder(source)
        # Clear both url and path on the source to simulate an invalid state.
        builder._source = OpenAPISource.__new__(OpenAPISource)
        object.__setattr__(builder._source, "url", None)
        object.__setattr__(builder._source, "path", None)
        object.__setattr__(builder._source, "spec", None)
        object.__setattr__(builder._source, "name", "broken")
        object.__setattr__(builder._source, "route_map", {})
        object.__setattr__(builder._source, "auth", AuthConfig())
        object.__setattr__(builder._source, "prefix", None)
        object.__setattr__(builder._source, "namespace", None)
        object.__setattr__(builder._source, "include_tags", [])
        object.__setattr__(builder._source, "include_operations", [])

        with pytest.raises(ValueError, match="no url or path"):
            await builder._load_spec()

    @pytest.mark.anyio
    async def test_filter_with_no_matching_tags_returns_empty(self) -> None:
        """Filtering by non-existent tags should return no tools."""
        source = _make_source(include_tags=["nonexistent"])
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=_make_petstore_spec(),
        ):
            tools = await builder.build()

        assert len(tools) == 0

    @pytest.mark.anyio
    async def test_operation_without_operation_id(self) -> None:
        """Operations without operationId should get a generated name."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "servers": [{"url": "https://example.com"}],
            "paths": {
                "/items": {
                    "get": {
                        "summary": "List items",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=spec,
        ):
            tools = await builder.build()

        assert len(tools) == 1
        # Generated name from "get_/items" sanitized.
        assert tools[0].name == "get__items"

    @pytest.mark.anyio
    async def test_path_level_parameters_inherited(self) -> None:
        """Path-level parameters should be inherited by operations."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "servers": [{"url": "https://example.com"}],
            "paths": {
                "/items/{itemId}": {
                    "parameters": [
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "get": {
                        "operationId": "getItem",
                        "summary": "Get item",
                        "responses": {"200": {"description": "OK"}},
                    },
                }
            },
        }
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=spec,
        ):
            tools = await builder.build()

        sig = inspect.signature(tools[0].function)
        assert "itemId" in sig.parameters
        assert sig.parameters["itemId"].annotation is str

    @pytest.mark.anyio
    async def test_header_params_in_signature(self) -> None:
        """Header parameters should appear in the function signature."""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "servers": [{"url": "https://example.com"}],
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
        source = _make_source()
        builder = OpenAPIToolBuilder(source)

        with patch.object(
            builder,
            "_fetch_remote_spec",
            new_callable=AsyncMock,
            return_value=spec,
        ):
            tools = await builder.build()

        sig = inspect.signature(tools[0].function)
        # X-Request-Id gets sanitized to X_Request_Id.
        assert "X_Request_Id" in sig.parameters


# ---------------------------------------------------------------------------
# Test: _resolve_auth_headers unit tests
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


class TestResolveAuthHeaders:
    """Unit tests for _resolve_auth_headers."""

    def test_none_auth_returns_empty_dict(self) -> None:
        """AuthType.NONE should produce no headers."""
        auth = AuthConfig(type=AuthType.NONE)
        result = _resolve_auth_headers(auth, None)
        assert result == {}

    def test_bearer_resolves_token(self) -> None:
        """Bearer auth should resolve the token SecretRef."""
        auth = AuthConfig(
            type=AuthType.BEARER,
            token=SecretRef(source=SecretSource.ENV, name="TOKEN"),
        )
        resolver = _StubResolver({"TOKEN": "abc123"})
        result = _resolve_auth_headers(auth, resolver)
        assert result == {"Authorization": "Bearer abc123"}

    def test_api_key_resolves_token(self) -> None:
        """API key auth should resolve the token SecretRef."""
        auth = AuthConfig(
            type=AuthType.API_KEY,
            token=SecretRef(source=SecretSource.ENV, name="KEY"),
            header_name="X-Api-Key",
        )
        resolver = _StubResolver({"KEY": "secret-key"})
        result = _resolve_auth_headers(auth, resolver)
        assert result == {"X-Api-Key": "secret-key"}

    def test_basic_resolves_username_and_password(self) -> None:
        """Basic auth should resolve both username and password."""
        import base64

        auth = AuthConfig(
            type=AuthType.BASIC,
            username=SecretRef(source=SecretSource.ENV, name="USER"),
            password=SecretRef(source=SecretSource.ENV, name="PASS"),
        )
        resolver = _StubResolver({"USER": "admin", "PASS": "s3cret"})
        result = _resolve_auth_headers(auth, resolver)
        expected_creds = base64.b64encode(b"admin:s3cret").decode()
        assert result == {"Authorization": f"Basic {expected_creds}"}

    def test_no_resolver_with_auth_raises(self) -> None:
        """Auth requiring secrets without a resolver should raise."""
        auth = AuthConfig(
            type=AuthType.BEARER,
            token=SecretRef(source=SecretSource.ENV, name="TOKEN"),
        )
        with pytest.raises(SecretResolutionError, match="SecretResolver"):
            _resolve_auth_headers(auth, None)

    def test_missing_secret_raises(self) -> None:
        """A resolver that cannot find the secret should raise."""
        auth = AuthConfig(
            type=AuthType.API_KEY,
            token=SecretRef(source=SecretSource.ENV, name="MISSING"),
            header_name="X-Api-Key",
        )
        resolver = _StubResolver({})
        with pytest.raises(SecretResolutionError, match="MISSING"):
            _resolve_auth_headers(auth, resolver)
