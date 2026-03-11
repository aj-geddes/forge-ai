"""Tests for forge_security.middleware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge_config.schema import SecurityConfig
from forge_security.audit import AuditLogger
from forge_security.identity import ForgeIdentityManager
from forge_security.middleware import GateResult, SecurityGate
from forge_security.trust import TrustPolicyEnforcer

# ---------------------------------------------------------------------------
# Mock authorization provider
# ---------------------------------------------------------------------------


@dataclass
class _MockAuthzDecision:
    allowed: bool
    reason: str


class _AllowAllAuthz:
    async def check(
        self,
        caller_id: str,
        resource: str,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> _MockAuthzDecision:
        return _MockAuthzDecision(allowed=True, reason="mock allow")


class _DenyAllAuthz:
    async def check(
        self,
        caller_id: str,
        resource: str,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> _MockAuthzDecision:
        return _MockAuthzDecision(allowed=False, reason="mock deny")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_gate(
    authz=None,
    allowed_origins: list[str] | None = None,
    rate_limit_rpm: int = 60,
) -> SecurityGate:
    config = SecurityConfig(
        allowed_origins=allowed_origins if allowed_origins is not None else ["*"],
        rate_limit_rpm=rate_limit_rpm,
    )
    return SecurityGate(
        identity_manager=ForgeIdentityManager(trust_domain="test.local"),
        trust_enforcer=TrustPolicyEnforcer(config),
        audit_logger=AuditLogger(),
        authz_provider=authz,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSecurityGate:
    async def test_allowed_no_authz(self):
        gate = _make_gate()
        result = await gate("caller-1", "search")
        assert result.allowed is True
        assert result.identity == "caller-1"
        assert result.audit_event is not None

    async def test_allowed_with_authz(self):
        gate = _make_gate(authz=_AllowAllAuthz())
        result = await gate("caller-1", "search")
        assert result.allowed is True

    async def test_denied_by_authz(self):
        gate = _make_gate(authz=_DenyAllAuthz())
        result = await gate("caller-1", "search")
        assert result.allowed is False
        assert "mock deny" in result.reason

    async def test_denied_by_origin(self):
        gate = _make_gate(allowed_origins=["https://allowed.com"])
        result = await gate("caller", "tool", origin="https://evil.com")
        assert result.allowed is False
        assert "not in allowed list" in result.reason

    async def test_denied_by_rate_limit(self):
        gate = _make_gate(rate_limit_rpm=1)
        r1 = await gate("agent", "tool")
        assert r1.allowed is True
        r2 = await gate("agent", "tool")
        assert r2.allowed is False
        assert "Rate limit" in r2.reason

    async def test_from_config(self):
        config = SecurityConfig()
        gate = SecurityGate.from_config(config)
        result = await gate("test", "ping")
        assert result.allowed is True

    async def test_authenticate_returns_caller_id(self):
        gate = _make_gate()
        identity = await gate.authenticate("spiffe://test/agent/x")
        assert identity == "spiffe://test/agent/x"

    async def test_authorize_tool_call_no_provider(self):
        gate = _make_gate()
        decision = await gate.authorize_tool_call("caller", "tool")
        assert decision.allowed is True

    async def test_authorize_tool_call_with_provider(self):
        gate = _make_gate(authz=_DenyAllAuthz())
        decision = await gate.authorize_tool_call("caller", "tool")
        assert decision.allowed is False

    async def test_audit_tool_call(self):
        gate = _make_gate()
        evt = await gate.audit_tool_call("c", "t", allowed=True, reason="ok")
        assert evt.caller_id == "c"
        assert evt.tool_name == "t"

    async def test_check_rate_limit(self):
        gate = _make_gate(rate_limit_rpm=2)
        assert await gate.check_rate_limit("id") is True
        assert await gate.check_rate_limit("id") is True
        assert await gate.check_rate_limit("id") is False

    async def test_gate_result_fields(self):
        gate = _make_gate()
        result = await gate("agent-x", "my-tool", origin=None, context={"k": "v"})
        assert isinstance(result, GateResult)
        assert result.identity == "agent-x"
        assert result.reason == "all checks passed"
