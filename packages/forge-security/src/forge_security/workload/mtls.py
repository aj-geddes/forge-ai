"""mTLS plumbing for the workload plane (ADR-0004 SS6, SS7.2, SS7.3).

Two narrow responsibilities, deliberately kept separate from
``resolver.py``:

- :func:`extract_spiffe_id_from_cert` -- parse the ``spiffe://`` URI SAN
  out of a peer's DER-encoded X.509 certificate. This mirrors
  agentweave's own ``SecureChannel._extract_spiffe_id_from_cert`` exactly
  (ADR-0004 SS4), so Forge's inbound extraction on the ``:8443`` listener
  matches the SDK's outbound peer-verification logic. This is the
  function a gateway-side ``PeerCertMiddleware`` (next stage, per
  ADR-0004 SS7.2) calls on the verified client certificate to obtain the
  string that ``resolver.resolve_workload_principal`` then consumes.
- :func:`server_ssl_context` / :func:`register_rotation_callback` -- thin
  wrappers over the identity provider's mTLS context and SVID-rotation
  hook, so callers (``WorkloadPlane``, the gateway's uvicorn wiring) never
  reach past this module into agentweave's identity API directly.
"""

from __future__ import annotations

import logging
import ssl
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from cryptography import x509

logger = logging.getLogger("forge.security.workload.mtls")

_SPIFFE_SCHEME = "spiffe://"


def extract_spiffe_id_from_cert(peer_cert_der: bytes) -> str | None:
    """Extract the ``spiffe://`` URI SAN from a DER-encoded certificate.

    Returns ``None`` -- never raises -- when the certificate cannot be
    parsed, has no Subject Alternative Name extension, or has a SAN with
    no ``spiffe://`` URI entry. Callers must treat ``None`` as an unknown
    identity (see :func:`forge_security.workload.resolver.resolve_workload_principal`),
    not as "no restriction."
    """
    try:
        cert = x509.load_der_x509_certificate(peer_cert_der)
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:
        logger.warning("workload plane: could not parse peer certificate for SPIFFE SAN")
        return None

    for name in san:
        if isinstance(name, x509.UniformResourceIdentifier) and name.value.startswith(
            _SPIFFE_SCHEME
        ):
            return name.value
    return None


class _IdentityProviderProtocol(Protocol):
    """The subset of agentweave's ``IdentityProvider`` this module needs."""

    async def create_tls_context(self, server: bool = False) -> ssl.SSLContext: ...


async def server_ssl_context(identity: _IdentityProviderProtocol) -> ssl.SSLContext:
    """Build the server-side mTLS ``SSLContext`` for the ``:8443``
    a2a-mtls listener (ADR-0004 SS3): ``CERT_REQUIRED``, TLS 1.3, backed
    by the identity provider's current SVID and SPIRE trust bundle."""
    context: ssl.SSLContext = await identity.create_tls_context(server=True)
    return context


def register_rotation_callback(identity: Any, callback: Callable[[Any], Awaitable[None]]) -> None:
    """Register ``callback`` to run whenever the identity provider's SVID
    rotates (ADR-0004 SS7.3), so the listener/audit trail can react to
    rotation.

    A no-op when ``identity`` doesn't support rotation callbacks -- true
    of agentweave's ``MockIdentityProvider`` in ``test_mode``, which has
    no ``register_rotation_callback`` method. Silently doing nothing here
    is safe (there is nothing to rotate in a mock), unlike the
    fail-closed identity/authz paths where silence would hide a real
    security decision.
    """
    register = getattr(identity, "register_rotation_callback", None)
    if register is None:
        logger.debug(
            "workload plane: identity provider %r does not support rotation callbacks",
            type(identity).__name__,
        )
        return
    register(callback)
