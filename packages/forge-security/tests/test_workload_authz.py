"""Tests for forge_security.workload.authz (ADR-0004 SS5, SS8).

The central guarantee under test: authorization is **fail closed**. An
explicit OPA "deny" raises; and -- separately, defense-in-depth on top of
``OPAProvider(default_deny=True)`` -- any exception raised while querying
the authorization provider (unreachable OPA, network error, ...) is *also*
treated as a deny. There is no path in ``authorize_workload`` that turns a
provider failure into an allow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from forge_security.oidc.principal import Principal
from forge_security.workload.authz import authorize_workload, build_authz_context
from forge_security.workload.errors import WorkloadForbidden

CALLER = "spiffe://hvslocal/ns/dev-aj-geddes/sa/caller"
RESOURCE = "spiffe://hvslocal/ns/dev-aj-geddes/sa/default"
FOREIGN_RESOURCE = "spiffe://other-trust-domain/ns/other/sa/default"


@dataclass
class _Decision:
    allowed: bool
    reason: str = ""


@dataclass
class _FakeAuthz:
    """Stands in for both ``OPAProvider`` and the mock adapter -- anything
    satisfying ``check(caller_id, resource, action, context) -> decision``."""

    decision: _Decision | None = None
    exc: Exception | None = None
    calls: list[tuple[str, str, str, dict[str, Any] | None]] = field(default_factory=list)

    async def check(
        self, caller_id: str, resource: str, action: str, context: dict[str, Any] | None = None
    ) -> _Decision:
        self.calls.append((caller_id, resource, action, context))
        if self.exc is not None:
            raise self.exc
        assert self.decision is not None
        return self.decision


@dataclass
class _FakeAudit:
    auth_checks: list[dict[str, Any]] = field(default_factory=list)

    async def record_auth_check(
        self,
        caller_id: str,
        action: str,
        resource: str,
        decision: str,
        duration: float,
        reason: str = "",
    ) -> None:
        self.auth_checks.append(
            {
                "caller_id": caller_id,
                "action": action,
                "resource": resource,
                "decision": decision,
                "reason": reason,
            }
        )


def _principal(caller: str = CALLER) -> Principal:
    return Principal(kind="workload", sub=caller, spiffe_id=caller)


class TestBuildAuthzContext:
    def test_shape_matches_adr_0004_section_5(self):
        context = build_authz_context(task_type="a2a:task", tool="search", peer_trust_level="high")

        assert context == {"task_type": "a2a:task", "tool": "search", "peer_trust_level": "high"}


class TestAuthorizeWorkloadAllow:
    async def test_allow_returns_decision_and_audits_allow(self):
        authz = _FakeAuthz(decision=_Decision(allowed=True, reason="same trust domain"))
        audit = _FakeAudit()

        decision = await authorize_workload(
            _principal(), "a2a:task", RESOURCE, authz=authz, audit=audit
        )

        assert decision.allowed is True
        assert authz.calls == [(CALLER, RESOURCE, "a2a:task", build_authz_context())]
        assert audit.auth_checks[0]["decision"] == "allow"


class TestAuthorizeWorkloadDeny:
    async def test_opa_deny_raises_403(self):
        authz = _FakeAuthz(decision=_Decision(allowed=False, reason="not a known peer"))
        audit = _FakeAudit()

        with pytest.raises(WorkloadForbidden) as exc_info:
            await authorize_workload(_principal(), "a2a:task", RESOURCE, authz=authz, audit=audit)

        assert exc_info.value.status == 403
        assert audit.auth_checks[0]["decision"] == "deny"
        assert audit.auth_checks[0]["reason"] == "not a known peer"

    async def test_peer_from_wrong_trust_domain_is_rejected(self):
        """Simulates the starter rego's same-trust-domain check (ADR-0004
        SS5): the policy provider denies because caller and resource live
        in different trust domains. Our code must propagate that deny,
        not paper over it."""

        async def _same_trust_domain_only(
            caller_id: str, resource: str, action: str, context: dict[str, Any] | None = None
        ) -> _Decision:
            caller_domain = caller_id.split("/")[2]
            resource_domain = resource.split("/")[2]
            if caller_domain != resource_domain:
                return _Decision(allowed=False, reason="cross-trust-domain call denied")
            return _Decision(allowed=True, reason="same trust domain")

        class _TrustDomainAuthz:
            check = staticmethod(_same_trust_domain_only)

        audit = _FakeAudit()

        with pytest.raises(WorkloadForbidden) as exc_info:
            await authorize_workload(
                _principal(),
                "a2a:task",
                FOREIGN_RESOURCE,
                authz=_TrustDomainAuthz(),
                audit=audit,
            )

        assert exc_info.value.status == 403
        assert audit.auth_checks[0]["decision"] == "deny"


class TestAuthorizeWorkloadFailClosed:
    async def test_opa_unreachable_raises_and_is_treated_as_deny_not_allow(self):
        authz = _FakeAuthz(exc=ConnectionError("opa.opa.svc.cluster.local:8181 unreachable"))
        audit = _FakeAudit()

        with pytest.raises(WorkloadForbidden) as exc_info:
            await authorize_workload(_principal(), "a2a:task", RESOURCE, authz=authz, audit=audit)

        assert exc_info.value.status == 403
        # Fail closed: the audit trail must record this as a DENY, never
        # as an allow -- an exception must not silently authorize.
        assert audit.auth_checks[0]["decision"] == "deny"
        assert "unreachable" in audit.auth_checks[0]["reason"]

    async def test_authz_provider_raising_any_exception_never_allows(self):
        authz = _FakeAuthz(exc=RuntimeError("boom"))
        audit = _FakeAudit()

        with pytest.raises(WorkloadForbidden):
            await authorize_workload(
                _principal(), "tools:invoke", RESOURCE, authz=authz, audit=audit
            )
