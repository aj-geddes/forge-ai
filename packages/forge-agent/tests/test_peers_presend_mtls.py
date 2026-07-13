"""Reproduction of the AgentWeave workload-plane security review's PoC
(ADR-0004 SS6): a real, local mTLS-capable TLS server stands in for a
peer agent, and :class:`PeerCaller` talks to it over genuine TLS
connections -- no mocked ``httpx`` transport -- so these tests prove the
actual network behavior, not just that the right functions were called.

The review proved that ``httpx``'s ``client.post(...)`` connects AND
sends the full request body in one call, so verifying the peer only
*after* ``post()`` returns means a complete task payload can already
have reached a wrong-but-trust-domain-valid peer before the mismatch is
even detected. The fix (``peers.py``) performs a handshake-only TLS
probe -- using the exact same mTLS ``SSLContext`` the real request would
use -- and verifies the peer's SPIFFE ID *before* the real request (and
its payload) is ever sent.

Both the "correct" and "impostor" leaf certificates below are signed by
the *same* CA, so the TLS handshake itself succeeds for both -- exactly
the "wrong-but-trust-domain-valid peer" scenario from the review: the
peer holds a certificate any SPIRE-issued trust bundle for this domain
would accept, but it is not the specific peer that was pinned.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import ssl
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from forge_agent.agent.peers import PeerCaller, PeerCallError, PeerVerificationError
from forge_config.schema import PeerAgent, TrustLevel

_A2A_SUCCESS_BODY = b'{"status": "completed", "result": {"ok": true}, "error": null}'
_PROBE_READ_TIMEOUT = 0.5


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _self_signed_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge-test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(minutes=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _issue_leaf(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, spiffe_id: str
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Issue a leaf certificate, signed by *ca_key*/*ca_cert*, carrying
    *spiffe_id* as its sole SAN URI -- mirroring a real SPIRE-issued
    X.509 SVID's shape closely enough for SPIFFE-SAN extraction."""
    key = _generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge-test-leaf")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(minutes=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id)]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _write_pem(path: Path, key: rsa.RSAPrivateKey, cert: x509.Certificate) -> None:
    path.with_suffix(".crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.with_suffix(".key").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


class _RecordingPeerServer:
    """A minimal local TLS server standing in for a peer agent.

    Records every non-empty chunk of bytes it receives on any
    connection, so tests can assert -- from the *server's* point of
    view, not by mocking the client -- whether a task payload was ever
    actually transmitted. Responds to a well-formed A2A POST with a
    successful ``A2ATaskResponse`` body.
    """

    def __init__(self) -> None:
        self.connection_count = 0
        self.received_chunks: list[bytes] = []
        self._server: asyncio.AbstractServer | None = None

    @property
    def payload_was_sent(self) -> bool:
        return any(chunk for chunk in self.received_chunks)

    async def start(self, ssl_context: ssl.SSLContext) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=ssl_context)
        port = self._server.sockets[0].getsockname()[1]
        return port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connection_count += 1
        try:
            try:
                data = await asyncio.wait_for(reader.read(65536), timeout=_PROBE_READ_TIMEOUT)
            except TimeoutError:
                data = b""
            if data:
                self.received_chunks.append(data)
            if data.startswith(b"POST"):
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(_A2A_SUCCESS_BODY)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n" + _A2A_SUCCESS_BODY
                )
                writer.write(response)
                with contextlib.suppress(Exception):
                    await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def _client_ssl_context(ca_cert: x509.Certificate) -> ssl.SSLContext:
    """Build a client-side SSLContext equivalent to what
    ``identity.create_tls_context(server=False)`` produces: mandatory
    peer verification against the trust bundle, SPIFFE-style (no
    hostname check)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ca_cert.public_bytes(serialization.Encoding.PEM).decode())
    return ctx


def _server_ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


class TestPreSendPeerVerificationPoC:
    """THE KEY property: a task payload is NEVER transmitted to a peer
    whose verified SPIFFE ID differs from the pinned value."""

    @pytest.mark.anyio
    async def test_wrong_peer_never_receives_the_task_payload(self, tmp_path: Path) -> None:
        """Reproduces the review's PoC: a peer that is TLS-valid (signed
        by the same trust-domain CA) but presents the WRONG SPIFFE ID
        must receive NOTHING -- not even the pre-send probe leaks
        anything, let alone the task payload."""
        ca_key, ca_cert = _self_signed_ca()

        pinned_id = "spiffe://hvslocal/ns/dev/sa/data-forge"
        impostor_id = "spiffe://hvslocal/ns/dev/sa/impostor"

        leaf_key, leaf_cert = _issue_leaf(ca_key, ca_cert, impostor_id)
        base = tmp_path / "leaf"
        _write_pem(base, leaf_key, leaf_cert)
        server_ctx = _server_ssl_context(base.with_suffix(".crt"), base.with_suffix(".key"))

        server = _RecordingPeerServer()
        port = await server.start(server_ctx)
        caller: PeerCaller | None = None
        try:
            peer = PeerAgent(
                name="data-forge",
                endpoint=f"https://127.0.0.1:{port}",
                trust_level=TrustLevel.HIGH,
                spiffe_id=pinned_id,
            )

            identity = AsyncMock()
            identity.create_tls_context.return_value = _client_ssl_context(ca_cert)

            caller = PeerCaller([peer], identity=identity)

            sensitive_payload = {"secret": "do-not-leak-this-to-an-impostor"}
            with pytest.raises(PeerVerificationError):
                await caller.call_peer("data-forge", "task", sensitive_payload)

            # The security property: nothing was ever sent to the wrong
            # peer -- not the payload, not even a byte of an HTTP request.
            assert server.payload_was_sent is False
            assert server.received_chunks == []
            # Only the handshake-only probe connected; call_peer must
            # never have gone on to issue the real (payload-carrying)
            # request after the probe failed.
            assert server.connection_count == 1
        finally:
            if caller is not None:
                await caller.aclose()
            await server.stop()

    @pytest.mark.anyio
    async def test_correctly_pinned_peer_completes_normally(self, tmp_path: Path) -> None:
        """The mirror-image case: when the peer's verified SPIFFE ID
        DOES match the pinned value, the call proceeds and succeeds --
        pre-send verification must not false-positive-block a
        legitimate peer."""
        ca_key, ca_cert = _self_signed_ca()
        pinned_id = "spiffe://hvslocal/ns/dev/sa/data-forge"

        leaf_key, leaf_cert = _issue_leaf(ca_key, ca_cert, pinned_id)
        base = tmp_path / "leaf"
        _write_pem(base, leaf_key, leaf_cert)
        server_ctx = _server_ssl_context(base.with_suffix(".crt"), base.with_suffix(".key"))

        server = _RecordingPeerServer()
        port = await server.start(server_ctx)
        caller: PeerCaller | None = None
        try:
            peer = PeerAgent(
                name="data-forge",
                endpoint=f"https://127.0.0.1:{port}",
                trust_level=TrustLevel.HIGH,
                spiffe_id=pinned_id,
            )

            identity = AsyncMock()
            identity.create_tls_context.return_value = _client_ssl_context(ca_cert)

            caller = PeerCaller([peer], identity=identity)
            result = await caller.call_peer("data-forge", "task", {"sql": "SELECT 1"})

            assert result.status == "completed"
            assert result.result == {"ok": True}

            # The probe connects once (verification), then the real
            # request connects again and actually carries the payload.
            assert server.connection_count == 2
            assert server.payload_was_sent is True
            assert b"SELECT 1" in b"".join(server.received_chunks)
        finally:
            if caller is not None:
                await caller.aclose()
            await server.stop()

    @pytest.mark.anyio
    async def test_probe_connection_failure_raises_peer_call_error(self, tmp_path: Path) -> None:
        """When the pre-send probe itself can't even connect (peer down,
        wrong port, etc.), that must surface as PeerCallError, not hang
        or crash with a raw OSError -- and must never fall through to
        attempting the real send anyway."""
        ca_key, ca_cert = _self_signed_ca()
        pinned_id = "spiffe://hvslocal/ns/dev/sa/data-forge"

        peer = PeerAgent(
            name="data-forge",
            # Port 1 is (almost) always refused on localhost.
            endpoint="https://127.0.0.1:1",
            trust_level=TrustLevel.HIGH,
            spiffe_id=pinned_id,
        )

        identity = AsyncMock()
        identity.create_tls_context.return_value = _client_ssl_context(ca_cert)

        caller = PeerCaller([peer], identity=identity)
        try:
            with pytest.raises(PeerCallError):
                await caller.call_peer("data-forge", "task", {})
        finally:
            await caller.aclose()
