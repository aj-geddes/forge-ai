"""MCP tool surface route — exposes agent tools via the MCP protocol.

Creates a FastMCP server instance and registers tools from the agent's
ToolSurfaceRegistry as MCP tools. Each MCP tool wraps the corresponding
PydanticAI tool function so that MCP clients can invoke Forge tools
directly over the MCP protocol.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool as MCPTool
from forge_agent.builder.registry import ToolSurfaceRegistry
from pydantic_ai.tools import Tool as PydanticAITool
from starlette.types import ASGIApp

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
