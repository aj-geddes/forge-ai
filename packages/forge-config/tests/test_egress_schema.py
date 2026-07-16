"""Tests for the ADR-0006 egress schema additions (additive + default-safe)."""

from __future__ import annotations

from forge_config.schema import (
    AuthConfig,
    AuthType,
    EgressAction,
    EgressPolicy,
    ForgeConfig,
    SecurityConfig,
)


class TestEgressActionEnum:
    def test_egress_action_enum_values(self) -> None:
        assert EgressAction.REJECT.value == "reject"
        assert EgressAction.DROP.value == "drop"

    def test_reject_is_default_fail_closed(self) -> None:
        assert EgressPolicy().default_action == EgressAction.REJECT


class TestEgressPolicyDefaults:
    def test_egress_policy_defaults(self) -> None:
        p = EgressPolicy()
        assert p.enabled is True
        assert p.allowed_hosts == []
        assert p.require_https is True
        assert p.default_action == EgressAction.REJECT

    def test_security_config_has_default_egress(self) -> None:
        assert SecurityConfig().egress == EgressPolicy()


class TestAuthConfigAdditions:
    def test_authconfig_allowed_hosts_optional(self) -> None:
        a = AuthConfig()
        assert a.allowed_hosts == []
        assert a.on_egress_violation == EgressAction.REJECT

    def test_authconfig_accepts_explicit_binding(self) -> None:
        a = AuthConfig(
            type=AuthType.NONE,
            allowed_hosts=["api.example.com", "*.trusted.com"],
            on_egress_violation=EgressAction.DROP,
        )
        assert a.allowed_hosts == ["api.example.com", "*.trusted.com"]
        assert a.on_egress_violation == EgressAction.DROP


class TestExistingConfigUnchanged:
    def test_existing_config_unchanged(self) -> None:
        """A ForgeConfig with no egress/allowed_hosts fields anywhere still
        parses, and the new fields default to their safe values."""
        cfg = ForgeConfig()
        assert cfg.security.egress.enabled is True
        assert cfg.security.egress.default_action == EgressAction.REJECT
        # Round-trips through YAML-style dict without the new keys.
        raw = {
            "metadata": {"name": "legacy"},
            "tools": {
                "manual_tools": [
                    {
                        "name": "t",
                        "description": "d",
                        "api": {"url": "https://api.example.com/x"},
                    }
                ]
            },
        }
        cfg2 = ForgeConfig.model_validate(raw)
        assert cfg2.tools.manual_tools[0].api.auth.allowed_hosts == []
        assert cfg2.security.egress.default_action == EgressAction.REJECT
