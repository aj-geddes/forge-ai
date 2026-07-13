"""ADR-0004: the AgentWeave workload (SPIFFE + OPA + mTLS) A2A plane --
a dedicated, pod-terminated mTLS listener physically and logically
separate from the human ``:8000`` OIDC plane.

Nothing in this module is reachable from ``:8000``: the human resolver
(``forge_gateway.security``) never imports or calls anything here, and
this module never imports ``forge_gateway.security``. The only shared
code is :func:`forge_gateway.routes.a2a.run_a2a_task` -- the agent
dispatch logic itself, which has no notion of identity at all.

**The :8443 listener approach (ADR-0004 SS3, SS7.3):** a second ASGI
app, served by a second in-process ``uvicorn.Server`` bound to
``security.agentweave.workload_listener_port`` (default 8443), with
``ssl.SSLContext`` from ``WorkloadPlane.server_ssl_context()``
(``CERT_REQUIRED``, TLS 1.3, backed by the current SVID + SPIRE trust
bundle -- a fake or foreign-CA client certificate is rejected at the TLS
handshake itself, before any Python request-handling code runs).

**The uvicorn/SSLContext-vs-rotating-SVID gotcha (SS7.3), and the
approach chosen:** uvicorn's ``Config`` normally only builds an
``SSLContext`` from ``ssl_certfile``/``ssl_keyfile`` *file paths*, but
``WorkloadPlane.server_ssl_context()`` returns a ready, in-memory
``ssl.SSLContext`` backed by SVID material that agentweave rotates
automatically. This module's approach: call ``Config.load()`` once
(with no ssl file paths configured, so it sets ``Config.ssl = None``),
then assign the workload SSLContext onto ``Config.ssl`` directly before
``Server.startup()`` -- ``asyncio.loop.create_server(..., ssl=config.ssl)``
captures that ``SSLContext`` *object* by reference when the listening
socket is created. Critically, a rotation callback (registered via
``forge_security.workload.register_rotation_callback``) reloads new
cert/key material onto that **same** ``SSLContext`` object
(``ctx.load_cert_chain(...)`` again, with fresh temp files) rather than
building a brand-new context -- because asyncio holds a reference to the
original object, mutating it in place is what actually takes effect for
new connections without restarting the listener (already-established
connections are unaffected, which is correct: a live connection's peer
cert doesn't change mid-session). This was chosen over (a) switching to
hypercorn -- evaluated and rejected: this hypercorn version implements
no ASGI TLS/client-cert extension either, so it would not solve the
harder half of this problem (see the peer-cert-extraction note below)
while adding a new production dependency -- and over (c) unconditionally
rebuilding the whole listener on every rotation, which would drop
in-flight connections for no benefit once (b) is available.

**How the peer SPIFFE ID is extracted from the verified mTLS cert:**
uvicorn's ASGI ``scope`` carries no client-certificate extension (there
is no ASGI TLS extension in this uvicorn/ecosystem version), so a
minimal custom HTTP/1.1 protocol, :class:`_WorkloadPeerCertProtocol`
(subclassing uvicorn's own ``H11Protocol``), reads the peer certificate
once per TCP connection straight off the transport
(``transport.get_extra_info("ssl_object").getpeercert(binary_form=True)``
-- the same primitive Python's own ``ssl`` module exposes everywhere),
parses the ``spiffe://`` URI SAN via
``forge_security.workload.extract_spiffe_id_from_cert`` (the identical
SAN-parsing logic the server-side mTLS resolver already uses), and
injects the result into every request's
``scope["extensions"]["forge_workload"]["peer_spiffe_id"]``. This value
is *never* trusted on its own: by the time it is read, the TLS handshake
has already verified the certificate chains to the SPIRE trust bundle
(``CERT_REQUIRED``); this step only extracts *which* verified identity
presented itself, and :func:`forge_security.workload.WorkloadPlane.verify_peer`
still applies deny-by-default if no ``spiffe://`` SAN is present at all.

**The workload request flow (ADR-0004 SS3-SS5):**
``POST /a2a/tasks`` on ``:8443`` -> read the verified peer SPIFFE ID from
``scope["extensions"]`` -> ``plane.verify_peer(peer_spiffe_id)`` ->
``Principal(kind="workload", spiffe_id=...)`` -> ``plane.authorize(principal,
action="a2a:task", resource=<this agent's own SPIFFE ID>, context={task_type,
tool, peer_trust_level})`` (``peer_trust_level`` looked up from
``agents.peers[].trust_level`` when the caller matches a configured peer's
``spiffe_id``) -> a deny (or an unreachable OPA -- fail closed) raises 403 ->
otherwise dispatch via ``forge_gateway.routes.a2a.run_a2a_task``, the same
agent-dispatch code the human plane uses. There is no ``caller_id`` field
read from the request body anywhere on this path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, HTTPException, Request
from forge_security.workload import (
    WorkloadForbidden,
    WorkloadUnauthenticated,
    extract_spiffe_id_from_cert,
    register_rotation_callback,
)
from uvicorn.protocols.http.h11_impl import H11Protocol

from forge_gateway.routes.a2a import A2ATaskRequest, A2ATaskResponse, run_a2a_task

if TYPE_CHECKING:
    import ssl

    from forge_config.schema import ForgeConfig
    from forge_security.workload import WorkloadPlane

logger = logging.getLogger("forge.gateway.workload")

_A2A_TASK_ACTION = "a2a:task"
_SCOPE_EXTENSION_KEY = "forge_workload"
_SVID_KEY_FILE_MODE = 0o600
_DEFAULT_LISTENER_PORT = 8443


def _extract_peer_spiffe_id_from_transport(transport: asyncio.Transport) -> str | None:
    """Extract the verified peer SPIFFE ID once per TCP connection.

    Called from :meth:`_WorkloadPeerCertProtocol.connection_made` --
    i.e. strictly *after* the TLS handshake has already validated the
    peer certificate against the SPIRE trust bundle (``CERT_REQUIRED``
    on the listener's ``SSLContext``). Never raises; returns ``None`` for
    a non-TLS transport or a peer that presented no certificate, which
    :func:`forge_security.workload.resolve_workload_principal` (invoked
    downstream via ``WorkloadPlane.verify_peer``) then treats as an
    unknown identity -- deny-by-default, never a bypass.
    """
    ssl_object = transport.get_extra_info("ssl_object")
    if ssl_object is None:
        return None
    try:
        cert_der = ssl_object.getpeercert(binary_form=True)
    except Exception:
        logger.warning("workload plane: could not read peer certificate from mTLS connection")
        return None
    if not cert_der:
        return None
    return extract_spiffe_id_from_cert(cert_der)


class _WorkloadPeerCertProtocol(H11Protocol):
    """Custom uvicorn HTTP/1.1 protocol for the ``:8443`` a2a-mtls
    listener -- see the module docstring for why this class exists.

    The *only* workload-plane-specific behavior is here: capturing the
    verified peer SPIFFE ID once per connection and injecting it into
    every request's ASGI scope. Everything else (HTTP/1.1 parsing,
    keep-alive, flow control, ...) is inherited unchanged from uvicorn's
    ``H11Protocol``, so this stays a thin, version-tolerant wrapper
    rather than a reimplementation of HTTP handling.
    """

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        peer_spiffe_id = _extract_peer_spiffe_id_from_transport(transport)
        self.app = wrap_app_with_peer_identity(self.app, peer_spiffe_id)


def wrap_app_with_peer_identity(app: Any, peer_spiffe_id: str | None) -> Any:
    """Wrap an ASGI *app* so every ``"http"`` scope it receives carries
    *peer_spiffe_id* under ``scope["extensions"]["forge_workload"]``.

    Factored out of :meth:`_WorkloadPeerCertProtocol.connection_made` so
    it's independently unit-testable (and reusable in tests that drive
    the workload ASGI app directly via ``httpx.ASGITransport`` without a
    real TCP/TLS connection).
    """

    async def _wrapped(scope: Any, receive: Any, send: Any) -> Any:
        if scope["type"] == "http":
            scope = dict(scope)
            scope["extensions"] = {
                **scope.get("extensions", {}),
                _SCOPE_EXTENSION_KEY: {"peer_spiffe_id": peer_spiffe_id},
            }
        return await app(scope, receive, send)

    return _wrapped


def peer_spiffe_id_from_request(request: Request) -> str | None:
    """Read the verified peer SPIFFE ID injected by
    :class:`_WorkloadPeerCertProtocol` for *request*'s connection.
    ``None`` when the peer presented no usable SPIFFE identity (or, in
    tests, when the request didn't go through the custom protocol at
    all) -- callers must treat this as unauthenticated, never fall back
    to any other identity source."""
    extensions = request.scope.get("extensions") or {}
    workload_extension = extensions.get(_SCOPE_EXTENSION_KEY) or {}
    peer_spiffe_id: str | None = workload_extension.get("peer_spiffe_id")
    return peer_spiffe_id


def _peer_trust_level(config: ForgeConfig | None, peer_spiffe_id: str) -> str | None:
    """Look up ``agents.peers[].trust_level`` for the configured peer
    whose ``spiffe_id`` matches *peer_spiffe_id* (ADR-0004 SS5). ``None``
    when the caller doesn't match any configured peer -- OPA's policy is
    responsible for deciding whether an unknown peer's call is allowed."""
    if config is None:
        return None
    for peer in config.agents.peers:
        if peer.spiffe_id == peer_spiffe_id:
            return peer.trust_level.value
    return None


def build_workload_app(
    plane: WorkloadPlane,
    agent: Any,
    config: ForgeConfig | None,
) -> FastAPI:
    """Build the minimal ASGI app served on the ``:8443`` a2a-mtls
    listener (ADR-0004 SS3): only the workload A2A surface, with the
    caller's identity coming *exclusively* from the verified mTLS peer
    certificate -- never a header, query parameter, or body field. No
    docs/OpenAPI endpoints (this is an east-west-only surface, not a
    public API).
    """
    app = FastAPI(
        title="Forge Workload A2A",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post("/a2a/tasks", response_model=A2ATaskResponse)
    async def submit_workload_task(request: Request, task: A2ATaskRequest) -> A2ATaskResponse:
        peer_spiffe_id = peer_spiffe_id_from_request(request)

        try:
            principal = await plane.verify_peer(peer_spiffe_id)
        except WorkloadUnauthenticated as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc

        my_spiffe_id = await plane.my_spiffe_id()
        context = {
            "task_type": task.task_type,
            "tool": None,
            "peer_trust_level": _peer_trust_level(config, peer_spiffe_id or ""),
        }
        try:
            await plane.authorize(principal, _A2A_TASK_ACTION, my_spiffe_id, context)
        except WorkloadForbidden as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc

        return await run_a2a_task(agent, task)

    return app


async def _reload_ssl_context_on_rotation(
    ctx: ssl.SSLContext,
    temp_dir: tempfile.TemporaryDirectory[str],
    svid: Any,
) -> None:
    """Registered as a rotation callback (ADR-0004 SS7.3): reloads the
    newly-rotated SVID's cert/key onto *ctx* -- the SAME ``SSLContext``
    object already bound to the running listener's socket -- so new
    connections pick up the rotated material without restarting the
    listener. See the module docstring for why in-place mutation (rather
    than swapping the context reference) is what actually takes effect.
    """
    unique = uuid.uuid4().hex
    cert_path = Path(temp_dir.name) / f"cert-{unique}.pem"
    key_path = Path(temp_dir.name) / f"key-{unique}.pem"
    # The real spiffe.X509Svid (py-spiffe 0.2.4+) has no
    # cert_chain_bytes/private_key_bytes attributes -- only cert_chain
    # (list[Certificate]), private_key, leaf, and save(). save() writes
    # the full cert chain PEM to cert_path and the private key PEM to
    # key_path, matching agentweave/identity/spiffe.py::_write_svid_to_files.
    svid.save(str(cert_path), str(key_path), serialization.Encoding.PEM)
    os.chmod(key_path, _SVID_KEY_FILE_MODE)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    logger.info("workload plane: :8443 listener SVID rotated and reloaded")


@dataclass
class WorkloadListenerHandle:
    """Handle for the running ``:8443`` a2a-mtls listener, returned by
    :func:`start_workload_listener`."""

    server: uvicorn.Server
    task: asyncio.Task[None]
    ssl_context: ssl.SSLContext
    temp_dir: tempfile.TemporaryDirectory[str]

    @property
    def bound_port(self) -> int:
        """The actual bound port (resolves ``port=0`` to the OS-assigned
        port, used by tests)."""
        sockets = self.server.servers[0].sockets
        port: int = sockets[0].getsockname()[1]
        return port

    async def stop(self) -> None:
        """Gracefully stop the listener: signal exit, wait for the
        request-serving loop to notice, then run uvicorn's shutdown
        sequence and clean up the SVID temp-file directory. Deliberately
        does **not** use ``uvicorn.Server.serve()``/``capture_signals()``
        (see :func:`start_workload_listener`), so nothing here touches
        process-wide ``SIGINT``/``SIGTERM`` handlers -- the human
        ``:8000`` server's own signal handling is completely unaffected
        by starting or stopping this listener.
        """
        self.server.should_exit = True
        try:
            await asyncio.wait_for(self.task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            self.task.cancel()
        await self.server.shutdown()
        self.temp_dir.cleanup()


class WorkloadListenerStartupError(Exception):
    """Raised when the ``:8443`` listener fails to bind (e.g. the port is
    already in use). Distinct from
    ``forge_security.workload.WorkloadUnavailable`` (SPIRE-unreachable) --
    both result in the same outcome (no workload listener, human plane
    unaffected), but this one is a local networking failure, not an
    identity-infrastructure one."""


def _resolve_listener_port(config: ForgeConfig | None) -> int:
    """The configured ``security.agentweave.workload_listener_port``, or
    the ADR-0004 default (8443) when no config is loaded at all."""
    if config is None:
        return _DEFAULT_LISTENER_PORT
    return config.security.agentweave.workload_listener_port


async def start_workload_listener(
    plane: WorkloadPlane,
    agent: Any,
    config: ForgeConfig | None,
    *,
    host: str = "0.0.0.0",  # noqa: S104 -- intentional pod-wide bind, matches :8000
    port: int | None = None,
) -> WorkloadListenerHandle:
    """Start the ``:8443`` a2a-mtls listener as a background task in the
    current process (ADR-0004 SS3, SS7.3). See the module docstring for
    the chosen uvicorn/SSLContext approach and the peer-cert-extraction
    mechanism.

    Deliberately calls ``Server.startup()`` + ``Server.main_loop()``
    directly rather than ``Server.serve()``: ``serve()`` wraps the call
    in ``capture_signals()``, which installs process-wide
    ``SIGINT``/``SIGTERM`` handlers -- since the primary ``:8000``
    server (started by the external ``uvicorn`` CLI entrypoint, see
    ``Dockerfile``) already owns those handlers for the process's
    lifetime, a second ``serve()`` call from within this app's lifespan
    would silently steal graceful-shutdown handling away from the human
    plane. Calling the lower-level methods avoids touching signal
    handling at all.

    Raises:
        WorkloadListenerStartupError: the listener could not bind (e.g.
            the port is already in use). Callers (the app lifespan) must
            treat this exactly like ``WorkloadUnavailable``: log and
            continue without a workload listener, human plane unaffected.
    """
    resolved_port = port if port is not None else _resolve_listener_port(config)

    ssl_context = await plane.server_ssl_context()
    app = build_workload_app(plane, agent, config)

    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=resolved_port,
        http=_WorkloadPeerCertProtocol,
        lifespan="off",
        log_level="warning",
    )
    uvicorn_config.load()
    uvicorn_config.ssl = ssl_context

    server = uvicorn.Server(uvicorn_config)
    # ``Server.startup()`` reads ``self.lifespan`` (normally set by
    # ``Server._serve()``, which this module deliberately bypasses -- see
    # the docstring above). ``lifespan="off"`` above means
    # ``LifespanOff.startup()``/``.shutdown()`` are no-ops, so this is
    # just satisfying the attribute, not actually running any lifespan.
    server.lifespan = uvicorn_config.lifespan_class(uvicorn_config)

    try:
        await server.startup()
    except SystemExit as exc:
        raise WorkloadListenerStartupError(
            f"workload plane: :8443 listener failed to bind on {host}:{resolved_port}"
        ) from exc

    if not server.started:
        raise WorkloadListenerStartupError(
            f"workload plane: :8443 listener failed to start on {host}:{resolved_port}"
        )

    task = asyncio.ensure_future(server.main_loop())
    temp_dir = tempfile.TemporaryDirectory(prefix="forge_workload_svid_")

    async def _on_rotation(svid: Any) -> None:
        await _reload_ssl_context_on_rotation(ssl_context, temp_dir, svid)

    register_rotation_callback(plane.identity, _on_rotation)

    logger.info("workload plane: :8443 a2a-mtls listener started on %s:%d", host, resolved_port)

    return WorkloadListenerHandle(
        server=server,
        task=task,
        ssl_context=ssl_context,
        temp_dir=temp_dir,
    )
