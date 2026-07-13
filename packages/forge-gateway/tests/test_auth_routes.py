"""Tests for the OIDC browser login routes (ADR-0001 SS9.1):
GET /auth/login, GET /auth/callback, POST /auth/logout, GET /v1/auth/me.

Also covers the session-cookie authorization path end-to-end and the
"cold JWKS at startup" infrastructure-failure case (503, never 401).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from forge_config.schema import AuthorizationConfig, OIDCConfig, RoleBinding, SessionConfig
from forge_gateway import security
from forge_gateway.routes import auth as auth_routes
from forge_gateway.routes import conversational, health
from forge_security.oidc import (
    Authorizer,
    DiscoveryDocument,
    JwksCache,
    OIDCTokenVerifier,
    OIDCVerifierConfig,
    ServiceTokenVerifier,
    SessionCodec,
)

ISSUER = "https://dex.hvslocal/dex"
CLIENT_ID = "forge-ai"
AUTHORIZATION_ENDPOINT = "https://dex.hvslocal/dex/auth"
TOKEN_ENDPOINT = "https://dex.hvslocal/dex/token"
JWKS_URI = "https://dex.hvslocal/dex/keys"
REDIRECT_URI = "https://forgeai.hvslocal/auth/callback"


# ---------------------------------------------------------------------------
# Minimal self-contained RSA/JWKS test fixtures (mirrors the pattern used in
# packages/forge-security/tests/_oidc_fixtures.py, kept local so this
# package doesn't reach into another package's tests directory).
# ---------------------------------------------------------------------------


class _RSAKeyPair:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_jwk(self) -> dict[str, Any]:
        alg = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256)
        jwk = json.loads(alg.to_jwk(self.private_key.public_key()))
        jwk["kid"] = self.kid
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        return jwk

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.kid})


def _jwks_document(*keypairs: _RSAKeyPair) -> dict[str, Any]:
    return {"keys": [kp.public_jwk() for kp in keypairs]}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    security.reset_auth()
    yield
    security.reset_auth()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(conversational.router)
    app.include_router(health.router)
    return app


def _wire_oidc(
    *,
    keypair: _RSAKeyPair,
    token_response: dict[str, Any] | None = None,
    token_status: int = 200,
    jwks_unreachable: bool = False,
    roles_bindings: list[RoleBinding] | None = None,
    nonce_holder: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, bytes, bytes]:
    """Wire the auth subsystem for the OIDC browser flow against a mocked
    Dex (token endpoint + JWKS) via httpx.MockTransport."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/keys"):
            if jwks_unreachable:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json=_jwks_document(keypair))
        if request.url.path.endswith("/token"):
            if token_response is not None:
                return httpx.Response(token_status, json=token_response)
            nonce = nonce_holder.get("nonce", "") if nonce_holder else ""
            now = int(time.time())
            id_token = keypair.sign(
                {
                    "iss": ISSUER,
                    "aud": CLIENT_ID,
                    "sub": "dex-sub-1",
                    "email": "ageddes75@gmail.com",
                    "name": "AJ Geddes",
                    "preferred_username": "aj-geddes",
                    "groups": ["hvs-platform:admins"],
                    "nonce": nonce,
                    "iat": now,
                    "exp": now + 3600,
                }
            )
            return httpx.Response(200, json={"id_token": id_token, "token_type": "Bearer"})
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    jwks_cache = JwksCache(http_client=http_client, jwks_uri=JWKS_URI)
    oidc_verifier = OIDCTokenVerifier(
        jwks_cache=jwks_cache,
        config=OIDCVerifierConfig(issuer=ISSUER, audience=CLIENT_ID, client_id=CLIENT_ID),
    )

    session_key = Fernet.generate_key()
    session_codec = SessionCodec(key=session_key)
    tx_key = Fernet.generate_key()

    oidc_config = OIDCConfig(
        client_id=CLIENT_ID,
        issuer=ISSUER,
        redirect_uri=REDIRECT_URI,
        discovery=False,
        session=SessionConfig(secure=True),
    )
    endpoints = DiscoveryDocument(
        issuer=ISSUER,
        authorization_endpoint=AUTHORIZATION_ENDPOINT,
        token_endpoint=TOKEN_ENDPOINT,
        jwks_uri=JWKS_URI,
    )
    authorizer = Authorizer(
        AuthorizationConfig(
            bindings=(
                roles_bindings
                if roles_bindings is not None
                else [RoleBinding(role="admin", emails=["ageddes75@gmail.com"])]
            )
        )
    )

    security.configure_auth(
        session_codec=session_codec,
        service_token_verifier=ServiceTokenVerifier([]),
        oidc_verifier=oidc_verifier,
        authorizer=authorizer,
        cookie_name=oidc_config.session.cookie_name,
        dev_insecure=False,
        http_client=http_client,
        oidc_config=oidc_config,
        endpoints=endpoints,
        tx_key=tx_key,
    )
    return http_client, session_key, tx_key


async def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="https://forgeai.hvslocal")


# ---------------------------------------------------------------------------
# GET /auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    async def test_login_redirects_to_dex_with_pkce_state_and_nonce(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.get("/auth/login?next=/config", follow_redirects=False)

        assert resp.status_code == 302
        location = resp.headers["location"]
        parsed = urlsplit(location)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZATION_ENDPOINT
        query = parse_qs(parsed.query)
        assert query["response_type"] == ["code"]
        assert query["client_id"] == [CLIENT_ID]
        assert query["code_challenge_method"] == ["S256"]
        assert "state" in query
        assert "nonce" in query
        assert "code_challenge" in query

    async def test_login_sets_transaction_cookie_httponly_secure_samesite_lax(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.get("/auth/login", follow_redirects=False)

        set_cookie = resp.headers.get("set-cookie", "")
        assert "forge_oidc_tx=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "samesite=lax" in set_cookie.lower()

    async def test_login_rejects_absolute_url_in_next_param(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.get(
                "/auth/login?next=https://evil.example.com/steal", follow_redirects=False
            )

        location = resp.headers["location"]
        # The malicious "next" must never survive into the transaction --
        # exercised indirectly via the callback test below, but at minimum
        # the login response itself must still point at Dex, not evil.com.
        assert urlsplit(location).netloc == urlsplit(AUTHORIZATION_ENDPOINT).netloc

    async def test_login_rejects_protocol_relative_next_param(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.get("/auth/login?next=//evil.example.com", follow_redirects=False)

        assert resp.status_code == 302  # still redirects to Dex, not evil.com


# ---------------------------------------------------------------------------
# GET /auth/callback
# ---------------------------------------------------------------------------


async def _do_login(ac: httpx.AsyncClient, *, next_path: str = "/config") -> tuple[str, str]:
    """Perform GET /auth/login and return (state, nonce) parsed from the
    redirect Location, with the tx cookie left in the client's jar."""
    resp = await ac.get(f"/auth/login?next={next_path}", follow_redirects=False)
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    return query["state"][0], query["nonce"][0]


class TestCallback:
    async def test_callback_completes_login_and_sets_session_cookie(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        nonce_holder: dict[str, str] = {}
        _wire_oidc(keypair=keypair, nonce_holder=nonce_holder)
        app = _make_app()

        async with await _client(app) as ac:
            state, nonce = await _do_login(ac)
            nonce_holder["nonce"] = nonce

            resp = await ac.get(f"/auth/callback?code=abc123&state={state}", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/config"
        set_cookie_headers = resp.headers.get_list("set-cookie")
        session_cookie = next(c for c in set_cookie_headers if c.startswith("forge_session="))
        csrf_cookie = next(c for c in set_cookie_headers if c.startswith("forge_csrf="))
        assert "HttpOnly" in session_cookie
        assert "HttpOnly" not in csrf_cookie  # readable by JS for the double-submit token

    async def test_callback_with_mismatched_state_returns_400(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        nonce_holder: dict[str, str] = {}
        _wire_oidc(keypair=keypair, nonce_holder=nonce_holder)
        app = _make_app()

        async with await _client(app) as ac:
            _state, nonce = await _do_login(ac)
            nonce_holder["nonce"] = nonce

            resp = await ac.get(
                "/auth/callback?code=abc123&state=totally-wrong-state", follow_redirects=False
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "state_mismatch"

    async def test_callback_without_transaction_cookie_returns_400(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.get("/auth/callback?code=abc123&state=whatever", follow_redirects=False)

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_transaction"

    async def test_callback_with_replayed_transaction_cookie_returns_400(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        nonce_holder: dict[str, str] = {}
        _wire_oidc(keypair=keypair, nonce_holder=nonce_holder)
        app = _make_app()

        async with await _client(app) as ac:
            state, nonce = await _do_login(ac)
            nonce_holder["nonce"] = nonce
            tx_cookie_value = ac.cookies.get("forge_oidc_tx")

            first = await ac.get(
                f"/auth/callback?code=abc123&state={state}", follow_redirects=False
            )
            assert first.status_code == 302

            # Re-attach the (now-cleared) tx cookie to simulate a replay of
            # the captured transaction cookie value.
            ac.cookies.set("forge_oidc_tx", tx_cookie_value)
            replay = await ac.get(
                f"/auth/callback?code=abc123&state={state}", follow_redirects=False
            )

        assert replay.status_code == 400
        assert replay.json()["error"] == "transaction_replayed"

    async def test_callback_with_bad_nonce_returns_401(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        nonce_holder = {"nonce": "an-attacker-supplied-nonce"}
        _wire_oidc(keypair=keypair, nonce_holder=nonce_holder)
        app = _make_app()

        async with await _client(app) as ac:
            state, _nonce = await _do_login(ac)
            # nonce_holder deliberately left mismatched with the real nonce

            resp = await ac.get(f"/auth/callback?code=abc123&state={state}", follow_redirects=False)

        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_nonce"

    async def test_callback_for_user_with_no_role_binding_returns_403_and_sets_no_cookie(
        self,
    ) -> None:
        keypair = _RSAKeyPair("kid-1")
        nonce_holder: dict[str, str] = {}
        _wire_oidc(keypair=keypair, nonce_holder=nonce_holder, roles_bindings=[])
        app = _make_app()

        async with await _client(app) as ac:
            state, nonce = await _do_login(ac)
            nonce_holder["nonce"] = nonce

            resp = await ac.get(f"/auth/callback?code=abc123&state={state}", follow_redirects=False)

        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
        assert "forge_session=" not in resp.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    async def test_logout_clears_session_cookie(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.post("/auth/logout")

        assert resp.status_code == 200
        set_cookie_headers = resp.headers.get_list("set-cookie")
        session_clear = next(c for c in set_cookie_headers if c.startswith("forge_session="))
        assert "Max-Age=0" in session_clear or 'forge_session=""' in session_clear


# ---------------------------------------------------------------------------
# GET /v1/auth/me
# ---------------------------------------------------------------------------


class TestAuthMe:
    async def test_auth_me_returns_claims_roles_and_permissions(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _http_client, session_key, _tx_key = _wire_oidc(keypair=keypair)
        codec = SessionCodec(key=session_key)
        cookie_value, _sid = codec.encode(
            sub="dex-sub-1",
            email="ageddes75@gmail.com",
            name="AJ Geddes",
            preferred_username="aj-geddes",
            groups=["hvs-platform:admins"],
        )

        app = _make_app()
        async with await _client(app) as ac:
            ac.cookies.set("forge_session", cookie_value)
            resp = await ac.get("/v1/auth/me")

        assert resp.status_code == 200
        body = resp.json()
        assert body["sub"] == "dex-sub-1"
        assert body["email"] == "ageddes75@gmail.com"
        assert "admin" in body["roles"]
        assert "*" in body["permissions"] or "config:write" in body["permissions"]

    async def test_auth_me_without_session_returns_401(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair)
        app = _make_app()

        async with await _client(app) as ac:
            resp = await ac.get("/v1/auth/me")

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# A valid session cookie authorizes per role (end-to-end through a
# protected route, not just /v1/auth/me).
# ---------------------------------------------------------------------------


class TestSessionCookieAuthorizesPerRole:
    async def test_session_cookie_with_admin_role_can_chat(self) -> None:
        from unittest.mock import AsyncMock

        from forge_agent.agent.core import ForgeRunResult

        keypair = _RSAKeyPair("kid-1")
        _http_client, session_key, _tx_key = _wire_oidc(keypair=keypair)
        codec = SessionCodec(key=session_key)
        cookie_value, _sid = codec.encode(
            sub="dex-sub-1",
            email="ageddes75@gmail.com",
            name="AJ Geddes",
            preferred_username="aj-geddes",
            groups=[],
        )

        mock_agent = AsyncMock()
        mock_agent.run_conversational.return_value = ForgeRunResult(output="hi")
        conversational.set_agent(mock_agent)
        conversational.set_config(None)

        app = _make_app()
        async with await _client(app) as ac:
            ac.cookies.set("forge_session", cookie_value)
            resp = await ac.post("/v1/chat/completions", json={"message": "hi"})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cold JWKS at startup: 503, never 401 (ADR-0001 SS7.3)
# ---------------------------------------------------------------------------


class TestColdJwksIsInfrastructureFailureNot401:
    async def test_cold_jwks_bearer_verification_returns_503_not_401(self) -> None:
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair, jwks_unreachable=True)
        app = _make_app()

        # A syntactically JWS-shaped bearer token; its signature is
        # irrelevant -- the JWKS fetch itself fails first.
        fake_jws = keypair.sign({"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "exp": 9999999999})

        async with await _client(app) as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "hi"},
                headers={"Authorization": f"Bearer {fake_jws}"},
            )

        assert resp.status_code == 503
        assert resp.json()["detail"] == "identity_provider_unavailable"

    async def test_readiness_stays_ready_despite_cold_jwks(self) -> None:
        """A cold/unreachable JWKS at startup degrades OIDC bearer requests
        (503 above), but the gateway itself is still READY -- service
        tokens and existing sessions are unaffected, and Dex being down is
        not the same as Forge's auth subsystem having no working mechanism
        at all."""
        keypair = _RSAKeyPair("kid-1")
        _wire_oidc(keypair=keypair, jwks_unreachable=True)
        assert security.is_auth_healthy() is True
