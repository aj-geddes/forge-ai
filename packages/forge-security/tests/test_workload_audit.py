"""Tests for forge_security.workload.audit.build_audit_trail (ADR-0004 SS7.1).

Default is ``StdoutAuditBackend`` (JSON-lines to stdout -> Promtail/Loki):
no volume needed, safe with any replica count. ``FileAuditBackend`` is
opt-in only when a path is actually configured. Either way, the resulting
``AuditTrail`` must actually record ``peer_verification`` and
``auth_check`` events (the two workload-plane event types ADR-0004 SS7.1
commits to).

``audit_backend``/``audit_path`` are ADR-0004 SS7.3 additions to
``forge_config.schema.AgentWeaveConfig`` that are a *separate*,
not-yet-landed forge-config change (out of scope here -- forge-security
only). ``build_audit_trail`` reads them via ``getattr`` with defaults so
it works against both the current schema (falls back to stdout) and a
future one that defines these fields -- exercised below with a small
duck-typed stand-in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentweave.observability.audit import FileAuditBackend, StdoutAuditBackend
from forge_config.schema import AgentWeaveConfig
from forge_security.workload.audit import build_audit_trail


@dataclass
class _ConfigWithAuditFields:
    """Stands in for a future ``AgentWeaveConfig`` that defines
    ``audit_backend``/``audit_path`` (ADR-0004 SS7.3)."""

    audit_backend: str
    audit_path: str | None


class TestBuildAuditTrailBackendSelection:
    def test_defaults_to_stdout_backend_for_the_current_schema(self):
        trail = build_audit_trail(AgentWeaveConfig(), agent_name="forge-gateway")

        assert isinstance(trail.backend, StdoutAuditBackend)
        assert trail.agent_name == "forge-gateway"

    def test_uses_file_backend_when_configured_with_a_path(self, tmp_path: Path):
        audit_path = tmp_path / "workload-audit.jsonl"
        cfg = _ConfigWithAuditFields(audit_backend="file", audit_path=str(audit_path))

        trail = build_audit_trail(cfg, agent_name="forge-gateway")

        assert isinstance(trail.backend, FileAuditBackend)

    def test_falls_back_to_stdout_when_file_backend_requested_without_a_path(self):
        cfg = _ConfigWithAuditFields(audit_backend="file", audit_path=None)

        trail = build_audit_trail(cfg, agent_name="forge-gateway")

        assert isinstance(trail.backend, StdoutAuditBackend)


class TestAuditTrailRecordsWorkloadEvents:
    async def test_records_peer_verification_and_auth_check(self, tmp_path: Path):
        audit_path = tmp_path / "workload-audit.jsonl"
        cfg = _ConfigWithAuditFields(audit_backend="file", audit_path=str(audit_path))
        trail = build_audit_trail(cfg, agent_name="forge-gateway")

        await trail.record_peer_verification(
            peer_id="spiffe://hvslocal/ns/dev-aj-geddes/sa/caller",
            status="success",
        )
        await trail.record_auth_check(
            caller_id="spiffe://hvslocal/ns/dev-aj-geddes/sa/caller",
            action="a2a:task",
            resource="spiffe://hvslocal/ns/dev-aj-geddes/sa/default",
            decision="allow",
            duration=0.01,
        )
        await trail.close()

        lines = audit_path.read_text().strip().splitlines()
        events = [json.loads(line) for line in lines]

        assert any(e["event_type"] == "PEER_VERIFICATION" for e in events)
        assert any(e["event_type"] == "AUTH_CHECK" for e in events)
