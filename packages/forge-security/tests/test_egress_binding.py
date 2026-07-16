"""Tests for the secret->destination binding plane (ADR-0006)."""

from __future__ import annotations

import pytest
from forge_config.schema import EgressAction, EgressPolicy
from forge_security.egress.binding import (
    BoundCredential,
    EgressViolationError,
    enforce_binding,
    host_matches,
)


class TestHostMatches:
    @pytest.mark.parametrize(
        ("host", "patterns", "expected"),
        [
            ("api.example.com", frozenset({"api.example.com"}), True),
            ("API.example.com", frozenset({"api.example.com"}), True),  # case-insensitive
            ("api.example.com", frozenset({"api.example.com:443"}), True),  # port ignored
            ("a.trusted.com", frozenset({"*.trusted.com"}), True),  # subdomain
            ("trusted.com", frozenset({"*.trusted.com"}), True),  # apex
            ("evil.com", frozenset({"api.example.com", "*.trusted.com"}), False),
            ("", frozenset({"api.example.com"}), False),
            ("api.example.com", frozenset(), False),
        ],
    )
    def test_host_matches_wildcard_and_port(
        self, host: str, patterns: frozenset[str], expected: bool
    ) -> None:
        assert host_matches(host, patterns) is expected


def _cred(action: EgressAction = EgressAction.REJECT) -> BoundCredential:
    return BoundCredential(
        headers={"Authorization": "Bearer secret"},
        allowed_hosts=frozenset({"api.example.com"}),
        action=action,
    )


class TestEnforceBindingCredential:
    def test_matching_host_returns_headers(self) -> None:
        out = enforce_binding("https://api.example.com/x", _cred(), policy=EgressPolicy())
        assert out == {"Authorization": "Bearer secret"}

    def test_enforce_binding_reject_raises(self) -> None:
        with pytest.raises(EgressViolationError) as exc:
            enforce_binding(
                "https://evil.example/x", _cred(EgressAction.REJECT), policy=EgressPolicy()
            )
        assert exc.value.host == "evil.example"

    def test_enforce_binding_drop_strips_headers(self) -> None:
        out = enforce_binding(
            "https://evil.example/x", _cred(EgressAction.DROP), policy=EgressPolicy()
        )
        assert out == {}

    def test_none_credential_attaches_nothing(self) -> None:
        out = enforce_binding(
            "https://anywhere.example/x", BoundCredential.none(), policy=EgressPolicy()
        )
        assert out == {}


class TestEnforceBindingGlobalPolicy:
    def test_require_https_reject_raises(self) -> None:
        policy = EgressPolicy(require_https=True, default_action=EgressAction.REJECT)
        with pytest.raises(EgressViolationError):
            enforce_binding("http://api.example.com/x", _cred(), policy=policy)

    def test_require_https_drop_strips(self) -> None:
        policy = EgressPolicy(require_https=True, default_action=EgressAction.DROP)
        out = enforce_binding("http://api.example.com/x", _cred(), policy=policy)
        assert out == {}

    def test_global_allowlist_reject(self) -> None:
        policy = EgressPolicy(
            allowed_hosts=["only.example.com"], default_action=EgressAction.REJECT
        )
        # host is public and matches the credential, but not the GLOBAL allowlist.
        with pytest.raises(EgressViolationError):
            enforce_binding("https://api.example.com/x", _cred(), policy=policy)

    def test_disabled_policy_skips_global_gate(self) -> None:
        policy = EgressPolicy(enabled=False, require_https=True)
        out = enforce_binding("http://api.example.com/x", _cred(), policy=policy)
        # global gate skipped; credential host matches -> headers returned.
        assert out == {"Authorization": "Bearer secret"}

    def test_global_intersects_never_widens(self) -> None:
        # Credential bound narrowly to api.example.com; a broad global allowlist
        # does NOT let the credential travel to a different host.
        policy = EgressPolicy(allowed_hosts=["*.example.com"])
        with pytest.raises(EgressViolationError):
            enforce_binding("https://other.example.com/x", _cred(), policy=policy)
