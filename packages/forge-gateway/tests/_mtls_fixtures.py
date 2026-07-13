"""Real-cert mTLS test helpers for the :8443 workload listener tests.

Builds a small self-signed CA plus server/client leaf certificates (with
or without a ``spiffe://`` URI SAN) so tests can exercise the actual TLS
handshake -- ``CERT_REQUIRED`` rejecting a client with no cert, or one
signed by a foreign CA, entirely at the socket layer -- without any real
SPIRE infrastructure. Mirrors
``forge-security/tests/_workload_fixtures.py``'s DER-cert helper but adds
real, loadable PEM material and matching private keys, which that module
doesn't need (it only tests SAN parsing on inert DER bytes).
"""

from __future__ import annotations

import ssl
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import NameOID

_KEY_SIZE = 2048
_VALIDITY = timedelta(hours=1)


@dataclass
class _CertKeyPair:
    cert_pem: bytes
    key_pem: bytes
    cert: x509.Certificate
    key: RSAPrivateKey


def _generate_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)


def make_ca(common_name: str = "forge-test-ca") -> _CertKeyPair:
    """Build a small self-signed CA usable as a trust anchor."""
    key = _generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + _VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return _CertKeyPair(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        cert=cert,
        key=key,
    )


def issue_leaf_cert(
    ca: _CertKeyPair,
    *,
    common_name: str,
    spiffe_id: str | None = None,
) -> _CertKeyPair:
    """Issue a leaf certificate signed by *ca*, with an optional
    ``spiffe://`` URI SAN."""
    key = _generate_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + _VALIDITY)
    )
    if spiffe_id:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id)]),
            critical=True,
        )
    cert = builder.sign(ca.key, hashes.SHA256())
    return _CertKeyPair(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        cert=cert,
        key=key,
    )


def _write_temp(*chunks: bytes, suffix: str = ".pem") -> str:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fd:
        for chunk in chunks:
            fd.write(chunk)
        return fd.name


def build_server_ssl_context(leaf: _CertKeyPair, ca: _CertKeyPair) -> ssl.SSLContext:
    """A ``CERT_REQUIRED`` server-side context trusting *ca*, presenting
    *leaf* -- mirrors what ``SPIFFEIdentityProvider.create_tls_context(server=True)``
    hands back in production."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = False
    cert_path = _write_temp(leaf.cert_pem)
    key_path = _write_temp(leaf.key_pem)
    ca_path = _write_temp(ca.cert_pem)
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    ctx.load_verify_locations(cafile=ca_path)
    return ctx


def build_client_ssl_context(
    ca: _CertKeyPair,
    leaf: _CertKeyPair | None = None,
) -> ssl.SSLContext:
    """A client-side context trusting *ca*; optionally presenting *leaf*
    as its own client certificate (mTLS). ``leaf=None`` builds a client
    that presents **no** certificate at all -- used to prove the server
    rejects an unauthenticated peer at the handshake."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ca_path = _write_temp(ca.cert_pem)
    ctx.load_verify_locations(cafile=ca_path)
    if leaf is not None:
        cert_path = _write_temp(leaf.cert_pem)
        key_path = _write_temp(leaf.key_pem)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


def cert_der(leaf: _CertKeyPair) -> bytes:
    """DER bytes for *leaf* -- what ``getpeercert(binary_form=True)``
    returns."""
    return leaf.cert.public_bytes(serialization.Encoding.DER)
