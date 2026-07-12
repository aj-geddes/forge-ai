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


def reset_components() -> None:
    """Clear all recorded component statuses."""
    _components.clear()


def _is_ready() -> bool:
    """Whether the gateway is ready to serve traffic.

    Overall readiness requires startup to have completed *and* no tracked
    component to be in a ``"failed"`` state. A component that is merely
    ``"unavailable"`` (e.g. an optional subsystem was never configured) does
    not block readiness.
    """
    if not _ready:
        return False
    return _FAILED not in _components.values()


@router.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Liveness probe: always 200 while the process is serving requests."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def readiness() -> HealthResponse | JSONResponse:
    """Readiness probe: 503 when startup is incomplete or a component failed."""
    if not _is_ready():
        body = HealthResponse(
            status="not_ready",
            version=_version,
            components=dict(_components),
        )
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(status="ready", version=_version, components=dict(_components))


@router.get("/health/startup", response_model=HealthResponse)
async def startup() -> HealthResponse:
    """Startup probe: 503 until the gateway process has begun serving requests."""
    if not _started:
        raise HTTPException(status_code=503, detail="Starting up")
    return HealthResponse(status="started")
