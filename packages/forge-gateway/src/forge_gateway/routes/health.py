"""Health check endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from forge_gateway.models import HealthResponse

router = APIRouter(tags=["health"])

_FAILED = "failed"

_ready = False
_started = False
_version = ""
_components: dict[str, str] = {}
_auth_mode = "enforce"
_auth_healthy = True


def set_ready(ready: bool) -> None:
    """Set whether the gateway has completed startup and is accepting traffic."""
    global _ready
    _ready = ready


def set_started(started: bool) -> None:
    """Set whether the gateway process has begun serving requests."""
    global _started
    _started = started


def set_version(version: str) -> None:
    """Set the version reported by the health endpoints (from config metadata)."""
    global _version
    _version = version


def set_component_status(name: str, status: str) -> None:
    """Record the health status of a named subsystem (e.g. ``"agent"``)."""
    _components[name] = status


def set_auth_mode(mode: str) -> None:
    """Record the declared auth enforcement mode (ADR-0001 SS7.1) for
    ``/health/ready`` and the UI's insecure-mode banner."""
    global _auth_mode
    _auth_mode = mode


def set_auth_healthy(healthy: bool) -> None:
    """Record whether the auth subsystem has at least one working
    credential mechanism. ``False`` marks the gateway NOT READY
    (ADR-0001 SS7.3) so a pod with a broken auth config never takes
    traffic in a rolling update."""
    global _auth_healthy
    _auth_healthy = healthy


def reset_components() -> None:
    """Clear all recorded component statuses."""
    _components.clear()


def _is_ready() -> bool:
    """Whether the gateway is ready to serve traffic.

    Overall readiness requires startup to have completed, no tracked
    component to be in a ``"failed"`` state, and the auth subsystem to
    have at least one working credential mechanism (ADR-0001 SS7.3: total
    auth failure is NOT READY, never a silent bypass). A component that is
    merely ``"unavailable"`` (e.g. an optional subsystem was never
    configured) does not block readiness.
    """
    if not _ready:
        return False
    if not _auth_healthy:
        return False
    return _FAILED not in _components.values()


@router.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Liveness probe: always 200 while the process is serving requests."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def readiness() -> HealthResponse | JSONResponse:
    """Readiness probe: 503 when startup is incomplete, a component failed,
    or the auth subsystem cannot operate."""
    if not _is_ready():
        body = HealthResponse(
            status="not_ready",
            version=_version,
            components=dict(_components),
            auth_mode=_auth_mode,
            auth_healthy=_auth_healthy,
        )
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(
        status="ready",
        version=_version,
        components=dict(_components),
        auth_mode=_auth_mode,
        auth_healthy=_auth_healthy,
    )


@router.get("/health/startup", response_model=HealthResponse)
async def startup() -> HealthResponse:
    """Startup probe: 503 until the gateway process has begun serving requests."""
    if not _started:
        raise HTTPException(status_code=503, detail="Starting up")
    return HealthResponse(status="started")
