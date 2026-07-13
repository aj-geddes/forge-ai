"""Peer agent caller for A2A communication.

Enables this Forge instance to call other Forge agents (or any A2A-compatible
agent) defined in the config's agents.peers section.

ADR-0004 SS6 (outbound mTLS): identity on the workload plane comes
exclusively from the mutual-TLS channel, never from a request body field.
There is deliberately no ``caller_id`` in :class:`A2ATaskRequest` -- the
server already ignores it (ADR-0001 closed that bypass), and on the
workload plane the caller's identity is the verified SPIFFE ID of the
client certificate presented during the TLS handshake, not anything the
caller could self-report in JSON. When an ``identity`` provider is
supplied, :class:`PeerCaller` builds its outbound client from that
provider's mTLS context and verifies the SPIFFE ID presented by the
responding peer against ``PeerAgent.spiffe_id`` (when configured),
raising :class:`PeerVerificationError` on a mismatch -- the request may
have reached the wrong service entirely, and must not be trusted.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx
from agentweave.transport.channel import PeerVerificationError
from forge_config.schema import PeerAgent
from forge_security.workload import extract_spiffe_id_from_cert
from pydantic import BaseModel, Field
from pydantic_ai.tools import Tool

logger = logging.getLogger(__name__)

__all__ = [
    "A2ATaskRequest",
    "A2ATaskResponse",
    "PeerCallError",
    "PeerCaller",
    "PeerNotFoundError",
    "PeerVerificationError",
]


class A2ATaskRequest(BaseModel):
    """Request body sent to a peer agent's /a2a/tasks endpoint.

    ADR-0004 SS6: no ``caller_id`` field -- see the module docstring.
    """

    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class A2ATaskResponse(BaseModel):
    """Response from a peer agent's /a2a/tasks endpoint."""

    status: str
    result: Any = None
    error: str | None = None


class PeerNotFoundError(Exception):
    """Raised when a peer agent name is not found in the configuration."""


class PeerCallError(Exception):
    """Raised when calling a peer agent fails due to a network or protocol error."""


class WorkloadIdentityProvider(Protocol):
    """The subset of the workload identity provider :class:`PeerCaller`
    needs for outbound mTLS -- satisfied by
    ``forge_security.workload.WorkloadPlane.identity``."""

    async def create_tls_context(self, server: bool = False) -> Any: ...


def _extract_peer_spiffe_id_from_response(response: httpx.Response) -> str | None:
    """Extract the verified peer's SPIFFE ID from *response*'s underlying
    TLS connection (ADR-0004 SS6).

    httpx exposes the raw network stream via
    ``response.extensions["network_stream"]`` (an httpcore
    ``NetworkStream``), whose ``get_extra_info("ssl_object")`` returns the
    ``ssl.SSLObject`` used for the handshake -- the same primitive
    ``asyncio``/``ssl`` use everywhere else in this codebase. Returns
    ``None`` (never raises) when the connection isn't TLS, the stream
    doesn't expose the extension (e.g. a mocked/test transport), or the
    peer presented no certificate -- callers must treat ``None`` as "no
    peer identity available to verify," not as a match.
    """
    network_stream = response.extensions.get("network_stream") if response.extensions else None
    if network_stream is None:
        return None

    get_extra_info = getattr(network_stream, "get_extra_info", None)
    if get_extra_info is None:
        return None

    ssl_object = get_extra_info("ssl_object")
    if ssl_object is None:
        return None

    try:
        cert_der = ssl_object.getpeercert(binary_form=True)
    except Exception:
        logger.warning("Could not read peer certificate from mTLS connection")
        return None

    if not cert_der:
        return None

    return extract_spiffe_id_from_cert(cert_der)


class PeerCaller:
    """Calls peer agents over the A2A protocol.

    Takes a list of PeerAgent configs and provides methods to call them
    by name, as well as to generate PydanticAI tools for agent integration.

    Args:
        peers: List of PeerAgent configurations.
        http_client: Optional pre-configured httpx client. When given, it
            is used as-is (test/DI seam) -- ``identity`` is not consulted
            to build a client, but peer SPIFFE-ID verification (when
            ``identity`` is set and the peer configures ``spiffe_id``)
            still applies to whatever client is used.
        identity: Optional workload identity provider (ADR-0004 SS6). When
            set and no ``http_client`` override is given, outbound calls
            use an mTLS client built from
            ``identity.create_tls_context(server=False)``, and the
            responding peer's SPIFFE ID is verified against
            ``PeerAgent.spiffe_id`` when configured.
    """

    def __init__(
        self,
        peers: list[PeerAgent],
        http_client: httpx.AsyncClient | None = None,
        *,
        identity: WorkloadIdentityProvider | None = None,
    ) -> None:
        self._peers = {peer.name: peer for peer in peers}
        self._http_client = http_client
        self._identity = identity
        self._mtls_client: httpx.AsyncClient | None = None

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

    async def _resolve_client(self) -> tuple[httpx.AsyncClient, bool]:
        """Resolve the httpx client to use for the next call.

        Returns ``(client, should_close_after_use)``. An explicitly
        injected ``http_client`` always wins (test/DI seam) and is never
        closed here. Otherwise, when a workload ``identity`` provider was
        supplied, a single mTLS client (ADR-0004 SS6) is built once from
        ``identity.create_tls_context(server=False)`` and cached for
        reuse across calls. With neither, falls back to a plain ephemeral
        client, closed after the single call that uses it.
        """
        if self._http_client is not None:
            return self._http_client, False

        if self._identity is not None:
            if self._mtls_client is None:
                ssl_context = await self._identity.create_tls_context(server=False)
                self._mtls_client = httpx.AsyncClient(verify=ssl_context)
            return self._mtls_client, False

        return httpx.AsyncClient(), True

    def _verify_peer_identity(self, peer: PeerAgent, response: httpx.Response) -> None:
        """Verify the responding peer's SPIFFE ID matches ``peer.spiffe_id``
        (ADR-0004 SS6). A no-op unless both a workload ``identity`` and an
        expected ``peer.spiffe_id`` are configured -- there is nothing to
        verify against otherwise.

        Raises:
            PeerVerificationError: The connection's verified peer SPIFFE ID
                does not match what was expected -- the request may have
                reached the wrong service and must not be trusted.
        """
        if self._identity is None or not peer.spiffe_id:
            return
        actual = _extract_peer_spiffe_id_from_response(response)
        if actual != peer.spiffe_id:
            raise PeerVerificationError(peer.spiffe_id, actual)

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
            PeerVerificationError: If mTLS peer verification is enabled
                and the responding peer's SPIFFE ID doesn't match.
        """
        peer = self.get_peer(name)
        url = peer.endpoint.rstrip("/") + "/a2a/tasks"

        request = A2ATaskRequest(task_type=task_type, payload=payload or {})

        client, should_close = await self._resolve_client()

        try:
            response = await client.post(
                url,
                json=request.model_dump(),
                timeout=30.0,
            )
            response.raise_for_status()
            self._verify_peer_identity(peer, response)
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

    async def aclose(self) -> None:
        """Close the cached mTLS client, if one was built. Safe to call
        even when no mTLS client was ever created."""
        if self._mtls_client is not None:
            await self._mtls_client.aclose()
            self._mtls_client = None

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
