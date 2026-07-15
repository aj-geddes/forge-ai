"""Bypass-free authentication/authorization wiring for the gateway (ADR-0001).

The only way a request resolves to an identity is
``forge_security.oidc.resolve_principal``. There is no dev-mode
fallthrough, no ``X-Caller-ID`` header, no ``caller_id`` query parameter --
those inputs simply do not exist anywhere in this module.

``configure_auth`` is called once from the application lifespan (and again
on every config hot-reload) to wire the verifiers built from the current
config. ``get_principal`` is the FastAPI dependency every protected route
ultimately depends on; ``require_permission`` layers an authorization
check on top of it. ``enforce_mcp_security`` gives the ``/mcp`` raw ASGI
mount the same enforcement outside FastAPI's ``Depends`` machinery.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException, Request
from forge_config.schema import (
    AuthMode,
    AuthorizationConfig,
    OIDCConfig,
    Permission,
    SecurityConfig,
    UserTokenConfig,
)
from forge_security.oidc import (
    AuthError,
    Authorizer,
    DiscoveryDocument,
    OIDCTokenVerifier,
    Principal,
    ServiceTokenVerifier,
    SessionCodec,
)

from forge_gateway import rate_limit

if TYPE_CHECKING:
    from forge_security.oidc.user_tokens import UserTokenStore

logger = logging.getLogger("forge.gateway.security")

_DEV_INSECURE_ENV_VAR = "FORGE_DEV_INSECURE"
_DEV_INSECURE_SUB = "dev-anonymous"
_DEV_INSECURE_ROLES = ["admin"]


class _NullSessionCodec:
    """Stands in for ``SessionCodec`` when session cookies cannot function
    (OIDC disabled, or the session encryption key could not be resolved).

    Any presented cookie is treated as an invalid session -- never as a
    crash -- so bearer/service-token paths remain unaffected.
    """

    def decode(self, cookie_value: str) -> None:  # noqa: ARG002
        raise AuthError(401, "invalid_session")


_NULL_SESSION_CODEC = _NullSessionCodec()


@dataclass
class _AuthState:
    """Module-level auth wiring, replaced wholesale by :func:`configure_auth`."""

    session_codec: SessionCodec | None = None
    service_token_verifier: ServiceTokenVerifier | None = None
    oidc_verifier: OIDCTokenVerifier | None = None
    authorizer: Authorizer | None = None
    cookie_name: str = "forge_session"
    dev_insecure: bool = False
    healthy: bool = False
    metrics_public: bool = True
    http_client: httpx.AsyncClient | None = None
    oidc_config: OIDCConfig | None = None
    endpoints: DiscoveryDocument | None = None
    tx_key: bytes | None = None
    # ADR-0002 (user-issued API keys): the live user-token store instance
    # handed to ServiceTokenVerifier, the UserTokenConfig it was built
    # from (TTL bounds + the enabled switch), and the AuthorizationConfig
    # (role -> permission map) that routes/tokens.py needs to reject
    # unknown role names at mint time -- Authorizer itself exposes no
    # public accessor for its underlying config.
    user_token_store: UserTokenStore | None = None
    user_token_config: UserTokenConfig | None = None
    authorization_config: AuthorizationConfig | None = None


_state = _AuthState()


def is_dev_insecure_active(config: SecurityConfig) -> bool:
    """ADR-0001 SS7.2: ``dev_insecure`` requires *both* the config switch
    and the ``FORGE_DEV_INSECURE=1`` environment variable. Neither alone
    is sufficient."""
    return (
        config.auth.mode == AuthMode.DEV_INSECURE and os.environ.get(_DEV_INSECURE_ENV_VAR) == "1"
    )


def configure_auth(
    *,
    session_codec: SessionCodec | None,
    service_token_verifier: ServiceTokenVerifier | None,
    oidc_verifier: OIDCTokenVerifier | None,
    authorizer: Authorizer,
    cookie_name: str = "forge_session",
    dev_insecure: bool = False,
    metrics_public: bool = True,
    http_client: httpx.AsyncClient | None = None,
    oidc_config: OIDCConfig | None = None,
    endpoints: DiscoveryDocument | None = None,
    tx_key: bytes | None = None,
    rate_limit_rpm: int | None = None,
    user_token_store: UserTokenStore | None = None,
    user_token_config: UserTokenConfig | None = None,
    authorization_config: AuthorizationConfig | None = None,
) -> None:
    """Wire the auth subsystem from the application lifespan.

    ``healthy`` (ADR-0001 SS7.3's "total auth failure" row) is ``True``
    when *any* credential mechanism can actually operate: dev_insecure,
    a working OIDC verifier, at least one configured service token, or
    (ADR-0002 SS4.4) a live user-token store -- a machine-only deployment
    with only user-issued tokens and no static ones still reports healthy.
    When none of these hold, :func:`get_principal` denies every request
    with ``503 auth_unavailable`` rather than silently allowing traffic.

    ``rate_limit_rpm`` wires ``security.rate_limit_rpm`` (see
    :mod:`forge_gateway.rate_limit`) to the identity-keyed and IP-keyed
    limiters. Defaults to ``None`` (disabled) so call sites that don't pass
    it -- most tests, which wire auth via the repo-wide ``dev_insecure``
    fixture -- are never rate limited by surprise; real config-driven
    startup (``forge_gateway.app._init_auth``) always passes the configured
    value explicitly.

    ``user_token_store`` is the live ``FileUserTokenStore`` instance
    (ADR-0002) -- the *same* object passed to ``ServiceTokenVerifier``'s
    ``store=`` kwarg by the caller, so a mint/revoke through
    ``routes/tokens.py`` is visible to verification on the very next
    request. ``user_token_config``/``authorization_config`` are the raw
    config blocks ``routes/tokens.py`` needs (TTL bounds, the feature
    switch, and the known role set) that have no other accessor.
    """
    global _state
    has_service_tokens = bool(
        service_token_verifier is not None and service_token_verifier._tokens  # noqa: SLF001
    )
    has_user_token_store = user_token_store is not None
    healthy = (
        dev_insecure or oidc_verifier is not None or has_service_tokens or has_user_token_store
    )
    _state = _AuthState(
        session_codec=session_codec,
        service_token_verifier=service_token_verifier,
        oidc_verifier=oidc_verifier,
        authorizer=authorizer,
        cookie_name=cookie_name,
        dev_insecure=dev_insecure,
        healthy=healthy,
        metrics_public=metrics_public,
        http_client=http_client,
        oidc_config=oidc_config,
        endpoints=endpoints,
        tx_key=tx_key,
        user_token_store=user_token_store,
        user_token_config=user_token_config,
        authorization_config=authorization_config,
    )
    rate_limit.configure_rate_limiting(rate_limit_rpm)
    if dev_insecure:
        logger.critical(
            "FORGE RUNNING IN dev_insecure MODE -- every request is authenticated as "
            "'%s' with admin privileges. This must never happen in production.",
            _DEV_INSECURE_SUB,
        )


async def shutdown_auth() -> None:
    """Close any resources opened by :func:`configure_auth` (e.g. the JWKS
    HTTP client). Safe to call even when nothing was configured."""
    if _state.http_client is not None:
        await _state.http_client.aclose()


def is_auth_healthy() -> bool:
    """Whether the auth subsystem has at least one working credential
    mechanism (ADR-0001 SS7.3's "total auth failure" readiness gate)."""
    return _state.healthy


def is_dev_insecure() -> bool:
    return _state.dev_insecure


def get_oidc_config() -> OIDCConfig | None:
    return _state.oidc_config


def get_endpoints() -> DiscoveryDocument | None:
    return _state.endpoints


def get_http_client() -> httpx.AsyncClient | None:
    return _state.http_client


def get_tx_key() -> bytes | None:
    return _state.tx_key


def get_cookie_name() -> str:
    return _state.cookie_name


def get_oidc_verifier() -> OIDCTokenVerifier | None:
    return _state.oidc_verifier


def get_session_codec() -> SessionCodec | None:
    return _state.session_codec


def get_authorizer() -> Authorizer | None:
    return _state.authorizer


def get_user_token_store() -> UserTokenStore | None:
    """The live user-token store (ADR-0002), or ``None`` when
    ``security.service_tokens.user_tokens.enabled`` is false."""
    return _state.user_token_store


def get_user_token_config() -> UserTokenConfig | None:
    """The ``security.service_tokens.user_tokens`` config block (TTL
    bounds + the feature switch), or ``None`` before auth has been wired."""
    return _state.user_token_config


def get_authorization_config() -> AuthorizationConfig | None:
    """The raw ``security.authorization`` config block -- used by
    ``routes/tokens.py`` to validate requested role names against the
    known role set (``Authorizer`` exposes no public accessor for it)."""
    return _state.authorization_config


def reset_auth() -> None:
    """Reset all auth wiring to its unconfigured (unhealthy) state. Test-only."""
    global _state
    _state = _AuthState()
    rate_limit.reset_rate_limiting()


def _to_http_exception(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code, headers=exc.headers or None)


def _dev_principal() -> Principal:
    assert _state.authorizer is not None  # noqa: S101 -- invariant: always built alongside dev_insecure
    permissions = _state.authorizer.permissions_for_roles(_DEV_INSECURE_ROLES)
    return Principal(
        kind="dev",
        sub=_DEV_INSECURE_SUB,
        roles=list(_DEV_INSECURE_ROLES),
        permissions=permissions,
    )


def _rate_limit_identity(principal: Principal) -> str:
    """The key ``enforce_principal_rate_limit`` buckets on: a service
    token's ``token_id`` when present, otherwise the principal's ``sub``
    (which already carries a ``svc:``/OIDC-sub/``dev-anonymous`` identity of
    its own -- see ``forge_security.oidc.resolve_principal``)."""
    return principal.token_id or principal.sub


async def get_principal(request: Request) -> Principal:
    """Resolve *request* to a verified :class:`Principal`, or raise.

    The sole entry point for authentication: extracts the session cookie
    and ``Authorization`` header from the framework request and delegates
    to ``forge_security.oidc.resolve_principal``. Raises ``HTTPException``
    (never returns a fabricated identity) on any failure.

    Once a principal is resolved, enforces the identity-keyed rate limit
    (``security.rate_limit_rpm`` -- see :mod:`forge_gateway.rate_limit``)
    before returning it, raising ``429 rate_limited`` when exceeded. This
    is the single choke point for every protected route (``require_permission``,
    ``enforce_mcp_security``, ``/v1/auth/me``, and ``/metrics`` when
    ``metrics_public: false``), so wiring the limiter here covers the whole
    authenticated surface without touching each route individually.
    """
    if _state.dev_insecure:
        principal = _dev_principal()
    else:
        if not _state.healthy:
            raise HTTPException(status_code=503, detail="auth_unavailable")

        assert _state.authorizer is not None  # noqa: S101 -- invariant: set whenever healthy

        from forge_security.oidc import resolve_principal

        session_cookie = request.cookies.get(_state.cookie_name)
        authorization_header = request.headers.get("Authorization")

        try:
            principal = await resolve_principal(
                session_cookie=session_cookie,
                authorization_header=authorization_header,
                session_codec=_state.session_codec or _NULL_SESSION_CODEC,  # type: ignore[arg-type]
                service_token_verifier=_state.service_token_verifier or ServiceTokenVerifier([]),
                oidc_verifier=_state.oidc_verifier,
                authorizer=_state.authorizer,
            )
        except AuthError as exc:
            raise _to_http_exception(exc) from exc

    await rate_limit.enforce_principal_rate_limit(_rate_limit_identity(principal))
    return principal


def _check_permission(principal: Principal, permission: str) -> None:
    assert _state.authorizer is not None  # noqa: S101
    if not _state.authorizer.has(principal, permission):
        raise HTTPException(status_code=403, detail="forbidden")


def require_permission(permission: str) -> Callable[[Request], Awaitable[Principal]]:
    """Build a FastAPI dependency that requires *permission*.

    Resolves the caller via :func:`get_principal` (401/503 on failure)
    and then checks authorization (403 ``forbidden`` when the principal's
    role set does not grant *permission* -- authenticated is not the same
    as authorized, ADR-0001 SS6).
    """

    async def _dependency(request: Request) -> Principal:
        principal = await get_principal(request)
        _check_permission(principal, permission)
        return principal

    return _dependency


async def enforce_mcp_security(request: Request) -> Principal:
    """Run the same bypass-free resolver + ``tools:invoke`` check for a raw
    ASGI mount (the ``/mcp`` server) that bypasses FastAPI's ``Depends``
    machinery entirely."""
    principal = await get_principal(request)
    _check_permission(principal, Permission.TOOLS_INVOKE.value)
    return principal


class DevInsecureHeaderMiddleware:
    """Sets ``X-Forge-Insecure-Mode: true`` on every response while
    ``dev_insecure`` is active (ADR-0001 SS7.2) -- it must be impossible
    to be in this mode and not know it."""

    def __init__(self, app: object) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:  # type: ignore[type-arg]
        if scope["type"] != "http" or not _state.dev_insecure:
            await self._app(scope, receive, send)  # type: ignore[operator]
            return

        async def _send_with_header(message: dict) -> None:  # type: ignore[type-arg]
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-forge-insecure-mode", b"true"))
            await send(message)  # type: ignore[operator]

        await self._app(scope, receive, _send_with_header)  # type: ignore[operator]


async def require_metrics_access(request: Request) -> None:
    """ADR-0001 SS6.5: ``/metrics`` is unauthenticated on the pod port by
    default (``authorization.metrics_public``) so Prometheus can scrape it
    directly; when the operator sets ``metrics_public: false`` it falls
    back to the normal ``metrics:read`` permission check."""
    if _state.metrics_public:
        return
    principal = await get_principal(request)
    _check_permission(principal, Permission.METRICS_READ.value)


# --- Config-mutation anti-escalation guard (layered BASE+OVERLAY config) ---


class ConfigEscalationDeniedError(Exception):
    """Raised by :func:`check_no_config_escalation` when applying a
    proposed config change would grant the calling principal a
    permission it does not currently hold.

    Belt-and-suspenders atop the STRUCTURAL guarantee that the overlay
    can never carry a ``security``/``authorization`` key at all (see
    ``forge_config.overlay.OverlayDocument`` and
    ``forge_config.loader.load_effective_config``'s defense-in-depth
    filtering): for any legitimate overlay-only mutation, *after* is
    always identical to *before*, so this only ever fires if one of
    those two independent structural guards has regressed.
    """

    def __init__(self, before: frozenset[str], after: frozenset[str]) -> None:
        self.before = before
        self.after = after
        self.gained = after - before
        super().__init__(
            "config change would grant the caller permission(s) it does not "
            f"currently hold: {sorted(self.gained)}"
        )


def check_no_config_escalation(
    principal: Principal,
    *,
    config_after: object,
) -> None:
    """Recompute *principal*'s permissions under *config_after*'s
    ``security.authorization`` block (using the SAME ``principal.roles``
    already resolved before this request) and raise
    :class:`ConfigEscalationDeniedError` if that set is not a subset of
    the permissions the principal already holds
    (``principal.permissions``, resolved under the config in effect at
    authentication time) -- mirroring
    ``forge_security.oidc.user_tokens.mint_user_token``'s
    ``requested <= minter_permissions`` subset check.

    Call this BEFORE any overlay write in
    ``forge_gateway.routes.admin.apply_overlay_mutation``. ``config_after``
    is typed ``object`` here (rather than importing ``ForgeConfig``, which
    would create an import-time coupling this module doesn't otherwise
    need) -- callers pass a ``forge_config.schema.ForgeConfig``.
    """
    authorizer_after = Authorizer(config_after.security.authorization)  # type: ignore[attr-defined]
    after_permissions = authorizer_after.permissions_for_roles(principal.roles)
    if not after_permissions <= principal.permissions:
        raise ConfigEscalationDeniedError(principal.permissions, after_permissions)
