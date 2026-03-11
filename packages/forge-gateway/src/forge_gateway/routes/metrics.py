"""Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """Expose Prometheus metrics."""
    try:
        from prometheus_client import generate_latest

        return generate_latest().decode("utf-8")
    except ImportError:
        return "# prometheus_client not available\n"
