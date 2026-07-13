"""Shared RSA-keypair / synthetic-JWKS fixtures for OIDC tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


@dataclass
class RSAKeyPair:
    kid: str
    private_key: RSAPrivateKey = field(repr=False)

    @classmethod
    def generate(cls, kid: str) -> RSAKeyPair:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return cls(kid=kid, private_key=key)

    def public_jwk(self) -> dict[str, Any]:
        alg = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256)
        jwk = json.loads(alg.to_jwk(self.private_key.public_key()))
        jwk["kid"] = self.kid
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        return jwk

    def sign(self, claims: dict[str, Any], *, headers: dict[str, Any] | None = None) -> str:
        hdrs = {"kid": self.kid}
        if headers:
            hdrs.update(headers)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers=hdrs)


def jwks_document(*keypairs: RSAKeyPair) -> dict[str, Any]:
    """Build a ``{"keys": [...]}`` JWKS document for the given keypairs."""
    return {"keys": [kp.public_jwk() for kp in keypairs]}
