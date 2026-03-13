"""Tests for forge_security.middleware."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from forge_config.schema import SecurityConfig
from forge_security.audit import AuditLogger
from forge_security.identity import ForgeIdentityManager
from forge_security.middleware import (
    GateResult,
    JWTAuthenticationError,
    SecurityGate,
)
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
    jwt_secret: str | None = None,
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
        jwt_secret=jwt_secret,
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


# ---------------------------------------------------------------------------
# JWT Authentication Tests
# ---------------------------------------------------------------------------

JWT_SECRET = "test-secret-key-for-jwt-hs256-min32bytes!"
WRONG_SECRET = "wrong-secret-key-definitely-not-right!!"


def _make_jwt(
    sub: str | None = "agent-1",
    secret: str = JWT_SECRET,
    exp_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create an HS256-signed JWT for testing."""
    payload: dict[str, Any] = {}
    if sub is not None:
        payload["sub"] = sub
    if exp_delta is not None:
        payload["exp"] = datetime.now(tz=UTC) + exp_delta
    else:
        # Default: valid for 1 hour
        payload["exp"] = datetime.now(tz=UTC) + timedelta(hours=1)
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm="HS256")


class TestJWTAuthentication:
    """Tests for JWT-based authentication in SecurityGate."""

    async def test_authenticate_with_valid_jwt(self):
        """A valid HS256 JWT with sub claim returns the subject."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub="agent-1")
        identity = await gate.authenticate(token)
        assert identity == "agent-1"

    async def test_authenticate_with_invalid_jwt_signature(self):
        """A JWT signed with the wrong secret is rejected."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub="agent-1", secret=WRONG_SECRET)
        with pytest.raises(JWTAuthenticationError, match="JWT verification failed"):
            await gate.authenticate(token)

    async def test_authenticate_with_expired_jwt(self):
        """An expired JWT is rejected."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub="agent-1", exp_delta=timedelta(hours=-1))
        with pytest.raises(JWTAuthenticationError, match="JWT verification failed"):
            await gate.authenticate(token)

    async def test_authenticate_with_missing_sub_claim(self):
        """A valid JWT that lacks a 'sub' claim is rejected."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub=None)
        with pytest.raises(JWTAuthenticationError, match="missing required 'sub' claim"):
            await gate.authenticate(token)

    async def test_authenticate_without_jwt_secret_trusts_raw(self):
        """Without jwt_secret configured, raw caller_id is trusted as-is."""
        gate = _make_gate(jwt_secret=None)
        identity = await gate.authenticate("raw-id")
        assert identity == "raw-id"

    async def test_authenticate_with_non_jwt_token_and_secret(self):
        """When jwt_secret is set but token is not a JWT, it passes through.

        Non-JWT tokens (plain strings, dotted API keys, semver strings,
        SPIFFE IDs) pass through as raw identities because they fail
        structural JWT decoding.
        """
        gate = _make_gate(jwt_secret=JWT_SECRET)

        # Plain string with no dots
        assert await gate.authenticate("plain-api-key") == "plain-api-key"

        # Dotted API key
        assert await gate.authenticate("my.api.key") == "my.api.key"

        # Semver-like string
        assert await gate.authenticate("1.2.3") == "1.2.3"

        # SPIFFE ID with dots
        assert (
            await gate.authenticate("spiffe://domain/path.to.service")
            == "spiffe://domain/path.to.service"
        )

    async def test_valid_jwt_through_full_pipeline(self):
        """A valid JWT flows through the full __call__ pipeline."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub="pipeline-agent")
        result = await gate(token, "search")
        assert result.allowed is True
        assert result.identity == "pipeline-agent"

    async def test_invalid_jwt_through_full_pipeline_denied(self):
        """An invalid JWT through __call__ returns a denied GateResult."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub="agent-1", secret=WRONG_SECRET)
        result = await gate(token, "search")
        assert result.allowed is False
        assert "JWT verification failed" in result.reason

    async def test_jwt_with_additional_claims(self):
        """Extra claims in the JWT do not interfere; sub is extracted."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(
            sub="agent-42",
            extra_claims={"iss": "forge", "role": "admin", "org": "acme"},
        )
        identity = await gate.authenticate(token)
        assert identity == "agent-42"

    async def test_jwt_warning_logged_without_secret(self, caplog):
        """A warning is logged once when no jwt_secret is configured."""
        gate = _make_gate(jwt_secret=None)
        with caplog.at_level("WARNING", logger="forge.security.middleware"):
            await gate.authenticate("id-1")
            await gate.authenticate("id-2")
        warning_msgs = [r.message for r in caplog.records if "jwt_secret" in r.message]
        # Warning should appear exactly once (not on the second call)
        assert len(warning_msgs) == 1

    async def test_dotted_api_key_passes_through_with_jwt_secret(self):
        """A dotted API key like 'my.api.key' is not a JWT and passes through."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        identity = await gate.authenticate("my.api.key")
        assert identity == "my.api.key"

    async def test_semver_string_passes_through_with_jwt_secret(self):
        """A semver-like string '1.2.3' passes through as raw identity."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        identity = await gate.authenticate("1.2.3")
        assert identity == "1.2.3"

    async def test_spiffe_id_passes_through_with_jwt_secret(self):
        """A SPIFFE ID passes through as raw identity even with jwt_secret."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        spiffe = "spiffe://example.org/ns/prod/sa/agent"
        identity = await gate.authenticate(spiffe)
        assert identity == spiffe

    async def test_valid_jwt_wrong_signature_denied(self):
        """A properly formatted JWT with wrong signature is denied, not passed through."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        token = _make_jwt(sub="attacker", secret=WRONG_SECRET)
        with pytest.raises(JWTAuthenticationError, match="JWT verification failed"):
            await gate.authenticate(token)

    async def test_malformed_base64_jwt_passes_through(self):
        """A string with two dots but invalid base64 passes through as raw identity."""
        gate = _make_gate(jwt_secret=JWT_SECRET)
        identity = await gate.authenticate("not.valid.base64jwt")
        assert identity == "not.valid.base64jwt"
