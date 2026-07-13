"""Tests for the ADR-0004 workload (SPIFFE + OPA + mTLS) A2A plane.

Named per ADR-0004 SS12 (forge-gateway task breakdown). Uses real
self-signed certs (``_mtls_fixtures``) for the SSL-layer handshake test
and directly-constructed ``WorkloadPlane`` fakes (mirroring
``forge-security/tests/test_workload_plane.py``) for everything above
the TLS layer, so none of this needs a real SPIRE/OPA.
"""

from __future__ import annotations

import asyncio
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import spiffe
from _mtls_fixtures import (
    build_client_ssl_context,
    build_server_ssl_context,
    cert_der,
    issue_leaf_cert,
    make_ca,
)
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import AgentsConfig, ForgeConfig, PeerAgent, TrustLevel
from forge_gateway.workload import (
    WorkloadListenerStartupError,
    _extract_peer_spiffe_id_from_transport,
    _reload_ssl_context_on_rotation,
    build_workload_app,
    start_workload_listener,
    wrap_app_with_peer_identity,
)
from forge_security.workload.providers import WorkloadPlane
from httpx import ASGITransport, AsyncClient

CALLER_SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/caller"
MY_SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/default"


# ---------------------------------------------------------------------------
# Fakes -- mirrors forge-security/tests/test_workload_plane.py
# ---------------------------------------------------------------------------


class _FakeIdentity:
    def __init__(self, spiffe_id: str = MY_SPIFFE_ID) -> None:
        self._spiffe_id = spiffe_id
        self.registered_rotation_callbacks: list[object] = []

    async def get_identity(self) -> str:
        return self._spiffe_id

    async def create_tls_context(self, server: bool = False) -> str:
        return f"ssl-context(server={server})"

    async def health_check(self) -> bool:
        return True

    def register_rotation_callback(self, callback: object) -> None:
        self.registered_rotation_callbacks.append(callback)


@dataclass
class _Decision:
    allowed: bool
    reason: str = ""


@dataclass
class _FakeAuthz:
    allowed: bool
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def check(
        self, caller_id: str, resource: str, action: str, context: dict[str, Any] | None = None
    ) -> _Decision:
        self.calls.append(
            {"caller_id": caller_id, "resource": resource, "action": action, "context": context}
        )
        return _Decision(allowed=self.allowed, reason="fixture decision")

    async def health_check(self) -> bool:
        return True


@dataclass
class _RecordingAudit:
    peer_verifications: list[dict[str, Any]] = field(default_factory=list)
    auth_checks: list[dict[str, Any]] = field(default_factory=list)

    async def record_peer_verification(self, peer_id: str, status: str, reason: str = "") -> None:
        self.peer_verifications.append({"peer_id": peer_id, "status": status, "reason": reason})

    async def record_auth_check(
        self,
        caller_id: str,
        action: str,
        resource: str,
        decision: str,
        duration: float,
        reason: str = "",
    ) -> None:
        self.auth_checks.append({"caller_id": caller_id, "decision": decision, "reason": reason})


def _plane(*, allow: bool = True, identity: Any | None = None) -> tuple[WorkloadPlane, _FakeAuthz]:
    authz = _FakeAuthz(allowed=allow)
    plane = WorkloadPlane(
        identity=identity or _FakeIdentity(), authz=authz, audit=_RecordingAudit()
    )
    return plane, authz


def _mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_structured.return_value = ForgeRunResult(output={"processed": True})
    return agent


def _config_with_peer(trust_level: TrustLevel = TrustLevel.HIGH) -> ForgeConfig:
    return ForgeConfig(
        agents=AgentsConfig(
            peers=[
                PeerAgent(
                    name="caller",
                    endpoint="https://caller.hvslocal:8443",
                    trust_level=trust_level,
                    spiffe_id=CALLER_SPIFFE_ID,
                )
            ]
        )
    )


# ---------------------------------------------------------------------------
# The load-bearing SSL-layer test: no cert / a foreign-CA cert cannot
# complete the :8443 handshake at all.
# ---------------------------------------------------------------------------


async def _attempt_handshake(
    port: int, client_ctx: ssl.SSLContext, *, server_hostname: str = "server"
) -> bool:
    """Attempt an mTLS connection + a request/response round trip against
    the given ``port``. Returns ``True`` only if the server actually
    accepted the connection AND echoed data back -- a rejected handshake
    manifests here as either an outright exception from
    ``open_connection`` *or* a connection that "succeeds" at the socket
    level but is immediately closed by the server before any bytes cross
    (Python/asyncio's TLS implementation delivers a CERT_REQUIRED
    rejection as connection teardown, not always as a raised exception
    from ``open_connection`` itself) -- so this checks both.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                "127.0.0.1", port, ssl=client_ctx, server_hostname=server_hostname
            ),
            timeout=5.0,
        )
    except (ssl.SSLError, ConnectionError, OSError):
        return False

    try:
        writer.write(b"ping")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(4), timeout=2.0)
    except (ssl.SSLError, ConnectionError, OSError, TimeoutError):
        return False
    finally:
        writer.close()

    return data == b"pong"


class TestMTLSHandshakeRejectsUnverifiedPeers:
    """ADR-0004 SS3/SS8, the load-bearing property: a client presenting
    no certificate, or one signed by a foreign CA, cannot complete the
    :8443 handshake -- the request is rejected at the TLS layer, before
    the server-side echo handler (standing in for the ASGI app) ever
    runs."""

    @staticmethod
    async def _run_echo_server(server_ctx: ssl.SSLContext) -> tuple[asyncio.Server, asyncio.Event]:
        handled = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            handled.set()
            await reader.read(4)
            writer.write(b"pong")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, host="127.0.0.1", port=0, ssl=server_ctx)
        return server, handled

    async def test_a2a_mtls_requires_client_cert(self) -> None:
        """A client presenting NO certificate cannot complete the TLS
        handshake against a CERT_REQUIRED server context -- rejected
        before any request is ever processed."""
        ca = make_ca()
        server_leaf = issue_leaf_cert(ca, common_name="server", spiffe_id=MY_SPIFFE_ID)
        server_ctx = build_server_ssl_context(server_leaf, ca)
        server, handled = await self._run_echo_server(server_ctx)
        port = server.sockets[0].getsockname()[1]

        try:
            no_cert_client_ctx = build_client_ssl_context(ca, leaf=None)
            succeeded = await _attempt_handshake(port, no_cert_client_ctx)

            assert succeeded is False
            assert not handled.is_set()
        finally:
            server.close()
            await server.wait_closed()

    async def test_a2a_mtls_rejects_foreign_ca_client_cert(self) -> None:
        """A client certificate signed by a DIFFERENT CA than the one the
        server trusts cannot complete the handshake either -- a forged
        or foreign-CA SVID is rejected at the TLS layer, not the app."""
        ca = make_ca("real-spire-ca")
        foreign_ca = make_ca("foreign-ca")
        server_leaf = issue_leaf_cert(ca, common_name="server", spiffe_id=MY_SPIFFE_ID)
        server_ctx = build_server_ssl_context(server_leaf, ca)
        server, handled = await self._run_echo_server(server_ctx)
        port = server.sockets[0].getsockname()[1]

        try:
            foreign_leaf = issue_leaf_cert(
                foreign_ca, common_name="impostor", spiffe_id=CALLER_SPIFFE_ID
            )
            foreign_client_ctx = build_client_ssl_context(ca, leaf=foreign_leaf)
            succeeded = await _attempt_handshake(port, foreign_client_ctx)

            assert succeeded is False
            assert not handled.is_set()
        finally:
            server.close()
            await server.wait_closed()

    async def test_a2a_mtls_accepts_a_valid_trusted_client_cert(self) -> None:
        """Sanity check for the two rejection tests above: a client cert
        signed by the SAME trusted CA completes the handshake fine."""
        ca = make_ca()
        server_leaf = issue_leaf_cert(ca, common_name="server", spiffe_id=MY_SPIFFE_ID)
        server_ctx = build_server_ssl_context(server_leaf, ca)
        server, handled = await self._run_echo_server(server_ctx)
        port = server.sockets[0].getsockname()[1]

        try:
            client_leaf = issue_leaf_cert(ca, common_name="caller", spiffe_id=CALLER_SPIFFE_ID)
            client_ctx = build_client_ssl_context(ca, leaf=client_leaf)
            succeeded = await _attempt_handshake(port, client_ctx)

            assert succeeded is True
            assert handled.is_set()
        finally:
            server.close()
            await server.wait_closed()


# ---------------------------------------------------------------------------
# Peer SPIFFE ID extraction from the verified transport/cert.
# ---------------------------------------------------------------------------


class TestExtractPeerSpiffeIdFromTransport:
    def test_extracts_spiffe_id_from_verified_peer_cert(self) -> None:
        ca = make_ca()
        leaf = issue_leaf_cert(ca, common_name="caller", spiffe_id=CALLER_SPIFFE_ID)

        class _FakeSSLObject:
            def getpeercert(self, binary_form: bool = False) -> bytes:
                assert binary_form is True
                return cert_der(leaf)

        class _FakeTransport:
            def get_extra_info(self, name: str) -> object:
                assert name == "ssl_object"
                return _FakeSSLObject()

        result = _extract_peer_spiffe_id_from_transport(_FakeTransport())  # type: ignore[arg-type]
        assert result == CALLER_SPIFFE_ID

    def test_returns_none_for_non_tls_transport(self) -> None:
        class _PlainTransport:
            def get_extra_info(self, name: str) -> object:
                return None

        assert _extract_peer_spiffe_id_from_transport(_PlainTransport()) is None  # type: ignore[arg-type]

    def test_returns_none_when_getpeercert_raises(self) -> None:
        class _ExplodingSSLObject:
            def getpeercert(self, binary_form: bool = False) -> bytes:
                raise ValueError("boom")

        class _FakeTransport:
            def get_extra_info(self, name: str) -> object:
                return _ExplodingSSLObject()

        assert _extract_peer_spiffe_id_from_transport(_FakeTransport()) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The workload ASGI app: verify -> authorize -> dispatch.
# ---------------------------------------------------------------------------


async def _post_task(app: Any, peer_spiffe_id: str | None, task_type: str = "ping") -> Any:
    wrapped = wrap_app_with_peer_identity(app, peer_spiffe_id)
    transport = ASGITransport(app=wrapped)
    async with AsyncClient(transport=transport, base_url="https://workload") as client:
        return await client.post("/a2a/tasks", json={"task_type": task_type, "payload": {}})


class TestWorkloadA2ATaskEndpoint:
    async def test_verified_peer_and_opa_allow_completes_the_task(self) -> None:
        """A verified peer + an OPA-allow decision completes
        POST /a2a/tasks on the workload path."""
        plane, authz = _plane(allow=True)
        agent = _mock_agent()
        config = _config_with_peer()
        app = build_workload_app(plane, agent, config)

        response = await _post_task(app, CALLER_SPIFFE_ID)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        agent.run_structured.assert_awaited_once()
        # The OPA context carries the configured peer's trust_level.
        assert authz.calls[0]["context"]["peer_trust_level"] == "high"
        assert authz.calls[0]["context"]["task_type"] == "ping"

    async def test_opa_deny_returns_403(self) -> None:
        plane, _authz = _plane(allow=False)
        agent = _mock_agent()
        app = build_workload_app(plane, agent, _config_with_peer())

        response = await _post_task(app, CALLER_SPIFFE_ID)

        assert response.status_code == 403
        agent.run_structured.assert_not_awaited()

    async def test_no_peer_identity_returns_401(self) -> None:
        """No SPIFFE SAN on the (already TLS-verified) peer cert --
        deny-by-default, never proceed with an unknown identity."""
        plane, _authz = _plane(allow=True)
        agent = _mock_agent()
        app = build_workload_app(plane, agent, _config_with_peer())

        response = await _post_task(app, None)

        assert response.status_code == 401
        agent.run_structured.assert_not_awaited()

    async def test_unconfigured_peer_has_no_trust_level_but_can_still_be_evaluated(self) -> None:
        """A caller that doesn't match any configured peer gets
        peer_trust_level=None in the OPA context -- OPA's policy decides
        whether that's allowed, this code doesn't pre-judge it."""
        plane, authz = _plane(allow=True)
        agent = _mock_agent()
        app = build_workload_app(plane, agent, ForgeConfig())

        response = await _post_task(app, "spiffe://hvslocal/ns/dev/sa/unknown-peer")

        assert response.status_code == 200
        assert authz.calls[0]["context"]["peer_trust_level"] is None

    async def test_resource_is_this_agents_own_spiffe_id(self) -> None:
        plane, authz = _plane(allow=True, identity=_FakeIdentity(spiffe_id=MY_SPIFFE_ID))
        agent = _mock_agent()
        app = build_workload_app(plane, agent, _config_with_peer())

        await _post_task(app, CALLER_SPIFFE_ID)

        assert authz.calls[0]["resource"] == MY_SPIFFE_ID
        assert authz.calls[0]["action"] == "a2a:task"

    async def test_audit_event_emitted_for_workload_authz(self) -> None:
        plane, _authz = _plane(allow=True)
        agent = _mock_agent()
        app = build_workload_app(plane, agent, _config_with_peer())

        await _post_task(app, CALLER_SPIFFE_ID)

        assert plane.audit.peer_verifications == [
            {"peer_id": CALLER_SPIFFE_ID, "status": "success", "reason": ""}
        ]
        assert plane.audit.auth_checks[0]["decision"] == "allow"


# ---------------------------------------------------------------------------
# Full listener lifecycle: real mTLS end to end.
# ---------------------------------------------------------------------------


class TestStartWorkloadListenerEndToEnd:
    async def test_full_mtls_round_trip_allow(self) -> None:
        """A real client, over a real mTLS socket, completes a task
        against a real (ephemeral-port) :8443-style listener."""
        ca = make_ca()
        server_leaf = issue_leaf_cert(ca, common_name="forge", spiffe_id=MY_SPIFFE_ID)
        client_leaf = issue_leaf_cert(ca, common_name="caller", spiffe_id=CALLER_SPIFFE_ID)

        class _RealIdentity(_FakeIdentity):
            async def create_tls_context(self, server: bool = False) -> ssl.SSLContext:
                if server:
                    return build_server_ssl_context(server_leaf, ca)
                return build_client_ssl_context(ca, leaf=client_leaf)

        plane, authz = _plane(allow=True, identity=_RealIdentity())
        agent = _mock_agent()
        config = _config_with_peer()

        handle = await start_workload_listener(plane, agent, config, host="127.0.0.1", port=0)
        try:
            client_ctx = await plane.identity.create_tls_context(server=False)
            async with AsyncClient(
                verify=client_ctx,
                base_url=f"https://127.0.0.1:{handle.bound_port}",
            ) as client:
                response = await client.post(
                    "/a2a/tasks",
                    json={"task_type": "ping", "payload": {}},
                )
            assert response.status_code == 200
            assert response.json()["status"] == "completed"
            assert authz.calls[0]["caller_id"] == CALLER_SPIFFE_ID
        finally:
            await handle.stop()

    async def test_listener_startup_error_when_port_already_in_use(self) -> None:
        ca = make_ca()
        server_leaf = issue_leaf_cert(ca, common_name="forge", spiffe_id=MY_SPIFFE_ID)

        class _RealIdentity(_FakeIdentity):
            async def create_tls_context(self, server: bool = False) -> ssl.SSLContext:
                return build_server_ssl_context(server_leaf, ca)

        plane, _authz = _plane(allow=True, identity=_RealIdentity())
        agent = _mock_agent()

        handle = await start_workload_listener(plane, agent, None, host="127.0.0.1", port=0)
        try:
            with pytest.raises(WorkloadListenerStartupError):
                await start_workload_listener(
                    plane, agent, None, host="127.0.0.1", port=handle.bound_port
                )
        finally:
            await handle.stop()

    async def test_rotation_callback_is_registered_with_identity(self) -> None:
        ca = make_ca()
        server_leaf = issue_leaf_cert(ca, common_name="forge", spiffe_id=MY_SPIFFE_ID)

        class _RealIdentity(_FakeIdentity):
            async def create_tls_context(self, server: bool = False) -> ssl.SSLContext:
                return build_server_ssl_context(server_leaf, ca)

        identity = _RealIdentity()
        plane, _authz = _plane(allow=True, identity=identity)
        agent = _mock_agent()

        handle = await start_workload_listener(plane, agent, None, host="127.0.0.1", port=0)
        try:
            assert len(identity.registered_rotation_callbacks) == 1
        finally:
            await handle.stop()


# ---------------------------------------------------------------------------
# SVID rotation callback -- the real spiffe.X509Svid API (regression test
# for the live-bug AttributeError: 'X509Svid' object has no attribute
# 'cert_chain_bytes').
# ---------------------------------------------------------------------------


class TestRotationCallbackReloadsRealX509Svid:
    async def test_rotation_callback_reloads_context_with_real_x509svid(self) -> None:
        """Reproduces the live :8443 bug: py-spiffe's real ``X509Svid``
        (0.2.4/0.2.5) has no ``cert_chain_bytes``/``private_key_bytes``
        attributes -- only ``cert_chain``, ``private_key``, ``leaf``, and
        ``save()``. Pre-fix, ``_reload_ssl_context_on_rotation`` raised
        ``AttributeError`` on the very first SVID rotation, so the
        listener kept serving the original SSLContext's cert forever --
        which then failed mTLS handshakes once that SVID actually
        expired. This must NOT raise, and the reloaded context's on-disk
        cert file must contain the newly-rotated leaf, not the original
        one."""
        ca = make_ca()
        initial_leaf = issue_leaf_cert(ca, common_name="forge", spiffe_id=MY_SPIFFE_ID)
        rotated_leaf = issue_leaf_cert(ca, common_name="forge-rotated", spiffe_id=MY_SPIFFE_ID)

        ctx = build_server_ssl_context(initial_leaf, ca)

        svid = spiffe.X509Svid(
            spiffe.SpiffeId(MY_SPIFFE_ID),
            [rotated_leaf.cert],
            rotated_leaf.key,
        )

        temp_dir = tempfile.TemporaryDirectory(prefix="forge_workload_svid_test_")
        try:
            await _reload_ssl_context_on_rotation(ctx, temp_dir, svid)

            cert_files = list(Path(temp_dir.name).glob("cert-*.pem"))
            key_files = list(Path(temp_dir.name).glob("key-*.pem"))
            assert len(cert_files) == 1
            assert len(key_files) == 1

            written_cert_pem = cert_files[0].read_bytes()
            assert written_cert_pem == rotated_leaf.cert_pem
            assert written_cert_pem != initial_leaf.cert_pem

            key_mode = key_files[0].stat().st_mode & 0o777
            assert key_mode == 0o600
        finally:
            temp_dir.cleanup()
