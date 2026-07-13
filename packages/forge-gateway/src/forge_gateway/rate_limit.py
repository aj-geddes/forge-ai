"""Per-process rate limiting for the gateway request path.

A security review found that ``security.rate_limit_rpm`` existed in the
config schema and in the ADR-0001 failure matrix (429 ``rate_limited``) but
was wired to nothing -- this module is the fix.

Two independent :class:`~forge_security.rate_limit.SlidingWindowRateLimiter`
instances:

* the **principal** limiter -- keyed on authenticated identity (a
  :class:`~forge_security.oidc.Principal`'s ``token_id`` for service
  tokens, otherwise its ``sub``). Enforced from
  ``forge_gateway.security.get_principal``, which every protected route
  reaches via ``require_permission`` / ``enforce_mcp_security`` (and
  ``/v1/auth/me`` and ``/metrics`` when ``metrics_public: false``) -- so
  wiring it there covers the whole authenticated surface in one place.
* the **auth-flow** limiter -- keyed on client IP (``ip:<host>``), applied
  only to ``/auth/login`` and ``/auth/callback`` via
  :func:`enforce_auth_flow_rate_limit`. No principal exists yet at that
  point in the OIDC redirect flow, and this pair is the documented abuse
  vector: hammering ``/auth/callback`` with garbage authorization codes.

Both are deliberately **per-process, in-memory** state -- not shared across
replicas. That means the *effective* fleet-wide budget is
``rate_limit_rpm * replica_count``, not a hard ceiling. This matches the
current single-replica deployment; a shared store (e.g. Redis) would be
required before scaling out to multiple replicas.

Rate limiting is **disabled by default** (``configure_rate_limiting`` is
never called with a positive value) so that code paths which construct
:class:`~forge_gateway.security.Principal`-resolving apps without an explicit
config -- most of the test suite, which drives ``get_principal`` through the
repo-wide ``dev_insecure`` autouse fixture -- are never limited by surprise.
Real deployments enable it from the application lifespan
(``forge_gateway.app._init_auth``), sourced from
``SecurityConfig.rate_limit_rpm``.

Fail-open on internal errors: a bug in the limiter itself (the *throttle*)
must never take the service down. This is deliberately asymmetric with
authentication (the *authorization gate*, in ``forge_gateway.security``),
which is fail-closed by design (ADR-0001). If ``SlidingWindowRateLimiter.check``
raises, the error is logged at ``ERROR`` (via ``logger.exception``) and the
request is allowed through.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request
from forge_security.rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger("forge.gateway.rate_limit")

_WINDOW_SECONDS = 60.0
_MIN_RETRY_AFTER_SECONDS = 1


@dataclass
class _RateLimitState:
    """Module-level rate-limiter wiring, replaced wholesale by
    :func:`configure_rate_limiting`. Both fields ``None`` means disabled."""

    principal_limiter: SlidingWindowRateLimiter | None = None
    auth_flow_limiter: SlidingWindowRateLimiter | None = None


_state = _RateLimitState()


def configure_rate_limiting(rpm: int | None) -> None:
    """Enable (or disable) rate limiting for both limiters at *rpm* requests
    per 60-second window (``security.rate_limit_rpm``).

    ``rpm`` of ``None`` or ``<= 0`` disables rate limiting entirely and
    drops any bucket state -- this is the default, and what every existing
    ``configure_auth`` call site that doesn't pass ``rate_limit_rpm`` gets.
    """
    global _state
    if rpm is None or rpm <= 0:
        _state = _RateLimitState()
        return
    _state = _RateLimitState(
        principal_limiter=SlidingWindowRateLimiter(
            max_requests=rpm, window_seconds=_WINDOW_SECONDS
        ),
        auth_flow_limiter=SlidingWindowRateLimiter(
            max_requests=rpm, window_seconds=_WINDOW_SECONDS
        ),
    )


def reset_rate_limiting() -> None:
    """Test-only: disable rate limiting and drop all bucket state."""
    configure_rate_limiting(None)


def is_rate_limiting_enabled() -> bool:
    """Whether a positive ``rate_limit_rpm`` is currently configured."""
    return _state.principal_limiter is not None


async def _enforce(limiter: SlidingWindowRateLimiter, key: str) -> None:
    """Check *key* against *limiter*; raise 429 if exceeded.

    Fails open on any exception from the limiter itself: this throttle must
    never take the service down. The 429 raised below is deliberately
    outside this try/except so it propagates -- only errors from
    ``limiter.check`` are swallowed.
    """
    try:
        result = await limiter.check(key)
    except Exception:
        logger.exception(
            "Rate limiter raised while checking %r; failing open (allowing request)", key
        )
        return

    if not result.allowed:
        retry_after = max(_MIN_RETRY_AFTER_SECONDS, round(result.reset_after))
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": str(retry_after)},
        )


async def enforce_principal_rate_limit(identity: str) -> None:
    """Enforce the identity-keyed limit for an authenticated principal.

    A no-op when rate limiting is disabled. Called from
    ``forge_gateway.security.get_principal`` for every principal resolved,
    including the ``dev_insecure`` short-circuit.
    """
    if _state.principal_limiter is None:
        return
    await _enforce(_state.principal_limiter, identity)


async def enforce_auth_flow_rate_limit(request: Request) -> None:
    """FastAPI dependency: IP-keyed rate limit for the pre-authentication
    OIDC login flow (``/auth/login``, ``/auth/callback``).

    No principal exists yet at this point in the flow, so this keys on the
    client's connecting IP instead. Note this is the direct TCP peer, not
    any ``X-Forwarded-For`` value -- trusting a client-supplied header for
    rate-limit keying would let an attacker rotate the key on every
    request and defeat the limiter entirely.
    """
    if _state.auth_flow_limiter is None:
        return
    client_ip = request.client.host if request.client else "unknown"
    await _enforce(_state.auth_flow_limiter, f"ip:{client_ip}")
