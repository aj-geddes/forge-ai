"""Health check endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from forge_gateway.models import HealthResponse

router = APIRouter(tags=["health"])

_ready = False
_started = False


def set_ready(ready: bool) -> None:
    global _ready
    _ready = ready


def set_started(started: bool) -> None:
    global _started
    _started = started


@router.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def readiness() -> HealthResponse:
    if not _ready:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Not ready")
    return HealthResponse(status="ready")


@router.get("/health/startup", response_model=HealthResponse)
async def startup() -> HealthResponse:
    if not _started:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Starting up")
    return HealthResponse(status="started")
