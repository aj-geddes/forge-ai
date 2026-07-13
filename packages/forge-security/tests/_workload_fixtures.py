"""Shared cert-building helpers for forge_security.workload tests.

Generates small self-signed X.509 certificates with (or without) a
``spiffe://`` URI SAN so tests can exercise SAN extraction without a real
SPIRE server, mirroring the DER bytes a TLS peer certificate would carry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def make_cert_der(*, spiffe_id: str | None, dns_name: str | None = None) -> bytes:
    """Build a self-signed cert (DER) with an optional spiffe:// URI SAN.

    ``spiffe_id=None`` produces a certificate with no SPIFFE SAN at all
    (only ``dns_name``, if given) -- used to prove unknown/foreign
    identities are rejected rather than silently accepted.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "workload-test")])
    now = datetime.now(UTC)

    names: list[x509.GeneralName] = []
    if spiffe_id:
        names.append(x509.UniformResourceIdentifier(spiffe_id))
    if dns_name:
        names.append(x509.DNSName(dns_name))

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(hours=1))
    )
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=True)

    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)
