"""Tests for forge_security.trust."""

from forge_config.schema import SecurityConfig
from forge_security.rate_limit import SlidingWindowRateLimiter
from forge_security.trust import TrustPolicyEnforcer


def _make_config(
    allowed_origins: list[str] | None = None,
    rate_limit_rpm: int = 60,
) -> SecurityConfig:
    return SecurityConfig(
        allowed_origins=allowed_origins if allowed_origins is not None else ["*"],
        rate_limit_rpm=rate_limit_rpm,
    )


class TestTrustPolicyEnforcer:
    async def test_wildcard_origin_allows_all(self):
        enforcer = TrustPolicyEnforcer(_make_config(allowed_origins=["*"]))
        decision = await enforcer.evaluate("agent-1", origin="https://evil.com")
        assert decision.allowed is True

    async def test_specific_origin_allowed(self):
        enforcer = TrustPolicyEnforcer(_make_config(allowed_origins=["https://app.example.com"]))
        d = await enforcer.evaluate("a", origin="https://app.example.com")
        assert d.allowed is True

    async def test_origin_denied(self):
        enforcer = TrustPolicyEnforcer(_make_config(allowed_origins=["https://allowed.com"]))
        d = await enforcer.evaluate("a", origin="https://notallowed.com")
        assert d.allowed is False
        assert "not in allowed list" in d.reason

    async def test_glob_origin_pattern(self):
        enforcer = TrustPolicyEnforcer(_make_config(allowed_origins=["https://*.example.com"]))
        assert (await enforcer.evaluate("a", origin="https://app.example.com")).allowed
        assert not (await enforcer.evaluate("a", origin="https://other.com")).allowed

    async def test_no_origin_skips_check(self):
        enforcer = TrustPolicyEnforcer(_make_config(allowed_origins=["https://only.this.com"]))
        d = await enforcer.evaluate("a", origin=None)
        assert d.allowed is True

    async def test_rate_limit_enforced(self):
        enforcer = TrustPolicyEnforcer(_make_config(rate_limit_rpm=2))
        await enforcer.evaluate("agent", origin=None)
        await enforcer.evaluate("agent", origin=None)
        d = await enforcer.evaluate("agent", origin=None)
        assert d.allowed is False
        assert "Rate limit" in d.reason

    async def test_check_origin_method(self):
        enforcer = TrustPolicyEnforcer(_make_config(allowed_origins=["https://ok.com"]))
        assert (await enforcer.check_origin("https://ok.com")).allowed
        assert not (await enforcer.check_origin("https://bad.com")).allowed

    async def test_check_rate_limit_method(self):
        enforcer = TrustPolicyEnforcer(_make_config(rate_limit_rpm=1))
        r1 = await enforcer.check_rate_limit("x")
        assert r1.allowed is True
        r2 = await enforcer.check_rate_limit("x")
        assert r2.allowed is False

    async def test_custom_rate_limiter(self):
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
        enforcer = TrustPolicyEnforcer(_make_config(), rate_limiter=rl)
        await enforcer.evaluate("id1")
        d = await enforcer.evaluate("id1")
        assert d.allowed is False
