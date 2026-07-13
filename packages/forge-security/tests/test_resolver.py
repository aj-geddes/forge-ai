"""Tests for forge_security.oidc.resolver.resolve_principal (ADR-0001 SS5.1).

This is THE bypass-free credential resolver: cookie -> session,
Bearer forge_sk_... -> service token, Bearer <JWS> -> Dex RS256, anything
else -> 401. There is no fallthrough branch. In particular, this module
never reads an X-Caller-ID header or a caller_id query parameter -- those
inputs simply do not exist in its signature.
"""

from __future__ import annotations

import hashlib
import time

import httpx
import pytest
from _oidc_fixtures import RSAKeyPair, jwks_document
from cryptography.fernet import Fernet
from forge_config.schema import AuthorizationConfig, RoleBinding, ServiceToken
from forge_security.oidc.authorizer import Authorizer
from forge_security.oidc.errors import AuthError
from forge_security.oidc.jwks import JwksCache
from forge_security.oidc.resolver import resolve_principal
from forge_security.oidc.service_tokens import ServiceTokenVerifier
from forge_security.oidc.session import SessionCodec
from forge_security.oidc.verifier import OIDCTokenVerifier, OIDCVerifierConfig

ISSUER = "https://dex.hvslocal/dex"
AUDIENCE = "forge-ai"


def _make_oidc_verifier(*keypairs: RSAKeyPair) -> OIDCTokenVerifier:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_document(*keypairs))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    jwks_cache = JwksCache(http_client=client, jwks_uri="https://dex.hvslocal/dex/keys")
    config = OIDCVerifierConfig(issuer=ISSUER, audience=AUDIENCE, client_id=AUDIENCE)
    return OIDCTokenVerifier(jwks_cache=jwks_cache, config=config)


def _make_authorizer() -> Authorizer:
    return Authorizer(
        AuthorizationConfig(
            bindings=[
                RoleBinding(role="admin", emails=["ageddes75@gmail.com"]),
            ],
        )
    )


def _make_session_codec() -> SessionCodec:
    return SessionCodec(key=Fernet.generate_key())


def _make_service_token_verifier(plaintext: str, *, roles: list[str]) -> ServiceTokenVerifier:
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return ServiceTokenVerifier([ServiceToken(id="ci-deployer", secret_sha256=digest, roles=roles)])


async def _resolve(
    *,
    session_cookie: str | None = None,
    authorization_header: str | None = None,
    session_codec: SessionCodec | None = None,
    service_token_verifier: ServiceTokenVerifier | None = None,
    oidc_verifier: OIDCTokenVerifier | None = None,
    authorizer: Authorizer | None = None,
):
    return await resolve_principal(
        session_cookie=session_cookie,
        authorization_header=authorization_header,
        session_codec=session_codec or _make_session_codec(),
        service_token_verifier=service_token_verifier
        or _make_service_token_verifier("forge_sk_x_unused", roles=[]),
        oidc_verifier=oidc_verifier,
        authorizer=authorizer or _make_authorizer(),
    )


class TestSessionCookiePath:
    async def test_session_cookie_resolves_to_user_principal(self):
        codec = _make_session_codec()
        token, _sid = codec.encode(
            sub="sub-123",
            email="ageddes75@gmail.com",
            name="AJ Geddes",
            preferred_username="aj-geddes",
            groups=[],
            email_verified=True,
        )

        principal = await _resolve(session_cookie=token, session_codec=codec)

        assert principal.kind == "user"
        assert principal.sub == "sub-123"
        assert "admin" in principal.roles

    async def test_invalid_session_cookie_raises_401(self):
        codec = _make_session_codec()

        with pytest.raises(AuthError) as exc:
            await _resolve(session_cookie="garbage", session_codec=codec)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_session"

    async def test_session_cookie_takes_priority_over_authorization_header(self):
        """Per ADR-0001 SS5.1 step ordering: cookie is checked first."""
        codec = _make_session_codec()
        token, _sid = codec.encode(
            sub="sub-cookie",
            email="ageddes75@gmail.com",
            name=None,
            preferred_username=None,
            groups=[],
            email_verified=True,
        )

        principal = await _resolve(
            session_cookie=token,
            authorization_header="Bearer forge_sk_should-be-ignored",
            session_codec=codec,
        )

        assert principal.sub == "sub-cookie"


class TestSessionCookieEmailVerifiedGating:
    """Security-review finding #2 (HIGH): the session-cookie path
    re-derives roles from claims snapshotted in the cookie at login time
    (Authorizer.roles_for is claims-driven on every request, not a
    one-time decision) -- so the stored email_verified must gate the
    email-derived role here too, not just at /auth/callback."""

    async def test_session_cookie_with_email_verified_true_grants_email_role(self):
        codec = _make_session_codec()
        token, _sid = codec.encode(
            sub="sub-123",
            email="ageddes75@gmail.com",
            name=None,
            preferred_username=None,
            groups=[],
            email_verified=True,
        )

        principal = await _resolve(session_cookie=token, session_codec=codec)

        assert "admin" in principal.roles

    async def test_session_cookie_with_email_verified_false_does_not_grant_email_role(self):
        codec = _make_session_codec()
        token, _sid = codec.encode(
            sub="sub-123",
            email="ageddes75@gmail.com",
            name=None,
            preferred_username=None,
            groups=[],
            email_verified=False,
        )

        principal = await _resolve(session_cookie=token, session_codec=codec)

        assert principal.roles == []

    async def test_session_cookie_with_email_verified_omitted_does_not_grant_email_role(self):
        """encode()'s email_verified defaults to False -- an old-shaped or
        naively-minted cookie must not silently grant an email role."""
        codec = _make_session_codec()
        token, _sid = codec.encode(
            sub="sub-123",
            email="ageddes75@gmail.com",
            name=None,
            preferred_username=None,
            groups=[],
        )

        principal = await _resolve(session_cookie=token, session_codec=codec)

        assert principal.roles == []


class TestServiceTokenPath:
    async def test_bearer_forge_sk_resolves_to_service_principal(self):
        plaintext = "forge_sk_ci-deployer_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        verifier = _make_service_token_verifier(plaintext, roles=["admin"])

        principal = await _resolve(
            authorization_header=f"Bearer {plaintext}",
            service_token_verifier=verifier,
        )

        assert principal.kind == "service"
        assert principal.sub == "svc:ci-deployer"
        assert principal.token_id == "ci-deployer"
        assert "config:write" in principal.permissions  # admin -> wildcard

    async def test_wrong_service_token_raises_401(self):
        verifier = _make_service_token_verifier("forge_sk_x_real-secret-value", roles=["admin"])

        with pytest.raises(AuthError) as exc:
            await _resolve(
                authorization_header="Bearer forge_sk_x_wrong-secret-value",
                service_token_verifier=verifier,
            )

        assert exc.value.status == 401


class TestOIDCBearerPath:
    async def test_bearer_jws_resolves_to_user_principal(self):
        kp = RSAKeyPair.generate("kid-1")
        oidc_verifier = _make_oidc_verifier(kp)
        now = int(time.time())
        token = kp.sign(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "dex-sub-1",
                "email": "ageddes75@gmail.com",
                "email_verified": True,
                "exp": now + 3600,
                "iat": now,
            }
        )

        principal = await _resolve(
            authorization_header=f"Bearer {token}",
            oidc_verifier=oidc_verifier,
        )

        assert principal.kind == "user"
        assert principal.sub == "dex-sub-1"
        assert "admin" in principal.roles

    async def test_invalid_jws_bearer_raises_401(self):
        kp = RSAKeyPair.generate("kid-1")
        oidc_verifier = _make_oidc_verifier(kp)

        with pytest.raises(AuthError) as exc:
            await _resolve(
                authorization_header="Bearer not.a.valid-jws-signature",
                oidc_verifier=oidc_verifier,
            )

        assert exc.value.status == 401

    async def test_jws_shaped_bearer_without_verifier_configured_raises_401(self):
        with pytest.raises(AuthError) as exc:
            await _resolve(
                authorization_header="Bearer aaa.bbb.ccc",
                oidc_verifier=None,
            )

        assert exc.value.status == 401
        assert exc.value.code == "invalid_credential_format"


class TestOIDCBearerEmailVerifiedGating:
    """Security-review finding #2 (HIGH): an id_token/bearer JWS's
    email_verified claim gates whether its email claim may match an
    email-based role binding."""

    def _sign_claims(self, kp: RSAKeyPair, **overrides: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "dex-sub-1",
            "email": "ageddes75@gmail.com",
            "exp": now + 3600,
            "iat": now,
        }
        claims.update(overrides)
        return kp.sign(claims)

    async def test_email_verified_true_grants_email_role(self):
        kp = RSAKeyPair.generate("kid-1")
        oidc_verifier = _make_oidc_verifier(kp)
        token = self._sign_claims(kp, email_verified=True)

        principal = await _resolve(
            authorization_header=f"Bearer {token}", oidc_verifier=oidc_verifier
        )

        assert "admin" in principal.roles

    async def test_email_verified_false_does_not_grant_email_role(self):
        kp = RSAKeyPair.generate("kid-1")
        oidc_verifier = _make_oidc_verifier(kp)
        token = self._sign_claims(kp, email_verified=False)

        principal = await _resolve(
            authorization_header=f"Bearer {token}", oidc_verifier=oidc_verifier
        )

        assert principal.roles == []

    async def test_email_verified_absent_does_not_grant_email_role(self):
        kp = RSAKeyPair.generate("kid-1")
        oidc_verifier = _make_oidc_verifier(kp)
        token = self._sign_claims(kp)  # no email_verified claim at all

        principal = await _resolve(
            authorization_header=f"Bearer {token}", oidc_verifier=oidc_verifier
        )

        assert principal.roles == []

    async def test_groups_binding_unaffected_by_missing_email_verified(self):
        kp = RSAKeyPair.generate("kid-1")
        authorizer = Authorizer(
            AuthorizationConfig(
                bindings=[
                    RoleBinding(role="admin", emails=["ageddes75@gmail.com"]),
                    RoleBinding(role="user", groups=["hvs-platform:engineers"]),
                ],
            )
        )
        oidc_verifier = _make_oidc_verifier(kp)
        token = self._sign_claims(kp, groups=["hvs-platform:engineers"])  # no email_verified

        principal = await _resolve(
            authorization_header=f"Bearer {token}",
            oidc_verifier=oidc_verifier,
            authorizer=authorizer,
        )

        assert principal.roles == ["user"]


class TestNoFallthrough:
    async def test_missing_credentials_raises_401_with_www_authenticate(self):
        with pytest.raises(AuthError) as exc:
            await _resolve()

        assert exc.value.status == 401
        assert exc.value.code == "missing_credentials"
        assert exc.value.headers.get("WWW-Authenticate") == "Bearer"

    async def test_non_bearer_authorization_scheme_raises_401(self):
        with pytest.raises(AuthError) as exc:
            await _resolve(authorization_header="Basic dXNlcjpwYXNz")

        assert exc.value.status == 401
        assert exc.value.code == "invalid_credential_format"

    async def test_bearer_token_with_no_recognisable_shape_raises_401(self):
        with pytest.raises(AuthError) as exc:
            await _resolve(authorization_header="Bearer just-a-random-opaque-string")

        assert exc.value.status == 401
        assert exc.value.code == "invalid_credential_format"

    async def test_x_caller_id_style_header_is_not_a_parameter_of_resolve_principal(self):
        """Structural regression test for the ADR-0001 SS1.2/SS5.1 bypass:
        resolve_principal's signature has no way to accept X-Caller-ID or a
        caller_id query parameter as an identity input."""
        import inspect

        sig = inspect.signature(resolve_principal)
        assert "caller_id" not in sig.parameters
        assert "x_caller_id" not in sig.parameters
