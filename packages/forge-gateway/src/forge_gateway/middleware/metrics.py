from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from forge_gateway import metrics_registry


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP request metrics using a path template (e.g., '/items/{id}')
    to avoid high-cardinality labels from raw paths; falls back to raw path
    only when route metadata is unavailable (e.g., unmatched 404s)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        route = request.scope.get("route")
        path = route.path if route is not None else request.url.path

        metrics_registry.record_http_request(request.method, path, response.status_code, elapsed)

        return response
