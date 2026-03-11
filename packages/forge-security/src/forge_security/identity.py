"""Forge identity management wrapping AgentWeave identity primitives.

Provides keypair generation via the cryptography library and registration
with AgentWeave identity providers.  A *test mode* is available that uses
an in-memory mock identity so real SPIFFE infrastructure is not required.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

# ---------------------------------------------------------------------------
# Protocols – keep coupling loose so tests can inject mocks easily
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentityProviderProtocol(Protocol):
    """Minimal subset of the AgentWeave IdentityProvider interface."""

    async def get_identity(self) -> str: ...

    async def get_svid(self) -> Any: ...

    async def get_trust_bundle(self, trust_domain: str | None = None) -> Any: ...

    async def create_tls_context(self, server: bool = False) -> ssl.SSLContext: ...


# ---------------------------------------------------------------------------
# Keypair helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForgeKeypair:
    """ED25519 keypair used for message signing."""

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey

    @classmethod
    def generate(cls) -> ForgeKeypair:
        private = Ed25519PrivateKey.generate()
        return cls(private_key=private, public_key=private.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------------------
# Mock identity provider for test mode
# ---------------------------------------------------------------------------


class MockIdentityProvider:
    """In-memory mock identity provider (no SPIFFE required)."""

    def __init__(self, spiffe_id: str = "spiffe://forge.test/agent/mock") -> None:
        self._spiffe_id = spiffe_id

    async def get_identity(self) -> str:
        return self._spiffe_id

    async def get_svid(self) -> str:
        return f"mock-svid-for-{self._spiffe_id}"

    async def get_trust_bundle(self, trust_domain: str | None = None) -> str:
        return "mock-trust-bundle"

    async def create_tls_context(self, server: bool = False) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT if not server else ssl.PROTOCOL_TLS_SERVER)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


# ---------------------------------------------------------------------------
# ForgeIdentityManager – main entry point
# ---------------------------------------------------------------------------


@dataclass
class ForgeIdentityManager:
    """Manages Forge agent identity by wrapping an AgentWeave identity provider.

    Parameters
    ----------
    provider:
        An object satisfying ``IdentityProviderProtocol``.
        Pass ``None`` or omit to enable *test mode* which uses a mock provider.
    trust_domain:
        Trust domain string (e.g. ``"forge.local"``).
    agent_name:
        Logical agent name used in SPIFFE path construction.
    """

    trust_domain: str = "forge.local"
    agent_name: str = "forge-agent"
    provider: Any = field(default=None)  # IdentityProviderProtocol | None
    _keypair: ForgeKeypair | None = field(default=None, init=False, repr=False)
    _test_mode: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.provider is None:
            self._test_mode = True
            self.provider = MockIdentityProvider(
                spiffe_id=f"spiffe://{self.trust_domain}/agent/{self.agent_name}"
            )

    # -- identity operations ------------------------------------------------

    async def get_identity(self) -> str:
        """Return the SPIFFE ID for this agent."""
        result: str = await self.provider.get_identity()
        return result

    async def get_svid(self) -> Any:
        """Return the SVID from the underlying provider."""
        return await self.provider.get_svid()

    # -- keypair management -------------------------------------------------

    def get_or_create_keypair(self) -> ForgeKeypair:
        """Return (and lazily generate) the ED25519 keypair."""
        if self._keypair is None:
            self._keypair = ForgeKeypair.generate()
        return self._keypair

    def get_public_key(self) -> Ed25519PublicKey:
        """Convenience accessor for the public key."""
        return self.get_or_create_keypair().public_key

    # -- helpers ------------------------------------------------------------

    @property
    def is_test_mode(self) -> bool:
        return self._test_mode
