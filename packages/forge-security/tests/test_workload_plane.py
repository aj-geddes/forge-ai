"""Integration tests for forge_security.workload.providers.WorkloadPlane --
the single object forge-gateway consumes (ADR-0004 SS7.2).

``WorkloadPlane`` is directly constructable (it's a plain dataclass), so
these tests wire it up with small fakes to exercise the full
verify-then-authorize flow end to end without needing
``build_workload_plane``'s specific default policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from forge_security.oidc.principal import Principal
from forge_security.workload.errors import WorkloadForbidden, WorkloadUnauthenticated
from forge_security.workload.providers import WorkloadPlane

SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/caller"
MY_SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/default"


class _FakeIdentity:
    def __init__(self, spiffe_id: str = MY_SPIFFE_ID) -> None:
        self._spiffe_id = spiffe_id

    async def get_identity(self) -> str:
        return self._spiffe_id

    async def create_tls_context(self, server: bool = False) -> str:
        return f"ssl-context(server={server})"

    async def health_check(self) -> bool:
        return True


@dataclass
class _Decision:
    allowed: bool
    reason: str = ""


@dataclass
class _FakeAuthz:
    allowed: bool

    async def check(
        self, caller_id: str, resource: str, action: str, context: dict[str, Any] | None = None
    ) -> _Decision:
        return _Decision(allowed=self.allowed, reason="fixture decision")

    async def health_check(self) -> bool:
        return True


@dataclass
class _RecordingAudit:
    peer_verifications: list[dict[str, Any]] = field(default_factory=list)
    auth_checks: list[dict[str, Any]] = field(default_factory=list)

    async def record_peer_verification(self, peer_id: str, status: str, reason: str = "") -> None:
        self.peer_verifications.append({"peer_id": peer_id, "status": status, "reason": reason})

    async def record_auth_check(
        self,
        caller_id: str,
        action: str,
        resource: str,
        decision: str,
        duration: float,
        reason: str = "",
    ) -> None:
        self.auth_checks.append({"caller_id": caller_id, "decision": decision, "reason": reason})


def _plane(*, allow: bool = True) -> tuple[WorkloadPlane, _RecordingAudit]:
    audit = _RecordingAudit()
    plane = WorkloadPlane(identity=_FakeIdentity(), authz=_FakeAuthz(allowed=allow), audit=audit)
    return plane, audit


class TestVerifyPeer:
    async def test_valid_peer_returns_workload_principal_and_audits_success(self):
        plane, audit = _plane()

        principal = await plane.verify_peer(SPIFFE_ID)

        assert principal.kind == "workload"
        assert principal.spiffe_id == SPIFFE_ID
        assert audit.peer_verifications == [
            {"peer_id": SPIFFE_ID, "status": "success", "reason": ""}
        ]

    async def test_unknown_peer_is_rejected_and_audits_failure(self):
        plane, audit = _plane()

        with pytest.raises(WorkloadUnauthenticated) as exc_info:
            await plane.verify_peer(None)

        assert exc_info.value.status == 401
        assert audit.peer_verifications[0]["status"] == "failure"


class TestAuthorize:
    async def test_allow_returns_decision(self):
        plane, audit = _plane(allow=True)
        principal = Principal(kind="workload", sub=SPIFFE_ID, spiffe_id=SPIFFE_ID)

        decision = await plane.authorize(principal, "a2a:task", MY_SPIFFE_ID)

        assert decision.allowed is True
        assert audit.auth_checks[0]["decision"] == "allow"

    async def test_deny_raises_403_and_audits_deny(self):
        plane, audit = _plane(allow=False)
        principal = Principal(kind="workload", sub=SPIFFE_ID, spiffe_id=SPIFFE_ID)

        with pytest.raises(WorkloadForbidden) as exc_info:
            await plane.authorize(principal, "a2a:task", MY_SPIFFE_ID)

        assert exc_info.value.status == 403
        assert audit.auth_checks[0]["decision"] == "deny"

    async def test_context_dict_is_forwarded_to_the_authz_check(self):
        plane, _audit = _plane(allow=True)
        principal = Principal(kind="workload", sub=SPIFFE_ID, spiffe_id=SPIFFE_ID)

        # Must not raise -- a context dict shaped per ADR-0004 SS5 is
        # accepted and passed straight through.
        await plane.authorize(
            principal,
            "tools:invoke",
            MY_SPIFFE_ID,
            {"task_type": "search", "tool": "web_search", "peer_trust_level": "high"},
        )


class TestServerSslContextAndSpiffeId:
    async def test_server_ssl_context_delegates_to_identity(self):
        plane, _audit = _plane()

        ctx = await plane.server_ssl_context()

        assert ctx == "ssl-context(server=True)"

    async def test_my_spiffe_id_delegates_to_identity(self):
        plane, _audit = _plane()

        assert await plane.my_spiffe_id() == MY_SPIFFE_ID


class TestHealth:
    async def test_healthy_when_identity_and_authz_both_healthy(self):
        plane, _audit = _plane()

        health = await plane.health()

        assert health["status"] == "healthy"

    async def test_unhealthy_when_identity_health_check_fails(self):
        class _UnhealthyIdentity(_FakeIdentity):
            async def health_check(self) -> bool:
                return False

        audit = _RecordingAudit()
        plane = WorkloadPlane(
            identity=_UnhealthyIdentity(), authz=_FakeAuthz(allowed=True), audit=audit
        )

        health = await plane.health()

        assert health["status"] == "unhealthy"
        assert health["identity"] == "down"

    async def test_health_does_not_raise_when_a_component_health_check_raises(self):
        class _ExplodingAuthz(_FakeAuthz):
            async def health_check(self) -> bool:
                raise ConnectionError("opa down")

        audit = _RecordingAudit()
        plane = WorkloadPlane(
            identity=_FakeIdentity(), authz=_ExplodingAuthz(allowed=True), audit=audit
        )

        health = await plane.health()

        assert health["status"] == "unhealthy"
        assert health["authz"] == "down"
