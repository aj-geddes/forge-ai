"""MCP tool surface route — exposes agent tools via the MCP protocol.

Creates a FastMCP server instance and registers tools from the agent's
ToolSurfaceRegistry as MCP tools. Each MCP tool wraps the corresponding
PydanticAI tool function so that MCP clients can invoke Forge tools
directly over the MCP protocol.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.tools import Tool as MCPTool
from forge_agent.builder.registry import ToolSurfaceRegistry
from pydantic_ai.tools import Tool as PydanticAITool
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("forge.gateway.mcp")

# Module-level MCP server instance, populated during app lifespan.
_mcp_server: FastMCP | None = None


def get_mcp_server() -> FastMCP | None:
    """Return the current FastMCP server instance, if initialized."""
    return _mcp_server


def create_mcp_server(name: str = "Forge AI") -> FastMCP:
    """Create a new FastMCP server instance.

    Args:
        name: The server name advertised to MCP clients.

    Returns:
        A fresh FastMCP server with no tools registered.
    """
    return FastMCP(name)


def register_tools_from_registry(
    mcp: FastMCP,
    registry: ToolSurfaceRegistry,
) -> int:
    """Register all tools from a ToolSurfaceRegistry into a FastMCP server.

    Each PydanticAI tool in the registry is converted to a FastMCP tool
    that wraps the original tool function, preserving name and description.

    Args:
        mcp: The FastMCP server to register tools on.
        registry: The ToolSurfaceRegistry containing PydanticAI tools.

    Returns:
        The number of tools registered.
    """
    count = 0
    for pai_tool in registry.tools:
        _register_single_tool(mcp, pai_tool)
        count += 1
    return count


def _register_single_tool(mcp: FastMCP, pai_tool: PydanticAITool[Any]) -> None:
    """Convert a single PydanticAI tool to a FastMCP tool and register it.

    Args:
        mcp: The FastMCP server instance.
        pai_tool: The PydanticAI tool to register.
    """
    fn = pai_tool.function
    name = pai_tool.name
    description = pai_tool.description or fn.__doc__ or ""

    mcp_tool = MCPTool.from_function(
        fn,
        name=name,
        description=description,
    )
    mcp.add_tool(mcp_tool)
    logger.debug("Registered MCP tool: %s", name)


def build_mcp_server(
    registry: ToolSurfaceRegistry,
    name: str = "Forge AI",
) -> FastMCP:
    """Build a complete FastMCP server from a tool registry.

    Creates the server, registers all tools, and stores it as the
    module-level instance for later access.

    Args:
        registry: The ToolSurfaceRegistry containing built tools.
        name: The server name advertised to MCP clients.

    Returns:
        The fully configured FastMCP server.
    """
    global _mcp_server

    mcp = create_mcp_server(name)
    count = register_tools_from_registry(mcp, registry)
    _mcp_server = mcp
    logger.info("MCP server built with %d tools", count)
    return mcp


def rebuild_mcp_server(registry: ToolSurfaceRegistry) -> FastMCP:
    """Rebuild the MCP server with an updated tool registry.

    Replaces the module-level server instance. Used when the config
    changes and the tool surface is rebuilt.

    Args:
        registry: The updated ToolSurfaceRegistry.

    Returns:
        The newly built FastMCP server.
    """
    name = "Forge AI"
    if _mcp_server is not None:
        name = _mcp_server.name
    return build_mcp_server(registry, name=name)


def get_mcp_asgi_app(mcp: FastMCP) -> ASGIApp:
    """Get the ASGI application for mounting the MCP server.

    Uses FastMCP's streamable-http transport for full MCP protocol
    support over HTTP.

    Args:
        mcp: The FastMCP server instance.

    Returns:
        A Starlette ASGI app ready for mounting in the FastAPI app.
    """
    return mcp.http_app(path="/")


# ---------------------------------------------------------------------------
# Persistent /mcp mount: dispatches to whichever FastMCP ASGI app is active
#
# Two runtime problems this section solves:
#
# 1. FastMCP's streamable-HTTP ASGI app owns a session manager that MUST
#    have its own ASGI lifespan entered (starting a task group) before any
#    request will work -- otherwise every call fails with "Task group is
#    not initialized." A plain ``app.mount("/mcp", mcp_app)`` never runs
#    that lifespan on its own; the parent application has to explicitly
#    enter it. ``set_active_asgi_app`` / ``activate`` do that.
#
# 2. FastAPI does not support unmounting/remounting routes on an
#    already-running app, so a tool-surface rebuild can't simply replace
#    the ``Mount`` at ``/mcp``. Instead, a single dispatcher app
#    (``get_mcp_mount_app``) is mounted exactly once, for the process
#    lifetime, and always forwards to whichever FastMCP ASGI app is
#    currently "active." ``rebuild_and_activate`` rebuilds the tool
#    surface and swaps the active app, so hot-reloaded tools are actually
#    served -- not just recorded in an inert module-level reference.
# ---------------------------------------------------------------------------


class _ManagedLifespan:
    """Holds a FastMCP ASGI app's lifespan open in its own dedicated task.

    ``anyio`` cancel scopes (which FastMCP's session manager relies on via
    ``anyio.create_task_group()``) must be entered and exited within the
    *same task*, and nested scopes within a task must be exited in strict
    LIFO order. That makes a shared ``AsyncExitStack`` unsuitable here:
    activating a new app while the previous one is still serving requests
    means two lifespans need to be open at once, entered in one order and
    exited in the other -- which a single task's scope stack cannot do.

    Running each app's lifespan in its own ``asyncio`` task sidesteps the
    problem entirely: each task has its own independent scope stack, so
    two lifespans can safely overlap regardless of start/stop order.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        async with self._app.lifespan(self._app):  # type: ignore[attr-defined]
            self._ready.set()
            await self._stop.wait()

    async def start(self) -> None:
        """Block until the lifespan has been entered and is ready to serve."""
        await self._ready.wait()

    async def stop(self) -> None:
        """Signal the lifespan to exit and wait for it to finish shutting down."""
        self._stop.set()
        await self._task


_active_asgi_app: ASGIApp | None = None
_active_lifespan: _ManagedLifespan | None = None


class _MCPMountApp:
    """Persistent ASGI app mounted at ``/mcp`` for the gateway's lifetime.

    Dispatches every HTTP request to whichever FastMCP ASGI app is
    currently active (see :func:`set_active_asgi_app`), and enforces the
    same bypass-free ``tools:invoke`` authorization used by the
    programmatic (``/v1``) routes (ADR-0001). A raw ``Mount`` bypasses
    FastAPI's routing/``Depends`` system entirely, so that auth check has
    to happen here rather than via a router dependency.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            from forge_gateway.security import enforce_mcp_security

            request = Request(scope, receive=receive)
            await enforce_mcp_security(request)

        current = _active_asgi_app
        if current is None:
            raise HTTPException(status_code=503, detail="MCP server not initialized")
        await current(scope, receive, send)


_mount_app = _MCPMountApp()


def get_mcp_mount_app() -> ASGIApp:
    """Return the persistent ASGI app to mount at ``/mcp``.

    Mount this exactly once, at application startup::

        app.mount("/mcp", get_mcp_mount_app(), name="mcp")

    It stays mounted for the process lifetime. Use :func:`activate` or
    :func:`rebuild_and_activate` to change which FastMCP server it serves.
    """
    return _mount_app


def get_active_asgi_app() -> ASGIApp | None:
    """Return the FastMCP ASGI app currently being served at ``/mcp``, if any."""
    return _active_asgi_app


async def set_active_asgi_app(new_app: ASGIApp) -> None:
    """Start *new_app*'s ASGI lifespan and make it the live ``/mcp`` app.

    The new app's lifespan is fully started (and only then) does it become
    the active app; the previously active app's lifespan is stopped only
    *after* that, so there is never a window where the mount has no
    working app, and no risk of dispatching to a half-initialized session
    manager. See :class:`_ManagedLifespan` for why each app's lifespan
    runs in its own task rather than a shared ``AsyncExitStack``.

    Args:
        new_app: A Starlette app with a ``.lifespan`` property, as
            returned by :func:`get_mcp_asgi_app` (FastMCP's
            ``StarletteWithLifespan``).
    """
    global _active_asgi_app, _active_lifespan

    new_lifespan = _ManagedLifespan(new_app)
    await new_lifespan.start()

    old_lifespan = _active_lifespan
    _active_asgi_app = new_app
    _active_lifespan = new_lifespan

    if old_lifespan is not None:
        await old_lifespan.stop()


async def shutdown_active_asgi_app() -> None:
    """Stop the currently active MCP ASGI app's lifespan, if any.

    Safe to call even when nothing is active (e.g. the gateway never
    successfully built an agent/tool registry).
    """
    global _active_asgi_app, _active_lifespan

    lifespan, _active_lifespan = _active_lifespan, None
    _active_asgi_app = None
    if lifespan is not None:
        await lifespan.stop()


async def activate(mcp_server: FastMCP) -> ASGIApp:
    """Build the ASGI app for *mcp_server* and make it the live ``/mcp`` app.

    Args:
        mcp_server: The FastMCP server to serve.

    Returns:
        The ASGI app now being served.
    """
    asgi_app = get_mcp_asgi_app(mcp_server)
    await set_active_asgi_app(asgi_app)
    return asgi_app


async def rebuild_and_activate(registry: ToolSurfaceRegistry) -> FastMCP:
    """Rebuild the MCP server from *registry* and activate it at ``/mcp``.

    Combines :func:`rebuild_mcp_server` (which produces a fresh FastMCP
    instance reflecting the current tool registry) with :func:`activate`
    (which starts its ASGI lifespan and atomically swaps it in), so that
    hot-reloaded tools are genuinely served over MCP rather than only
    updating a reference nothing reads from.

    Args:
        registry: The updated ToolSurfaceRegistry.

    Returns:
        The newly built and activated FastMCP server.
    """
    server = rebuild_mcp_server(registry)
    await activate(server)
    return server
