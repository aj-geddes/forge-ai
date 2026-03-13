"""SecurityGate FastAPI dependency for agent-facing routes.

Wraps the ``forge_security.SecurityGate`` pipeline (identity, trust,
rate-limit, audit) into a reusable FastAPI dependency.  When security
is not configured, falls back to unauthenticated development mode with
a logged warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from forge_security import GateResult, SecurityGate

logger = logging.getLogger("forge.gateway.security")

# ---------------------------------------------------------------------------
# Module-level state (wired during app lifespan)
# ---------------------------------------------------------------------------

_security_gate: SecurityGate | None = None
_dev_mode: bool = True

_bearer_scheme = HTTPBearer(auto_error=False)

_DEV_MODE_IDENTITY = "dev-anonymous"


def set_security_gate(gate: SecurityGate | None) -> None:
    """Wire the ``SecurityGate`` from the application lifespan.

    When *gate* is ``None`` the dependency operates in development mode,
    allowing all requests through with a warning.
    """
    global _security_gate, _dev_mode
    _security_gate = gate
    _dev_mode = gate is None
    if _dev_mode:
        logger.warning(
            "SecurityGate not configured — running in DEVELOPMENT mode. "
            "All agent routes allow unauthenticated access."
        )


# ---------------------------------------------------------------------------
# Authenticated caller identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallerIdentity:
    """Represents an authenticated (or dev-mode) caller."""

    identity: str
    dev_mode: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_caller_id(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None,
) -> str | None:
    """Extract caller identity from the request.

    Checks (in order):
    1. ``Authorization: Bearer <token>`` header (the token *is* the identity)
    2. ``X-Caller-ID`` custom header
    3. ``caller_id`` query parameter

    Returns ``None`` if no identity can be determined.
    """
    if bearer is not None:
        return bearer.credentials
    caller_header = request.headers.get("X-Caller-ID")
    if caller_header:
        return caller_header
    caller_param = request.query_params.get("caller_id")
    if caller_param:
        return caller_param
    return None


def _extract_origin(request: Request) -> str | None:
    """Extract the request origin for trust-policy evaluation."""
    return request.headers.get("Origin") or request.headers.get("Referer")


def _route_name(request: Request) -> str:
    """Derive a short route name for audit logging."""
    route = getattr(request, "scope", {}).get("route")
    if route is not None:
        return getattr(route, "name", request.url.path)
    return request.url.path


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def _classify_denial(reason: str) -> int:
    """Map a denial reason to the appropriate HTTP status code."""
    lower = reason.lower()
    if "rate limit" in lower:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if "origin" in lower:
        return status.HTTP_403_FORBIDDEN
    return status.HTTP_403_FORBIDDEN


async def require_security(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = None,
) -> CallerIdentity:
    """Core security check logic.

    In **production mode** (SecurityGate configured):
    - Extracts caller identity from the request.
    - Runs the full SecurityGate pipeline (authenticate, trust, authz, audit).
    - Returns ``CallerIdentity`` on success.
    - Raises 401 (no credentials), 403 (denied), or 429 (rate-limited).

    In **development mode** (no SecurityGate):
    - Allows all requests through with a synthetic dev identity.
    """
    if _dev_mode or _security_gate is None:
        return CallerIdentity(identity=_DEV_MODE_IDENTITY, dev_mode=True)

    # Extract caller identity
    caller_id = _extract_caller_id(request, bearer)
    if caller_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing caller identity (provide Authorization header or X-Caller-ID)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Run the SecurityGate pipeline
    origin = _extract_origin(request)
    route = _route_name(request)

    result: GateResult = await _security_gate(
        caller_id=caller_id,
        tool_name=route,
        origin=origin,
    )

    if not result.allowed:
        http_status = _classify_denial(result.reason)
        raise HTTPException(status_code=http_status, detail=result.reason)

    return CallerIdentity(identity=result.identity)


# ---------------------------------------------------------------------------
# Top-level dependency with bearer scheme injection
# ---------------------------------------------------------------------------


async def security_dependency(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),  # noqa: B008
) -> CallerIdentity:
    """FastAPI dependency that enforces SecurityGate checks.

    Use this as ``dependencies=[Depends(security_dependency)]`` on routers,
    or as a parameter dependency on individual endpoints to receive the
    ``CallerIdentity``.
    """
    return await require_security(request, bearer)
