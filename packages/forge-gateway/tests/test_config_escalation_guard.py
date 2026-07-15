"""**SECURITY**: tests for forge_gateway.security.check_no_config_escalation
-- the mint_user_token-style anti-escalation guard that runs before any
overlay write in ``apply_overlay_mutation`` (belt-and-suspenders atop the
structural base-only guarantee: the overlay can never carry a
``security``/``authorization`` key at all).
"""

from __future__ import annotations

from forge_config.schema import (
    AuthorizationConfig,
    ForgeConfig,
    RoleBinding,
    SecurityConfig,
)
from forge_gateway.security import ConfigEscalationDeniedError, check_no_config_escalation
from forge_security.oidc.principal import Principal


def _principal(*, roles: list[str], permissions: frozenset[str]) -> Principal:
    return Principal(kind="user", sub="user:alice", roles=roles, permissions=permissions)


def _config_with_roles(roles: dict[str, list[str]]) -> ForgeConfig:
    return ForgeConfig(
        security=SecurityConfig(
            authorization=AuthorizationConfig(
                roles=roles,
                bindings=[RoleBinding(role="viewer", subs=["user:alice"])],
            )
        )
    )


class TestNoEscalationForLegitimateNoOpChange:
    def test_identical_authorization_block_never_raises(self) -> None:
        """The normal case: an overlay-only mutation never touches
        security.authorization, so config_after's authorization block is
        byte-identical to what the principal was authenticated under."""
        principal = _principal(roles=["viewer"], permissions=frozenset({"config:read"}))
        config_after = _config_with_roles({"viewer": ["config:read"]})
        check_no_config_escalation(principal, config_after=config_after)  # must not raise

    def test_narrowing_permissions_never_raises(self) -> None:
        """A config_after that grants FEWER permissions than the caller
        currently holds is always safe."""
        principal = _principal(
            roles=["viewer"], permissions=frozenset({"config:read", "metrics:read"})
        )
        config_after = _config_with_roles({"viewer": ["config:read"]})
        check_no_config_escalation(principal, config_after=config_after)  # must not raise


class TestEscalationIsDenied:
    def test_widened_role_permissions_are_rejected(self) -> None:
        """Simulates the ONLY way this guard could ever fire: some future
        regression that let a config change alter what the caller's
        current roles grant. Proves the guard independently catches an
        escalation attempt even though the primary structural guard
        (OverlayDocument rejecting the 'security'/'authorization' key
        entirely) already prevents this from being reachable via a real
        overlay mutation."""
        principal = _principal(roles=["viewer"], permissions=frozenset({"config:read"}))
        # config_after somehow grants "viewer" the wildcard -- exactly
        # the self-escalation this guard exists to catch.
        config_after = _config_with_roles({"viewer": ["*"]})

        try:
            check_no_config_escalation(principal, config_after=config_after)
        except ConfigEscalationDeniedError as exc:
            assert "config:write" in exc.gained or len(exc.gained) > 0
        else:
            raise AssertionError("expected ConfigEscalationDeniedError to be raised")

    def test_gained_permissions_are_exactly_the_delta(self) -> None:
        principal = _principal(roles=["viewer"], permissions=frozenset({"config:read"}))
        config_after = _config_with_roles({"viewer": ["config:read", "config:write"]})

        raised = False
        try:
            check_no_config_escalation(principal, config_after=config_after)
        except ConfigEscalationDeniedError as exc:
            raised = True
            assert exc.gained == frozenset({"config:write"})
        assert raised

    def test_rejected_before_any_write_is_a_pure_function_no_side_effects(self) -> None:
        """The guard is a pure check -- calling it twice with the same
        (denied) inputs must raise identically both times, proving it has
        no hidden state/side effects that a caller could accidentally
        rely on to "consume" a denial."""
        principal = _principal(roles=["viewer"], permissions=frozenset({"config:read"}))
        config_after = _config_with_roles({"viewer": ["*"]})

        for _ in range(2):
            try:
                check_no_config_escalation(principal, config_after=config_after)
            except ConfigEscalationDeniedError:
                continue
            raise AssertionError("expected ConfigEscalationDeniedError on every call")
