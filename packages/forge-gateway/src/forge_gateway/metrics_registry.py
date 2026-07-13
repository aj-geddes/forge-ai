"""
Central registry for application Prometheus metrics.

Metrics defined here are recorded in the gateway's request path and exposed via
the /metrics endpoint (packages/forge-gateway/src/forge_gateway/routes/metrics.py),
which calls prometheus_client.generate_latest() against the default global registry.
All metrics are constructed against the default REGISTRY, with no explicit registry= argument.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

if PROMETHEUS_AVAILABLE:
    FORGE_HTTP_REQUESTS_TOTAL = Counter(
        "forge_http_requests_total",
        "Total HTTP requests handled by the gateway.",
        ["method", "path", "status"],
    )
    FORGE_HTTP_REQUEST_DURATION_SECONDS = Histogram(
        "forge_http_request_duration_seconds",
        "HTTP request latency in seconds.",
        ["method", "path", "status"],
    )
    FORGE_AGENT_INVOCATIONS_TOTAL = Counter(
        "forge_agent_invocations_total",
        "Total agent invocations, by interface and outcome.",
        ["interface", "status"],
    )
    FORGE_AGENT_INVOCATION_DURATION_SECONDS = Histogram(
        "forge_agent_invocation_duration_seconds",
        "Agent invocation latency in seconds, by interface.",
        ["interface"],
    )
    FORGE_TOOL_INVOCATIONS_TOTAL = Counter(
        "forge_tool_invocations_total",
        "Total tool invocations executed by the agent, by tool name.",
        ["tool"],
    )


def record_http_request(method: str, path: str, status_code: int, duration_seconds: float) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    status_label = str(status_code)
    FORGE_HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=status_label).inc()
    FORGE_HTTP_REQUEST_DURATION_SECONDS.labels(
        method=method, path=path, status=status_label
    ).observe(duration_seconds)


def record_agent_invocation(interface: str, status: str, duration_seconds: float) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    FORGE_AGENT_INVOCATIONS_TOTAL.labels(interface=interface, status=status).inc()
    FORGE_AGENT_INVOCATION_DURATION_SECONDS.labels(interface=interface).observe(duration_seconds)


def record_tool_invocation(tool_name: str) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    FORGE_TOOL_INVOCATIONS_TOTAL.labels(tool=tool_name).inc()
