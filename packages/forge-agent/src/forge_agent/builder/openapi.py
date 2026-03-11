"""OpenAPI-based tool builder for Forge Agent.

Generates tool definitions from OpenAPI specifications. Currently uses
placeholder implementations; full FastMCP.from_openapi() integration
is planned for a future release.
"""

from __future__ import annotations

from typing import Any

from forge_config.schema import OpenAPISource
from pydantic_ai.tools import Tool


class OpenAPIToolBuilder:
    """Build PydanticAI tools from an OpenAPI specification.

    Takes an OpenAPISource config and generates tool definitions from
    the route_map. Currently produces placeholder tools that return
    stub responses; real HTTP-calling implementations will be added
    when FastMCP.from_openapi() integration is complete.
    """

    def __init__(self, source: OpenAPISource) -> None:
        self._source = source

    def build(self) -> list[Tool[None]]:
        """Build tool definitions from the OpenAPI route_map.

        Returns:
            List of PydanticAI Tool objects, one per route in route_map.
        """
        tools: list[Tool[None]] = []
        for route, tool_name in self._source.route_map.items():
            full_name = f"{self._source.prefix}_{tool_name}" if self._source.prefix else tool_name
            tool = self._create_placeholder_tool(full_name, route)
            tools.append(tool)
        return tools

    def _create_placeholder_tool(self, name: str, route: str) -> Tool[None]:
        """Create a placeholder tool for a given route.

        Args:
            name: The tool name.
            route: The API route (e.g., "GET /users").

        Returns:
            A PydanticAI Tool with a stub implementation.
        """
        source_url = self._source.url or self._source.path or "unknown"

        async def placeholder(**kwargs: Any) -> dict[str, Any]:
            return {
                "status": "stub",
                "tool": name,
                "route": route,
                "source": source_url,
                "params": kwargs,
            }

        placeholder.__name__ = name
        placeholder.__qualname__ = name
        placeholder.__doc__ = f"Placeholder for OpenAPI route: {route}"

        return Tool(placeholder, name=name)
