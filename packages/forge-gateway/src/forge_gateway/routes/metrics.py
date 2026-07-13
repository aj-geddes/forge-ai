"""Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from forge_gateway import security

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    dependencies=[Depends(security.require_metrics_access)],
)
async def metrics() -> str:
    """Expose Prometheus metrics.

    Public by default (ADR-0001 SS6.5, ``authorization.metrics_public``):
    Prometheus scrapes the pod IP directly, bypassing normal ingress auth,
    so requiring a credential here would break scraping. Set
    ``metrics_public: false`` to require the ``metrics:read`` permission
    instead.
    """
    try:
        from prometheus_client import generate_latest

        return generate_latest().decode("utf-8")
    except ImportError:
        return "# prometheus_client not available\n"
