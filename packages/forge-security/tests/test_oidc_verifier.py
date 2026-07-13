"""Tests for forge_security.oidc.verifier.OIDCTokenVerifier (ADR-0001 SS8.2).

These are the security-critical regression tests: alg-confusion, the
verify_aud=False regression, and "a credential that does not parse must
never become an identity".
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from _oidc_fixtures import RSAKeyPair, jwks_document
from forge_security.oidc.errors import AuthError
from forge_security.oidc.jwks import JwksCache
from forge_security.oidc.verifier import OIDCTokenVerifier, OIDCVerifierConfig

ISSUER = "https://dex.hvslocal/dex"
CLIENT_ID = "forge-ai"
AUDIENCE = "forge-ai"


def _make_verifier(
    *keypairs: RSAKeyPair,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    client_id: str = CLIENT_ID,
    allowed_algorithms: list[str] | None = None,
    clock_skew_seconds: int = 60,
) -> OIDCTokenVerifier:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_document(*keypairs))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    jwks_cache = JwksCache(http_client=client, jwks_uri="https://dex.hvslocal/dex/keys")
    config = OIDCVerifierConfig(
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        allowed_algorithms=allowed_algorithms or ["RS256"],
        clock_skew_seconds=clock_skew_seconds,
    )
    return OIDCTokenVerifier(jwks_cache=jwks_cache, config=config)


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "CgdhamdlZGRlcxIGZ2l0aHVi",
        "exp": now + 3600,
        "iat": now,
        "email": "ageddes75@gmail.com",
    }
    base.update(overrides)
    return base


class TestValidToken:
    async def test_valid_rs256_token_returns_claims(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = kp.sign(_claims())

        claims = await verifier.verify(token)

        assert claims["sub"] == "CgdhamdlZGRlcxIGZ2l0aHVi"
        assert claims["iss"] == ISSUER


class TestAlgConfusion:
    async def test_hs256_token_signed_with_jwks_modulus_is_rejected(self):
        """Alg-confusion attack: sign with the RSA public key's modulus used
        as an HMAC secret. Must be rejected by the alg allow-list BEFORE any
        key lookup -- allowed_algorithms=["RS256"] excludes HS256 outright."""
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        public_numbers = kp.private_key.public_key().public_numbers()
        fake_hmac_secret = str(public_numbers.n)
        forged = jwt.encode(
            _claims(), fake_hmac_secret, algorithm="HS256", headers={"kid": "kid-1"}
        )

        with pytest.raises(AuthError) as exc:
            await verifier.verify(forged)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"

    async def test_alg_none_token_is_rejected(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = jwt.encode(_claims(), key="", algorithm="none")

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"


class TestIssuerAndAudience:
    async def test_wrong_issuer_rejected_401_invalid_issuer(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = kp.sign(_claims(iss="https://not-dex.example.com/dex"))

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_issuer"

    async def test_wrong_audience_rejected_401_invalid_audience(self):
        """Regression test for the retired verify_aud=False bug."""
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = kp.sign(_claims(aud="some-other-service"))

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_audience"

    async def test_audience_of_another_dex_client_rejected(self):
        """A validly-signed token issued to a different Dex client (e.g.
        ArgoCD) must not be accepted for Forge."""
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp, audience="forge-ai")
        token = kp.sign(_claims(aud="argocd"))

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.code == "invalid_audience"


class TestExpiry:
    async def test_expired_token_rejected_401_token_expired(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp, clock_skew_seconds=0)
        token = kp.sign(_claims(exp=int(time.time()) - 3600))

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "token_expired"

    async def test_token_within_clock_skew_leeway_accepted(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp, clock_skew_seconds=60)
        token = kp.sign(_claims(exp=int(time.time()) - 10))  # 10s past exp, 60s leeway

        claims = await verifier.verify(token)

        assert claims["sub"]


class TestRequiredClaims:
    async def test_missing_kid_rejected(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = jwt.encode(_claims(), kp.private_key, algorithm="RS256")  # no kid header

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"

    async def test_missing_sub_rejected(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        claims = _claims()
        del claims["sub"]
        token = kp.sign(claims)

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"


class TestSignatureTampering:
    async def test_token_signed_by_unrelated_key_rejected(self):
        real_kp = RSAKeyPair.generate("kid-1")
        attacker_kp = RSAKeyPair.generate("kid-1")  # same kid, different key
        verifier = _make_verifier(real_kp)
        forged = attacker_kp.sign(_claims())

        with pytest.raises(AuthError) as exc:
            await verifier.verify(forged)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"


class TestGarbageInput:
    async def test_garbage_string_rejected_401_not_treated_as_identity(self):
        """The current-code bypass: a string that fails to parse must be a
        401, never an identity."""
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)

        with pytest.raises(AuthError) as exc:
            await verifier.verify("not-a-jwt-at-all")

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"


class TestAzpCheck:
    async def test_azp_mismatch_rejected_when_aud_is_a_list(self):
        """ADR-0001 SS8.2 step 6: when aud is a list, azp (if present) must
        equal our client_id."""
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp, audience="forge-ai", client_id="forge-ai")
        token = kp.sign(_claims(aud=["forge-ai", "other-service"], azp="other-service"))

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_audience"

    async def test_azp_matching_client_id_is_accepted(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp, audience="forge-ai", client_id="forge-ai")
        token = kp.sign(_claims(aud=["forge-ai"], azp="forge-ai"))

        claims = await verifier.verify(token)

        assert claims["azp"] == "forge-ai"


class TestNonce:
    async def test_nonce_mismatch_rejected_in_authcode_flow(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = kp.sign(_claims(nonce="expected-nonce"))

        with pytest.raises(AuthError) as exc:
            await verifier.verify(token, nonce="different-nonce")

        assert exc.value.status == 401
        assert exc.value.code == "invalid_nonce"

    async def test_nonce_match_in_authcode_flow_accepted(self):
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = kp.sign(_claims(nonce="expected-nonce"))

        claims = await verifier.verify(token, nonce="expected-nonce")

        assert claims["nonce"] == "expected-nonce"

    async def test_bearer_verification_does_not_require_nonce(self):
        """Bearer-token verification has no nonce to bind to (ADR-0001 SS8.2
        step 7 applies only to the auth-code flow)."""
        kp = RSAKeyPair.generate("kid-1")
        verifier = _make_verifier(kp)
        token = kp.sign(_claims())  # no nonce claim at all

        claims = await verifier.verify(token, nonce=None)

        assert claims["sub"]
