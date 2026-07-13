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

import httpx
from fastapi import HTTPException, Request
from forge_config.schema import AuthMode, OIDCConfig, Permission, SecurityConfig
from forge_security.oidc import (
    AuthError,
    Authorizer,
    DiscoveryDocument,
    OIDCTokenVerifier,
    Principal,
    ServiceTokenVerifier,
    SessionCodec,
)

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
) -> None:
    """Wire the auth subsystem from the application lifespan.

    ``healthy`` (ADR-0001 SS7.3's "total auth failure" row) is ``True``
    when *any* credential mechanism can actually operate: dev_insecure,
    a working OIDC verifier, or at least one configured service token.
    When none of these hold, :func:`get_principal` denies every request
    with ``503 auth_unavailable`` rather than silently allowing traffic.
    """
    global _state
    has_service_tokens = bool(
        service_token_verifier is not None and service_token_verifier._tokens  # noqa: SLF001
    )
    healthy = dev_insecure or oidc_verifier is not None or has_service_tokens
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
    )
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


def reset_auth() -> None:
    """Reset all auth wiring to its unconfigured (unhealthy) state. Test-only."""
    global _state
    _state = _AuthState()


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


async def get_principal(request: Request) -> Principal:
    """Resolve *request* to a verified :class:`Principal`, or raise.

    The sole entry point for authentication: extracts the session cookie
    and ``Authorization`` header from the framework request and delegates
    to ``forge_security.oidc.resolve_principal``. Raises ``HTTPException``
    (never returns a fabricated identity) on any failure.
    """
    if _state.dev_insecure:
        return _dev_principal()

    if not _state.healthy:
        raise HTTPException(status_code=503, detail="auth_unavailable")

    assert _state.authorizer is not None  # noqa: S101 -- invariant: set whenever healthy

    from forge_security.oidc import resolve_principal

    session_cookie = request.cookies.get(_state.cookie_name)
    authorization_header = request.headers.get("Authorization")

    try:
        return await resolve_principal(
            session_cookie=session_cookie,
            authorization_header=authorization_header,
            session_codec=_state.session_codec or _NULL_SESSION_CODEC,  # type: ignore[arg-type]
            service_token_verifier=_state.service_token_verifier or ServiceTokenVerifier([]),
            oidc_verifier=_state.oidc_verifier,
            authorizer=_state.authorizer,
        )
    except AuthError as exc:
        raise _to_http_exception(exc) from exc


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
