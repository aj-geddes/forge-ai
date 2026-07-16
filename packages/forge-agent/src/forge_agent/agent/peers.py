"""Peer agent caller for A2A communication.

Enables this Forge instance to call other Forge agents (or any A2A-compatible
agent) defined in the config's agents.peers section.

ADR-0004 SS6 (outbound mTLS): identity on the workload plane comes
exclusively from the mutual-TLS channel, never from a request body field.
There is deliberately no ``caller_id`` in :class:`A2ATaskRequest` -- the
server already ignores it (ADR-0001 closed that bypass), and on the
workload plane the caller's identity is the verified SPIFFE ID of the
client certificate presented during the TLS handshake, not anything the
caller could self-report in JSON.

ADR-0004 SS7.3 (mandatory pinning + pre-send verification): when an
``identity`` provider is supplied, every peer :class:`PeerCaller` is
asked to call MUST have a pinned ``PeerAgent.spiffe_id`` -- a peer with
no pinned identity is refused outright (:class:`PeerPinningRequiredError`,
a subclass of :class:`PeerVerificationError`) before any connection is
attempted, because there would be nothing to verify the responder
against. ``httpx``'s ``client.post(...)`` connects and sends the full
request body in a single call, so verifying the peer only *after*
``post()`` returns would let a complete task payload already reach a
wrong-but-trust-domain-valid peer before a mismatch is even detected.
To close that window, :meth:`PeerCaller.call_peer` first performs a
handshake-only TLS probe (:func:`_probe_peer_tls_identity`) to the peer
over the *same* mTLS ``SSLContext`` the real request will use, reads the
SPIFFE ID from the peer's certificate, and only proceeds to send the
real request -- payload and all -- once that matches ``peer.spiffe_id``.
On a mismatch, :class:`PeerVerificationError` is raised and *nothing* is
ever written to the connection beyond the TLS handshake itself.

This pre-send probe applies only to the internally managed mTLS client
(no ``http_client`` override). When a caller supplies its own
``http_client`` (a test/DI seam -- see :class:`PeerCaller`'s docstring),
there is no way to know it is safe to open a second, independent
handshake-only connection to the same origin (it may not even be a real
network endpoint), so verification there still falls back to inspecting
the actual response's TLS connection *after* ``post()`` returns
(:meth:`PeerCaller._verify_peer_identity`) -- the same residual timing
window the security review identified, now scoped to that explicit
opt-in seam rather than the default production path.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from agentweave.transport.channel import PeerVerificationError
from forge_config.schema import EgressPolicy, PeerAgent
from forge_security.egress import make_guarded_client
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
    "PeerPinningRequiredError",
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


class PeerPinningRequiredError(PeerVerificationError):  # type: ignore[misc]
    """Raised when :class:`PeerCaller` has a workload ``identity`` (mTLS
    is in play) but the target peer has no pinned ``spiffe_id`` to
    verify against (ADR-0004 SS7.3, mandatory pinning).

    A peer with no pinned SPIFFE ID must never receive a task payload
    over a channel whose only guarantee is "some SPIRE workload
    answered" -- there is nothing to verify the responder's identity
    against, so the call is refused before any connection is attempted.

    Subclasses :class:`PeerVerificationError` so callers that already
    handle outbound peer-verification failures generically continue to
    catch this without changes.
    """

    def __init__(self, peer_name: str) -> None:
        self.peer_name = peer_name
        message = (
            f"Peer '{peer_name}' has no spiffe_id configured, but this "
            "PeerCaller is using a workload identity (mTLS). ADR-0004 "
            "SS7.3 requires every peer called over mTLS to have a "
            "pinned spiffe_id so its identity can be verified before a "
            "task payload is sent. Set PeerAgent.spiffe_id for this peer."
        )
        # Deliberately bypass PeerVerificationError.__init__'s
        # expected/actual formatting -- there is no expected id to
        # report here, only "none configured at all".
        Exception.__init__(self, message)
        self.expected_id = "<none configured>"
        self.actual_id = None


def _split_endpoint_host_port(endpoint: str) -> tuple[str, int]:
    """Parse *endpoint* (e.g. ``https://peer.hvslocal:8443``) into
    ``(host, port)`` -- the same origin ``httpx``'s ``client.post`` will
    connect to for the real request -- for the pre-send TLS probe.
    """
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    if not host:
        msg = f"Peer endpoint {endpoint!r} has no host to verify mTLS identity against"
        raise PeerCallError(msg)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


async def _probe_peer_tls_identity(
    ssl_context: Any,
    host: str,
    port: int,
    *,
    connect_timeout: float = 10.0,
) -> str | None:
    """Open a handshake-only TLS connection to *host*:*port* using
    *ssl_context* and return the SPIFFE ID presented by the peer's
    certificate -- WITHOUT sending any application data.

    This is the pre-send half of ADR-0004 SS6/SS7.3 peer verification
    (see the module docstring): it performs the same TLS handshake
    ``httpx`` would perform to the same origin, over the same mTLS
    ``SSLContext``, reads the peer certificate, and closes the
    connection immediately -- before any request line or body is ever
    written to the socket.

    Returns ``None`` (never raises for identity-related reasons) when
    the peer presents no certificate or the certificate has no SPIFFE
    SAN -- callers must treat ``None`` as "no identity available", not
    as a match.

    Raises:
        OSError: On connection failure (DNS, refused, timeout, TLS
            handshake/verification failure) -- callers translate this
            into :class:`PeerCallError`, consistent with how a failed
            ``client.post`` is handled.
        TimeoutError: If the connection does not complete within
            *connect_timeout* seconds.
    """
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ssl_context, server_hostname=host),
        timeout=connect_timeout,
    )
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            return None
        try:
            cert_der = ssl_object.getpeercert(binary_form=True)
        except Exception:
            logger.warning("Could not read peer certificate from pre-send mTLS probe")
            return None
        if not cert_der:
            return None
        return extract_spiffe_id_from_cert(cert_der)
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


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
            to build a client. Peer SPIFFE-ID verification still applies
            (mandatory pinning is enforced regardless of this override),
            but the pre-send TLS probe (ADR-0004 SS7.3) is skipped for
            an injected client -- verification instead happens by
            inspecting the actual response's TLS connection *after*
            ``post()`` returns, which is the documented residual timing
            window for this explicit opt-in seam (see the module
            docstring).
        identity: Optional workload identity provider (ADR-0004 SS6). When
            set and no ``http_client`` override is given, outbound calls
            use an mTLS client built from
            ``identity.create_tls_context(server=False)``, every peer
            must have a pinned ``spiffe_id`` (mandatory pinning --
            :class:`PeerPinningRequiredError` otherwise), and the peer's
            SPIFFE ID is verified BEFORE the request is sent.
        egress_policy: Optional SSRF/egress policy (ADR-0006), threaded
            from ``config.security.egress``. Both the internally managed
            mTLS client and the non-mTLS fallback client are built via
            :func:`make_guarded_client`, so every peer connection gets
            connect-time pinned-IP internal-address blocking -- closing
            the DNS-rebind window between ``validate_peer_endpoint`` at
            write time and the actual connect. Ignored for an injected
            ``http_client`` (test/DI seam). ``None`` => a default-safe
            :class:`EgressPolicy` is applied by the guarded transport.
    """

    def __init__(
        self,
        peers: list[PeerAgent],
        http_client: httpx.AsyncClient | None = None,
        *,
        identity: WorkloadIdentityProvider | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        self._peers = {peer.name: peer for peer in peers}
        self._http_client = http_client
        self._identity = identity
        self._egress_policy = egress_policy
        self._mtls_client: httpx.AsyncClient | None = None
        self._mtls_ssl_context: Any = None

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
        reuse across calls. With neither, falls back to an ephemeral guarded
        client, closed after the single call that uses it. Both internally
        built clients (mTLS and fallback) go through
        :func:`make_guarded_client` (ADR-0006) for connect-time internal-IP
        blocking; an injected ``http_client`` is used verbatim.
        """
        if self._http_client is not None:
            return self._http_client, False

        if self._identity is not None:
            if self._mtls_client is None:
                self._mtls_ssl_context = await self._identity.create_tls_context(server=False)
                # ADR-0006: route the mTLS peer client through the SSRF guard too.
                # ``verify`` preserves the workload SSLContext (SNI + cert
                # verification bind to the peer hostname); the guard adds
                # connect-time pinned-IP internal-address blocking on top.
                self._mtls_client = make_guarded_client(
                    policy=self._egress_policy, verify=self._mtls_ssl_context
                )
            return self._mtls_client, False

        # ADR-0006: the non-mTLS fallback must not open a bare, unguarded
        # client -- build the sanctioned guarded client so a peer endpoint
        # that (re)binds to an internal IP is refused at connect.
        return make_guarded_client(policy=self._egress_policy), True

    def _require_pinned_spiffe_id(self, peer: PeerAgent) -> None:
        """Mandatory pinning (ADR-0004 SS7.3): when this caller has a
        workload ``identity`` (mTLS is in play), every peer it calls must
        have a pinned ``spiffe_id`` to verify against -- a peer with no
        pinned identity must never receive a payload over a channel whose
        only guarantee is "some SPIRE workload answered".

        A no-op when no ``identity`` is configured -- nothing to pin
        against on a plain (non-mTLS) call, matching pre-ADR-0004
        behavior exactly.

        Raises:
            PeerPinningRequiredError: ``identity`` is set but ``peer`` has
                no ``spiffe_id`` configured.
        """
        if self._identity is not None and not peer.spiffe_id:
            raise PeerPinningRequiredError(peer.name)

    async def _verify_peer_before_send(self, peer: PeerAgent) -> None:
        """Pre-send half of ADR-0004 SS6/SS7.3 peer verification (see the
        module docstring): perform a handshake-only TLS probe to *peer*
        over the cached mTLS ``SSLContext`` and confirm the presented
        SPIFFE ID matches ``peer.spiffe_id`` BEFORE the real request
        (carrying the task payload) is ever sent. Only called for the
        internally managed mTLS client -- see :meth:`call_peer`.

        Raises:
            PeerVerificationError: The probe's verified peer SPIFFE ID
                does not match ``peer.spiffe_id`` -- nothing is sent.
            PeerCallError: The probe connection itself failed (DNS,
                refused, timeout, TLS handshake/verification failure).
        """
        if self._mtls_ssl_context is None:
            msg = f"No mTLS SSLContext available to verify peer '{peer.name}' before sending"
            raise PeerCallError(msg)

        host, port = _split_endpoint_host_port(peer.endpoint)
        try:
            actual = await _probe_peer_tls_identity(self._mtls_ssl_context, host, port)
        except (OSError, TimeoutError) as exc:
            msg = f"Failed to verify peer '{peer.name}' at {peer.endpoint} before sending: {exc}"
            raise PeerCallError(msg) from exc

        if actual != peer.spiffe_id:
            raise PeerVerificationError(peer.spiffe_id, actual)

    def _verify_peer_identity(self, peer: PeerAgent, response: httpx.Response) -> None:
        """Verify the responding peer's SPIFFE ID matches ``peer.spiffe_id``
        (ADR-0004 SS6). A no-op unless both a workload ``identity`` and an
        expected ``peer.spiffe_id`` are configured -- there is nothing to
        verify against otherwise. This is a post-send, defense-in-depth
        check: for the internally managed mTLS client, the pre-send probe
        in :meth:`_verify_peer_before_send` already blocked sending to a
        mismatched peer; for an injected ``http_client`` (test/DI seam),
        this is the *only* verification performed -- see the module
        docstring for that residual timing window.

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
            PeerCallError: If the HTTP call (or the pre-send verification
                probe) fails.
            PeerPinningRequiredError: If this caller has a workload
                ``identity`` (mTLS) but ``peer`` has no pinned
                ``spiffe_id`` (ADR-0004 SS7.3, mandatory pinning). No
                connection is attempted.
            PeerVerificationError: If mTLS peer verification is enabled
                and the responding peer's SPIFFE ID doesn't match --
                raised BEFORE the task payload is sent whenever the
                internally managed mTLS client is in use (see the module
                docstring for the residual window with an injected
                ``http_client``).
        """
        peer = self.get_peer(name)
        url = peer.endpoint.rstrip("/") + "/a2a/tasks"

        # ADR-0004 SS7.3: mandatory pinning -- refuse before ever
        # attempting a connection when there is nothing to verify the
        # peer against.
        self._require_pinned_spiffe_id(peer)

        request = A2ATaskRequest(task_type=task_type, payload=payload or {})

        client, should_close = await self._resolve_client()

        try:
            if self._identity is not None and self._http_client is None:
                # ADR-0004 SS6/SS7.3: verify the peer BEFORE sending the
                # task payload -- see _verify_peer_before_send and the
                # module docstring.
                await self._verify_peer_before_send(peer)

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
