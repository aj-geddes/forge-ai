"""Tests for forge_security.workload.mtls (ADR-0004 SS7.2, SS7.3).

``extract_spiffe_id_from_cert`` mirrors agentweave's
``SecureChannel._extract_spiffe_id_from_cert`` -- the SAN-parsing logic
that turns a verified peer certificate into a ``spiffe://`` identity
string (or ``None`` when no such SAN exists, which the resolver then
treats as an unknown/rejected identity).
"""

from __future__ import annotations

from _workload_fixtures import make_cert_der
from forge_security.workload.mtls import (
    extract_spiffe_id_from_cert,
    register_rotation_callback,
    server_ssl_context,
)

SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/default"


class TestExtractSpiffeIdFromCert:
    def test_extracts_spiffe_uri_san(self):
        der = make_cert_der(spiffe_id=SPIFFE_ID)
        assert extract_spiffe_id_from_cert(der) == SPIFFE_ID

    def test_returns_none_when_no_spiffe_san_present(self):
        der = make_cert_der(spiffe_id=None, dns_name="not-spiffe.example.com")
        assert extract_spiffe_id_from_cert(der) is None

    def test_returns_none_when_no_san_extension_at_all(self):
        der = make_cert_der(spiffe_id=None)
        assert extract_spiffe_id_from_cert(der) is None

    def test_returns_none_for_unparseable_bytes(self):
        assert extract_spiffe_id_from_cert(b"not a certificate") is None


class _FakeIdentity:
    def __init__(self) -> None:
        self.create_tls_context_calls: list[bool] = []

    async def create_tls_context(self, server: bool = False):
        self.create_tls_context_calls.append(server)
        return "fake-ssl-context"


class TestServerSslContext:
    async def test_delegates_to_identity_create_tls_context_server_true(self):
        identity = _FakeIdentity()
        ctx = await server_ssl_context(identity)
        assert ctx == "fake-ssl-context"
        assert identity.create_tls_context_calls == [True]


class _IdentityWithRotation:
    def __init__(self) -> None:
        self.registered: list[object] = []

    def register_rotation_callback(self, callback: object) -> None:
        self.registered.append(callback)


class _IdentityWithoutRotation:
    """Shaped like agentweave's mock identity provider, which has no
    ``register_rotation_callback`` method."""


class TestRegisterRotationCallback:
    def test_registers_when_identity_supports_it(self):
        identity = _IdentityWithRotation()

        async def _cb(_svid: object) -> None:
            pass

        register_rotation_callback(identity, _cb)
        assert identity.registered == [_cb]

    def test_is_a_noop_when_identity_has_no_rotation_support(self):
        identity = _IdentityWithoutRotation()

        async def _cb(_svid: object) -> None:
            pass

        # Must not raise -- agentweave's MockIdentityProvider (test_mode)
        # has no register_rotation_callback method.
        register_rotation_callback(identity, _cb)
