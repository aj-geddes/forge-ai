"""Tests for the MCP tool surface route module.

Covers:
1. MCP server is created with correct name
2. Tools from ToolSurfaceRegistry are registered as MCP tools
3. MCP endpoint is accessible at the expected path
4. MCP tool execution calls the underlying agent tool function
5. Empty tool registry -> MCP server with no tools (not an error)
6. Tool with parameters -> MCP tool accepts correct parameters
7. Tool execution error -> proper error response
8. MCP server reflects current tool surface
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from forge_gateway.routes import mcp
from forge_gateway.routes.mcp import (
    build_mcp_server,
    create_mcp_server,
    get_mcp_server,
    rebuild_mcp_server,
    register_tools_from_registry,
)
from pydantic_ai.tools import Tool as PydanticAITool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pai_tool(
    name: str,
    description: str = "",
    fn: Any = None,
) -> PydanticAITool[None]:
    """Create a PydanticAI Tool wrapping a given (or default async) function."""
    if fn is None:

        async def _default_fn(*, query: str) -> str:
            return f"{name}: {query}"

        _default_fn.__name__ = name
        _default_fn.__qualname__ = name
        if description:
            _default_fn.__doc__ = description
        fn = _default_fn

    return PydanticAITool(fn, name=name, description=description or f"Tool: {name}")


def _make_sync_pai_tool(
    name: str,
    fn: Any,
    description: str = "",
) -> PydanticAITool[None]:
    """Create a PydanticAI Tool wrapping a synchronous function."""
    return PydanticAITool(fn, name=name, description=description or f"Tool: {name}")


def _make_registry(tools: list[PydanticAITool[None]] | None = None) -> MagicMock:
    """Create a mock ToolSurfaceRegistry with optional tools."""
    registry = MagicMock()
    registry.tools = list(tools) if tools else []
    registry.tool_count = len(registry.tools)
    return registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> Iterator[None]:
    """Reset the module-level MCP server after each test."""
    yield
    mcp._mcp_server = None


# ---------------------------------------------------------------------------
# 1. MCP server is created with correct name
# ---------------------------------------------------------------------------


class TestMCPServerCreation:
    """create_mcp_server() and build_mcp_server() produce correctly named servers."""

    def test_creates_fastmcp_instance(self) -> None:
        server = create_mcp_server("TestServer")
        assert isinstance(server, FastMCP)

    def test_server_has_given_name(self) -> None:
        server = create_mcp_server("MyAgent")
        assert server.name == "MyAgent"

    def test_default_name(self) -> None:
        server = create_mcp_server()
        assert server.name == "Forge AI"

    def test_build_mcp_server_uses_name(self) -> None:
        """build_mcp_server() passes the name through to the FastMCP server."""
        registry = _make_registry()
        server = build_mcp_server(registry, name="Forge Test")
        assert server.name == "Forge Test"

    def test_build_mcp_server_default_name(self) -> None:
        """build_mcp_server() defaults to 'Forge AI' when no name is given."""
        registry = _make_registry()
        server = build_mcp_server(registry)
        assert server.name == "Forge AI"


# ---------------------------------------------------------------------------
# 2. Tools from ToolSurfaceRegistry are registered as MCP tools
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """PydanticAI tools from the registry become MCP tools with name and description."""

    async def test_single_tool_registered(self) -> None:
        """A single PydanticAI tool in the registry becomes an MCP tool."""
        server = create_mcp_server()
        tool = _make_pai_tool("search", "Search for items")
        registry = _make_registry([tool])

        count = register_tools_from_registry(server, registry)

        assert count == 1
        tools = await server.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert tools[0].description == "Search for items"

    async def test_multiple_tools_registered(self) -> None:
        """Multiple PydanticAI tools are all registered as MCP tools."""
        server = create_mcp_server()
        tools = [
            _make_pai_tool("search", "Search things"),
            _make_pai_tool("analyze", "Analyze data"),
            _make_pai_tool("summarize", "Summarize text"),
        ]
        registry = _make_registry(tools)

        count = register_tools_from_registry(server, registry)

        assert count == 3
        mcp_tools = await server.list_tools()
        names = {t.name for t in mcp_tools}
        assert names == {"search", "analyze", "summarize"}

    async def test_tool_description_preserved(self) -> None:
        """The PydanticAI tool description is preserved on the MCP tool."""
        server = create_mcp_server()
        tool = _make_pai_tool("lookup", "Look up records in the database")
        registry = _make_registry([tool])

        register_tools_from_registry(server, registry)
        mcp_tools = await server.list_tools()

        assert mcp_tools[0].description == "Look up records in the database"

    def test_register_tools_returns_count(self) -> None:
        """register_tools_from_registry() returns the number of tools registered."""
        tools = [_make_pai_tool(f"tool_{i}") for i in range(5)]
        registry = _make_registry(tools)
        server = create_mcp_server()

        count = register_tools_from_registry(server, registry)
        assert count == 5

    async def test_tool_with_complex_signature(self) -> None:
        """Tools with multi-parameter signatures are registered correctly."""

        async def complex_fn(*, name: str, count: int, active: bool) -> dict[str, Any]:
            return {"name": name, "count": count, "active": active}

        pai_tool = PydanticAITool(complex_fn, name="complex", description="Complex tool")
        server = create_mcp_server()

        mcp._register_single_tool(server, pai_tool)

        tools = await server.list_tools()
        assert tools[0].name == "complex"
        result = await server.call_tool("complex", {"name": "test", "count": 5, "active": True})
        assert any("test" in str(item) for item in result)


# ---------------------------------------------------------------------------
# 3. MCP endpoint is accessible at the expected path
# ---------------------------------------------------------------------------


class TestMCPEndpointMount:
    """The MCP server produces an ASGI app that can be mounted at /mcp."""

    def test_returns_asgi_app(self) -> None:
        """get_mcp_asgi_app() returns a Starlette ASGI app."""
        server = create_mcp_server()
        app = mcp.get_mcp_asgi_app(server)
        assert hasattr(app, "routes")

    def test_asgi_app_is_callable(self) -> None:
        """The returned ASGI app is callable (as required by the ASGI spec)."""
        server = create_mcp_server()
        app = mcp.get_mcp_asgi_app(server)
        assert callable(app)

    def test_mcp_server_mounted_at_mcp_path(self) -> None:
        """The MCP ASGI app can be mounted at /mcp on a FastAPI instance."""
        from fastapi import FastAPI

        app = FastAPI()
        registry = _make_registry()
        server = build_mcp_server(registry)
        asgi_app = mcp.get_mcp_asgi_app(server)
        app.mount("/mcp", asgi_app, name="mcp")

        mount_names = [r.name for r in app.routes if hasattr(r, "name")]
        assert "mcp" in mount_names

    def test_init_mcp_server_mounts_on_app(self) -> None:
        """_init_mcp_server() builds and mounts the MCP app at /mcp."""
        from fastapi import FastAPI
        from forge_gateway.app import _init_mcp_server

        mock_registry = _make_registry([_make_pai_tool("test_tool")])
        mock_agent = MagicMock()
        mock_agent.registry = mock_registry

        mock_config = MagicMock()
        mock_config.metadata.name = "TestForge"

        app = FastAPI()

        with (
            patch("forge_gateway.app.mcp.build_mcp_server") as mock_build,
            patch("forge_gateway.app.mcp.get_mcp_asgi_app") as mock_get_app,
            patch("forge_agent.ForgeAgent", new=type(mock_agent)),
            patch("forge_config.schema.ForgeConfig", new=type(mock_config)),
        ):
            mock_mcp_server = MagicMock()
            mock_build.return_value = mock_mcp_server
            mock_asgi_app = MagicMock()
            mock_get_app.return_value = mock_asgi_app

            _init_mcp_server(app, mock_agent, mock_config)

            mock_build.assert_called_once()
            mock_get_app.assert_called_once_with(mock_mcp_server)

    def test_init_mcp_server_skips_when_no_agent(self) -> None:
        """_init_mcp_server() gracefully skips when agent is None."""
        from fastapi import FastAPI
        from forge_gateway.app import _init_mcp_server

        app = FastAPI()
        _init_mcp_server(app, None, None)
        assert get_mcp_server() is None

    def test_init_mcp_server_handles_import_error(self) -> None:
        """_init_mcp_server() degrades gracefully when imports fail."""
        from fastapi import FastAPI
        from forge_gateway.app import _init_mcp_server

        app = FastAPI()

        with patch(
            "forge_gateway.app.mcp.build_mcp_server",
            side_effect=ImportError("no module"),
        ):
            _init_mcp_server(app, MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# 4. MCP tool execution calls the underlying agent tool function
# ---------------------------------------------------------------------------


class TestMCPToolExecution:
    """Calling an MCP tool invokes the original PydanticAI tool function."""

    async def test_execution_calls_underlying_function(self) -> None:
        """Calling an MCP tool invokes the original function with correct args."""
        call_log: list[dict[str, Any]] = []

        def greet(name: str) -> str:
            """Greet a person."""
            call_log.append({"name": name})
            return f"Hello, {name}!"

        tool = _make_sync_pai_tool("greet", greet, "Greet a person")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        result = await server.call_tool("greet", {"name": "Alice"})

        assert len(call_log) == 1
        assert call_log[0]["name"] == "Alice"
        assert result.content[0].text == "Hello, Alice!"

    async def test_execution_with_multiple_params(self) -> None:
        """MCP tool correctly passes multiple parameters to the underlying function."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        tool = _make_sync_pai_tool("add", add, "Add two numbers")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        result = await server.call_tool("add", {"a": 3, "b": 7})
        assert result.content[0].text == "10"

    async def test_execution_returns_result_content(self) -> None:
        """MCP tool execution returns the function's return value as text content."""

        def echo(message: str) -> str:
            """Echo a message."""
            return f"Echo: {message}"

        tool = _make_sync_pai_tool("echo", echo, "Echo a message back")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        result = await server.call_tool("echo", {"message": "hello"})
        assert "Echo: hello" in result.content[0].text

    async def test_async_tool_function_execution(self) -> None:
        """An async PydanticAI tool function is correctly invoked via MCP."""
        call_count = 0

        async def async_fetch(*, query: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"fetched: {query}"

        tool = _make_pai_tool("async_fetch", "Fetch data asynchronously", fn=async_fetch)
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        result = await server.call_tool("async_fetch", {"query": "test"})
        assert call_count == 1
        assert "fetched: test" in result.content[0].text

    async def test_tool_called_exactly_once_per_invocation(self) -> None:
        """Each call_tool invocation calls the underlying function exactly once."""
        call_count = 0

        def counter() -> str:
            """Count calls."""
            nonlocal call_count
            call_count += 1
            return "ok"

        tool = _make_sync_pai_tool("counter", counter, "Counter tool")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        await server.call_tool("counter", {})
        assert call_count == 1

        await server.call_tool("counter", {})
        assert call_count == 2


# ---------------------------------------------------------------------------
# 5. Empty tool registry -> MCP server with no tools (not an error)
# ---------------------------------------------------------------------------


class TestEmptyRegistry:
    """An empty ToolSurfaceRegistry produces a valid MCP server with zero tools."""

    async def test_empty_registry_produces_server_with_no_tools(self) -> None:
        registry = _make_registry()
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        assert len(mcp_tools) == 0

    def test_empty_registry_does_not_raise(self) -> None:
        """Building an MCP server from an empty registry does not raise."""
        registry = _make_registry()
        server = build_mcp_server(registry)
        assert isinstance(server, FastMCP)

    def test_register_returns_zero_for_empty(self) -> None:
        """register_tools_from_registry() returns 0 for an empty registry."""
        registry = _make_registry()
        server = create_mcp_server()

        count = register_tools_from_registry(server, registry)
        assert count == 0

    async def test_empty_registry_server_name_correct(self) -> None:
        """Even with no tools, the server name is set correctly."""
        registry = _make_registry()
        server = build_mcp_server(registry, name="Empty Agent")
        assert server.name == "Empty Agent"

        mcp_tools = await server.list_tools()
        assert len(mcp_tools) == 0

    def test_empty_registry_stores_module_server(self) -> None:
        """build_mcp_server sets module-level server even with an empty registry."""
        registry = _make_registry()
        server = build_mcp_server(registry)
        assert get_mcp_server() is server


# ---------------------------------------------------------------------------
# 6. Tool with parameters -> MCP tool accepts correct parameters
# ---------------------------------------------------------------------------


class TestToolParameters:
    """MCP tools expose correct parameter schemas from the underlying functions."""

    async def test_tool_exposes_parameter_schema(self) -> None:
        """An MCP tool derived from a typed function exposes the correct parameter schema."""

        def search(query: str, max_results: int) -> str:
            """Search with query and limit."""
            return f"Results for {query}"

        tool = _make_sync_pai_tool("search", search, "Search with parameters")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        assert len(mcp_tools) == 1

        params = mcp_tools[0].parameters
        assert "query" in params["properties"]
        assert "max_results" in params["properties"]
        assert params["properties"]["query"]["type"] == "string"
        assert params["properties"]["max_results"]["type"] == "integer"

    async def test_required_parameters_in_schema(self) -> None:
        """Required parameters are listed in the MCP tool schema."""

        def lookup(key: str) -> str:
            """Look up by key."""
            return key

        tool = _make_sync_pai_tool("lookup", lookup, "Key lookup")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        params = mcp_tools[0].parameters
        assert "key" in params.get("required", [])

    async def test_optional_parameter_not_required(self) -> None:
        """A parameter with a default value is not required in the MCP schema."""

        def fetch(url: str, timeout: int = 30) -> str:
            """Fetch a URL."""
            return url

        tool = _make_sync_pai_tool("fetch", fetch, "Fetch URL")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        params = mcp_tools[0].parameters

        required = params.get("required", [])
        assert "url" in required
        assert "timeout" not in required

    async def test_bool_parameter_type(self) -> None:
        """Boolean parameters are reflected as boolean in the MCP schema."""

        def toggle(flag: bool) -> str:
            """Toggle a flag."""
            return str(flag)

        tool = _make_sync_pai_tool("toggle", toggle, "Toggle flag")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        params = mcp_tools[0].parameters
        assert params["properties"]["flag"]["type"] == "boolean"

    async def test_tool_execution_with_correct_params_succeeds(self) -> None:
        """Calling an MCP tool with the correct parameters produces the expected result."""

        def multiply(x: int, y: int) -> int:
            """Multiply two numbers."""
            return x * y

        tool = _make_sync_pai_tool("multiply", multiply, "Multiply")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        result = await server.call_tool("multiply", {"x": 6, "y": 7})
        assert result.content[0].text == "42"

    async def test_no_param_tool_schema(self) -> None:
        """A tool with no parameters has an empty properties dict."""

        def ping() -> str:
            """Ping the server."""
            return "pong"

        tool = _make_sync_pai_tool("ping", ping, "Ping")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        params = mcp_tools[0].parameters
        assert params.get("properties", {}) == {}


# ---------------------------------------------------------------------------
# 7. Tool execution error -> proper error response
# ---------------------------------------------------------------------------


class TestToolExecutionErrors:
    """Errors from underlying tool functions are surfaced as ToolError."""

    async def test_value_error_produces_tool_error(self) -> None:
        """A ValueError from the tool function raises ToolError."""

        def fail_tool(msg: str) -> str:
            """A tool that always fails."""
            raise ValueError(f"Intentional failure: {msg}")

        tool = _make_sync_pai_tool("fail_tool", fail_tool, "Always fails")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        with pytest.raises(ToolError, match="Intentional failure"):
            await server.call_tool("fail_tool", {"msg": "test"})

    async def test_runtime_error_produces_tool_error(self) -> None:
        """A RuntimeError from the tool function is surfaced as ToolError."""

        def crash() -> str:
            """A tool that crashes."""
            raise RuntimeError("unexpected crash")

        tool = _make_sync_pai_tool("crash", crash, "Crashes")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        with pytest.raises(ToolError, match="unexpected crash"):
            await server.call_tool("crash", {})

    async def test_calling_nonexistent_tool_raises(self) -> None:
        """Calling a tool name that does not exist raises an error."""
        registry = _make_registry()
        server = build_mcp_server(registry)

        with pytest.raises(NotFoundError):
            await server.call_tool("nonexistent_tool", {})

    async def test_async_tool_error_produces_tool_error(self) -> None:
        """An async tool function that raises is surfaced as ToolError."""

        async def async_fail(*, query: str) -> str:
            raise ConnectionError(f"Cannot reach service for: {query}")

        tool = _make_pai_tool("async_fail", "Async failure", fn=async_fail)
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        with pytest.raises(ToolError, match="Cannot reach service"):
            await server.call_tool("async_fail", {"query": "test"})

    async def test_error_message_preserved(self) -> None:
        """The original error message is preserved in the ToolError."""

        def fragile(x: int) -> str:
            """A fragile tool."""
            raise TypeError(f"Expected string, got {type(x).__name__}")

        tool = _make_sync_pai_tool("fragile", fragile, "Fragile tool")
        registry = _make_registry([tool])
        server = build_mcp_server(registry)

        with pytest.raises(ToolError, match="Expected string, got int"):
            await server.call_tool("fragile", {"x": 42})


# ---------------------------------------------------------------------------
# 8. MCP server reflects current tool surface
# ---------------------------------------------------------------------------


class TestMCPServerReflectsToolSurface:
    """The MCP server tracks the module-level state and supports rebuild."""

    def test_build_stores_module_level_server(self) -> None:
        """build_mcp_server() stores the server as the module-level instance."""
        registry = _make_registry()
        server = build_mcp_server(registry)

        assert get_mcp_server() is server

    def test_get_mcp_server_returns_none_initially(self) -> None:
        """get_mcp_server() returns None before any server is built."""
        assert get_mcp_server() is None

    async def test_rebuild_replaces_server_tools(self) -> None:
        """rebuild_mcp_server() creates a fresh server with updated tools."""
        tool_a = _make_pai_tool("tool_a", "First tool")
        registry_v1 = _make_registry([tool_a])
        server_v1 = build_mcp_server(registry_v1)

        tools_v1 = await server_v1.list_tools()
        assert len(tools_v1) == 1
        assert tools_v1[0].name == "tool_a"

        tool_b = _make_pai_tool("tool_b", "Second tool")
        tool_c = _make_pai_tool("tool_c", "Third tool")
        registry_v2 = _make_registry([tool_b, tool_c])
        server_v2 = rebuild_mcp_server(registry_v2)

        tools_v2 = await server_v2.list_tools()
        tool_names_v2 = {t.name for t in tools_v2}
        assert tool_names_v2 == {"tool_b", "tool_c"}
        assert "tool_a" not in tool_names_v2

    def test_rebuild_replaces_module_server_reference(self) -> None:
        """rebuild_mcp_server() replaces the module-level server reference."""
        registry1 = _make_registry([_make_pai_tool("old_tool")])
        registry2 = _make_registry([_make_pai_tool("new_tool")])

        server1 = build_mcp_server(registry1)
        server2 = rebuild_mcp_server(registry2)

        assert server1 is not server2
        assert get_mcp_server() is server2

    def test_rebuild_preserves_server_name(self) -> None:
        """rebuild_mcp_server() preserves the name from the previous server."""
        registry = _make_registry()
        build_mcp_server(registry, name="OriginalName")

        new_server = rebuild_mcp_server(registry)
        assert new_server.name == "OriginalName"

    def test_rebuild_with_no_previous_server_uses_default_name(self) -> None:
        """When no previous server exists, rebuild uses 'Forge AI' as name."""
        registry = _make_registry()
        server = rebuild_mcp_server(registry)
        assert server.name == "Forge AI"

    async def test_rebuilt_server_has_only_new_tools(self) -> None:
        """After rebuild, old tools are gone and only new tools are present."""
        old_tools = [_make_pai_tool(f"old_{i}") for i in range(3)]
        registry_old = _make_registry(old_tools)
        build_mcp_server(registry_old)

        new_tools = [_make_pai_tool("new_only")]
        registry_new = _make_registry(new_tools)
        server = rebuild_mcp_server(registry_new)

        mcp_tools = await server.list_tools()
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "new_only"

    async def test_build_then_list_reflects_registered_tools(self) -> None:
        """After build_mcp_server, list_tools returns exactly the registered tools."""
        tools = [
            _make_pai_tool("alpha", "Alpha tool"),
            _make_pai_tool("beta", "Beta tool"),
        ]
        registry = _make_registry(tools)
        server = build_mcp_server(registry)

        mcp_tools = await server.list_tools()
        names = {t.name for t in mcp_tools}
        assert names == {"alpha", "beta"}
        assert len(mcp_tools) == 2
