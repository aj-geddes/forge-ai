"""Peer agent caller for A2A communication.

Enables this Forge instance to call other Forge agents (or any A2A-compatible
agent) defined in the config's agents.peers section.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from forge_config.schema import PeerAgent
from pydantic import BaseModel, Field
from pydantic_ai.tools import Tool

logger = logging.getLogger(__name__)


class A2ATaskRequest(BaseModel):
    """Request body sent to a peer agent's /a2a/tasks endpoint."""

    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    caller_id: str = ""


class A2ATaskResponse(BaseModel):
    """Response from a peer agent's /a2a/tasks endpoint."""

    status: str
    result: Any = None
    error: str | None = None


class PeerNotFoundError(Exception):
    """Raised when a peer agent name is not found in the configuration."""


class PeerCallError(Exception):
    """Raised when calling a peer agent fails due to a network or protocol error."""


class PeerCaller:
    """Calls peer agents over the A2A protocol.

    Takes a list of PeerAgent configs and provides methods to call them
    by name, as well as to generate PydanticAI tools for agent integration.

    Args:
        peers: List of PeerAgent configurations.
        caller_id: Identity string for this Forge instance.
        http_client: Optional pre-configured httpx client.
    """

    def __init__(
        self,
        peers: list[PeerAgent],
        caller_id: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._peers = {peer.name: peer for peer in peers}
        self._caller_id = caller_id
        self._http_client = http_client

    @property
    def peer_names(self) -> list[str]:
        """Return the names of all configured peers."""
        return list(self._peers.keys())

    def get_peer(self, name: str) -> PeerAgent:
        """Look up a peer by name.

        Args:
            name: The peer agent name.

        Returns:
            The PeerAgent config.

        Raises:
            PeerNotFoundError: If no peer with the given name exists.
        """
        peer = self._peers.get(name)
        if peer is None:
            available = ", ".join(sorted(self._peers.keys())) or "(none)"
            msg = f"Peer agent '{name}' not found. Available peers: {available}"
            raise PeerNotFoundError(msg)
        return peer

    async def call_peer(
        self,
        name: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
    ) -> A2ATaskResponse:
        """Call a peer agent by name with an A2A task request.

        Args:
            name: The peer agent name (must match a configured peer).
            task_type: The type of task to request.
            payload: Optional payload data for the task.

        Returns:
            The parsed A2ATaskResponse from the peer.

        Raises:
            PeerNotFoundError: If the peer name is not configured.
            PeerCallError: If the HTTP call fails.
        """
        peer = self.get_peer(name)
        url = peer.endpoint.rstrip("/") + "/a2a/tasks"

        request = A2ATaskRequest(
            task_type=task_type,
            payload=payload or {},
            caller_id=self._caller_id,
        )

        client = self._http_client or httpx.AsyncClient()
        should_close = self._http_client is None

        try:
            response = await client.post(
                url,
                json=request.model_dump(),
                timeout=30.0,
            )
            response.raise_for_status()
            return A2ATaskResponse.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            msg = f"Peer '{name}' returned HTTP {exc.response.status_code}: {exc.response.text}"
            raise PeerCallError(msg) from exc
        except httpx.HTTPError as exc:
            msg = f"Failed to call peer '{name}' at {url}: {exc}"
            raise PeerCallError(msg) from exc
        finally:
            if should_close:
                await client.aclose()

    def build_tools(self) -> list[Tool[None]]:
        """Generate PydanticAI tools for each configured peer.

        Each peer becomes a tool named ``peer_{name}`` (with hyphens
        replaced by underscores) that accepts ``task_type`` and ``payload``
        parameters and delegates to :meth:`call_peer`.

        Returns:
            List of PydanticAI Tool instances, one per peer.
        """
        tools: list[Tool[None]] = []
        for peer in self._peers.values():
            tool = self._build_peer_tool(peer)
            tools.append(tool)
        return tools

    def _build_peer_tool(self, peer: PeerAgent) -> Tool[None]:
        """Build a single PydanticAI tool for a peer agent.

        Args:
            peer: The peer agent configuration.

        Returns:
            A PydanticAI Tool wrapping a call to the peer.
        """
        safe_name = peer.name.replace("-", "_")
        tool_name = f"peer_{safe_name}"
        capabilities_str = ", ".join(peer.capabilities) if peer.capabilities else "general"
        description = (
            f"Call peer agent '{peer.name}' "
            f"(capabilities: {capabilities_str}). "
            f"Send a task_type and payload dict."
        )
        peer_name = peer.name
        caller = self

        async def peer_tool_func(
            *, task_type: str, payload: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            result = await caller.call_peer(peer_name, task_type, payload)
            return result.model_dump()

        peer_tool_func.__name__ = tool_name
        peer_tool_func.__qualname__ = tool_name
        peer_tool_func.__doc__ = description

        return Tool(peer_tool_func, name=tool_name)
