"""Message signing and verification using ED25519.

All tool-call messages in Forge can be signed to provide integrity and
non-repudiation guarantees.  ``MessageSigner`` wraps the low-level
cryptography calls behind a clean async interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@dataclass(frozen=True)
class SignedMessage:
    """A message together with its ED25519 signature."""

    payload: bytes
    signature: bytes


class MessageSigner:
    """Sign and verify tool-call messages using ED25519 keys.

    Parameters
    ----------
    private_key:
        The signing key.  Required for ``sign``; may be ``None`` if the
        instance is only used for verification.
    public_key:
        The verification key.  Required for ``verify``.
    """

    def __init__(
        self,
        private_key: Ed25519PrivateKey | None = None,
        public_key: Ed25519PublicKey | None = None,
    ) -> None:
        self._private_key = private_key
        self._public_key = public_key

    # -- signing ------------------------------------------------------------

    async def sign(self, payload: bytes) -> SignedMessage:
        """Sign *payload* and return a ``SignedMessage``.

        Raises
        ------
        ValueError
            If no private key was provided at construction time.
        """
        if self._private_key is None:
            raise ValueError("Cannot sign without a private key")
        signature = self._private_key.sign(payload)
        return SignedMessage(payload=payload, signature=signature)

    # -- verification -------------------------------------------------------

    async def verify(self, message: SignedMessage) -> bool:
        """Return ``True`` if *message.signature* is valid for *message.payload*.

        Raises
        ------
        ValueError
            If no public key was provided at construction time.
        """
        if self._public_key is None:
            raise ValueError("Cannot verify without a public key")
        try:
            self._public_key.verify(message.signature, message.payload)
            return True
        except InvalidSignature:
            return False
