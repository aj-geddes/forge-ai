"""Shared wiring helpers for the ADR-0002 user-token endpoint tests
(``test_token_endpoints.py``, ``test_token_end_to_end.py``). Mirrors the
``_oidc_fixtures.py`` / inline-helper pattern already used by
``test_auth_routes.py`` in this package.

Builds a real, unmocked auth stack -- a real ``SessionCodec`` (session
cookies for "user" principals), a real ``Authorizer``/``AuthorizationConfig``
(role -> permission expansion, including the admin wildcard), and a real
``FileUserTokenStore`` on a pytest ``tmp_path`` -- so these tests exercise
the actual enforcement code paths rather than a mocked stand-in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import FastAPI
from forge_config.schema import AuthorizationConfig, RoleBinding, ServiceToken, UserTokenConfig
from forge_gateway import security
from forge_gateway.routes import tokens as tokens_routes
from forge_security.oidc import Authorizer, ServiceTokenVerifier, SessionCodec
from forge_security.oidc.service_tokens import SERVICE_TOKEN_PREFIX
from forge_security.oidc.user_tokens import FileUserTokenStore

SESSION_COOKIE_NAME = "forge_session"

USER_SUB = "dex-sub-alice"
USER_EMAIL = "alice@example.com"
ADMIN_SUB = "dex-sub-bob"
ADMIN_EMAIL = "bob@example.com"
UNBOUND_SUB = "dex-sub-charlie"  # matches no binding -> zero roles
UNBOUND_EMAIL = "charlie@example.com"

DEFAULT_ROLES: dict[str, list[str]] = {
    "viewer": ["config:read", "metrics:read"],
    "user": ["config:read", "metrics:read", "agent:invoke", "tools:invoke"],
    "admin": ["*"],
}

# A static service token, wired alongside the dynamic store, for the
# "a service-token principal cannot mint" test (kind="service").
STATIC_SERVICE_TOKEN_RAW = f"{SERVICE_TOKEN_PREFIX}svc1_" + "x" * 43  # noqa: S105
STATIC_SERVICE_TOKEN_DIGEST = hashlib.sha256(STATIC_SERVICE_TOKEN_RAW.encode("utf-8")).hexdigest()


def make_app() -> FastAPI:
    """A bare FastAPI app carrying only the tokens router -- no
    CSRFMiddleware, so these are pure route-logic tests (CSRF enforcement
    is generic middleware, exercised separately)."""
    app = FastAPI()
    app.include_router(tokens_routes.router)
    return app


@dataclass
class TokenTestWiring:
    store: FileUserTokenStore
    session_codec: SessionCodec
    user_cookie: str
    admin_cookie: str
    unbound_cookie: str
    authorization_config: AuthorizationConfig


async def wire(
    tmp_path: Path,
    *,
    user_tokens_enabled: bool = True,
    default_ttl_seconds: int = 2_592_000,
    max_ttl_seconds: int = 7_776_000,
    max_tokens_per_owner: int | None = None,
    roles: dict[str, list[str]] | None = None,
    static_service_token: bool = False,
    store_path: Path | None = None,
) -> TokenTestWiring:
    """Wire ``forge_gateway.security`` with a real store + authorizer and
    return session cookies for a "user"-role principal, an "admin"-role
    principal, and an authenticated-but-unbound (zero-role) principal."""
    resolved_store_path = store_path or (tmp_path / "user_tokens.json")
    store = FileUserTokenStore(resolved_store_path)
    await store.load()

    authorization_config = AuthorizationConfig(
        roles=roles or dict(DEFAULT_ROLES),
        bindings=[
            RoleBinding(role="user", subs=[USER_SUB]),
            RoleBinding(role="admin", subs=[ADMIN_SUB]),
        ],
    )
    authorizer = Authorizer(authorization_config)

    session_key = Fernet.generate_key()
    session_codec = SessionCodec(key=session_key)

    user_cookie, _ = session_codec.encode(
        sub=USER_SUB,
        email=USER_EMAIL,
        name="Alice",
        preferred_username="alice",
        groups=[],
        email_verified=True,
    )
    admin_cookie, _ = session_codec.encode(
        sub=ADMIN_SUB,
        email=ADMIN_EMAIL,
        name="Bob",
        preferred_username="bob",
        groups=[],
        email_verified=True,
    )
    unbound_cookie, _ = session_codec.encode(
        sub=UNBOUND_SUB,
        email=UNBOUND_EMAIL,
        name="Charlie",
        preferred_username="charlie",
        groups=[],
        email_verified=True,
    )

    static_tokens = []
    if static_service_token:
        static_tokens.append(
            ServiceToken(id="svc1", secret_sha256=STATIC_SERVICE_TOKEN_DIGEST, roles=["admin"])
        )

    # max_tokens_per_owner is only passed through when the caller explicitly
    # asked for it, so tests that omit it exercise UserTokenConfig's own
    # default rather than a value pinned here.
    user_token_config = (
        UserTokenConfig(
            enabled=user_tokens_enabled,
            store_path=str(resolved_store_path),
            default_ttl_seconds=default_ttl_seconds,
            max_ttl_seconds=max_ttl_seconds,
            max_tokens_per_owner=max_tokens_per_owner,
        )
        if max_tokens_per_owner is not None
        else UserTokenConfig(
            enabled=user_tokens_enabled,
            store_path=str(resolved_store_path),
            default_ttl_seconds=default_ttl_seconds,
            max_ttl_seconds=max_ttl_seconds,
        )
    )

    security.configure_auth(
        session_codec=session_codec,
        service_token_verifier=ServiceTokenVerifier(static_tokens, store=store),
        oidc_verifier=None,
        authorizer=authorizer,
        cookie_name=SESSION_COOKIE_NAME,
        dev_insecure=False,
        user_token_store=store,
        user_token_config=user_token_config,
        authorization_config=authorization_config,
    )
    return TokenTestWiring(
        store=store,
        session_codec=session_codec,
        user_cookie=user_cookie,
        admin_cookie=admin_cookie,
        unbound_cookie=unbound_cookie,
        authorization_config=authorization_config,
    )
