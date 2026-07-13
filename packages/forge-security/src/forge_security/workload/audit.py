"""Build the workload-plane audit trail (ADR-0004 SS7.1).

Thin wrapper over agentweave's ``AuditTrail``/``AuditBackend``: the only
Forge-specific logic is backend *selection*. Default is
``StdoutAuditBackend`` (JSON-lines to stdout -> Promtail/Loki) -- it
needs no volume and is safe with any replica count, so it's what a
config that never mentions audit settings gets. ``FileAuditBackend`` is
strictly opt-in, and only used when a path is actually configured;
requesting the file backend without a path falls back to stdout rather
than failing (a missing path must never silently drop the audit trail
that recorded it).
"""

from __future__ import annotations

from typing import Any

from agentweave.observability.audit import (
    AuditBackend,
    AuditTrail,
    FileAuditBackend,
    StdoutAuditBackend,
)


def build_audit_trail(agentweave_config: Any, *, agent_name: str = "forge-agent") -> AuditTrail:
    """Build the workload-plane :class:`agentweave.observability.audit.AuditTrail`.

    ``agentweave_config`` is typically a ``forge_config.schema.AgentWeaveConfig``.
    ``audit_backend``/``audit_path`` are read via ``getattr`` with
    defaults (not required attributes) so this stays forward-compatible
    with configs that don't define them yet (ADR-0004 SS7.3).
    """
    backend_name = getattr(agentweave_config, "audit_backend", "stdout")
    audit_path = getattr(agentweave_config, "audit_path", None)

    backend: AuditBackend
    if backend_name == "file" and audit_path:
        backend = FileAuditBackend(audit_path)
    else:
        backend = StdoutAuditBackend()

    return AuditTrail(agent_name=agent_name, backend=backend)
