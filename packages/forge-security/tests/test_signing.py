"""Tests for forge_security.signing."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from forge_security.signing import MessageSigner, SignedMessage


@pytest.fixture
def keypair():
    private = Ed25519PrivateKey.generate()
    return private, private.public_key()


@pytest.fixture
def signer(keypair):
    priv, pub = keypair
    return MessageSigner(private_key=priv, public_key=pub)


class TestMessageSigner:
    async def test_sign_returns_signed_message(self, signer):
        msg = await signer.sign(b"hello world")
        assert isinstance(msg, SignedMessage)
        assert msg.payload == b"hello world"
        assert len(msg.signature) == 64  # ED25519 signature length

    async def test_verify_valid_signature(self, signer):
        msg = await signer.sign(b"hello world")
        assert await signer.verify(msg) is True

    async def test_verify_tampered_payload(self, signer):
        msg = await signer.sign(b"hello world")
        tampered = SignedMessage(payload=b"TAMPERED", signature=msg.signature)
        assert await signer.verify(tampered) is False

    async def test_verify_tampered_signature(self, signer):
        msg = await signer.sign(b"hello world")
        bad_sig = bytes(64)  # all zeros
        tampered = SignedMessage(payload=msg.payload, signature=bad_sig)
        assert await signer.verify(tampered) is False

    async def test_sign_without_private_key_raises(self):
        pub = Ed25519PrivateKey.generate().public_key()
        s = MessageSigner(public_key=pub)
        with pytest.raises(ValueError, match="private key"):
            await s.sign(b"data")

    async def test_verify_without_public_key_raises(self):
        priv = Ed25519PrivateKey.generate()
        s = MessageSigner(private_key=priv)
        msg = await s.sign(b"data")
        with pytest.raises(ValueError, match="public key"):
            await s.verify(msg)

    async def test_cross_key_verification_fails(self):
        priv1 = Ed25519PrivateKey.generate()
        priv2 = Ed25519PrivateKey.generate()
        signer1 = MessageSigner(private_key=priv1, public_key=priv1.public_key())
        verifier2 = MessageSigner(public_key=priv2.public_key())
        msg = await signer1.sign(b"secret")
        assert await verifier2.verify(msg) is False
