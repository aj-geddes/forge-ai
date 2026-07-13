"""Tests for forge_security.oidc.authorizer.Authorizer (ADR-0001 SS6)."""

from __future__ import annotations

import logging

from forge_config.schema import AuthorizationConfig, RoleBinding
from forge_security.oidc.authorizer import Authorizer


def _make_authorizer(**overrides: object) -> Authorizer:
    config = AuthorizationConfig(**overrides)  # type: ignore[arg-type]
    return Authorizer(config)


class TestGroupBinding:
    def test_group_binding_grants_role(self):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="user", groups=["hvs-platform:engineers"])],
        )

        roles = authorizer.roles_for(groups=["hvs-platform:engineers"])

        assert roles == ["user"]


class TestEmailBinding:
    def test_email_binding_is_case_insensitive(self):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="admin", emails=["Ageddes75@Gmail.com"])],
        )

        roles = authorizer.roles_for(email="ageddes75@gmail.com", email_verified=True)

        assert roles == ["admin"]

    def test_group_binding_is_case_sensitive(self):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="user", groups=["hvs-platform:engineers"])],
        )

        roles = authorizer.roles_for(groups=["HVS-PLATFORM:ENGINEERS"])

        assert roles == []


class TestMultipleBindings:
    def test_multiple_matching_bindings_union_roles(self):
        authorizer = _make_authorizer(
            bindings=[
                RoleBinding(role="admin", emails=["a@b.com"]),
                RoleBinding(role="user", groups=["engineers"]),
            ],
        )

        roles = authorizer.roles_for(email="a@b.com", email_verified=True, groups=["engineers"])

        assert set(roles) == {"admin", "user"}


class TestDenyByDefault:
    def test_no_matching_binding_yields_zero_permissions(self):
        authorizer = _make_authorizer(bindings=[])

        roles = authorizer.roles_for(
            email="stranger@example.com", email_verified=True, groups=["nobody"]
        )
        permissions = authorizer.permissions_for_roles(roles)

        assert roles == []
        assert permissions == frozenset()

    def test_default_role_opts_in_to_trusting_everyone(self):
        authorizer = _make_authorizer(bindings=[], default_role="viewer")

        roles = authorizer.roles_for(email="stranger@example.com")

        assert roles == ["viewer"]


class TestEmailVerifiedGating:
    """Security-review finding #2 (HIGH): an email claim must only be used
    to match an email-based role binding when the token's
    ``email_verified`` claim is True. A missing/false ``email_verified``
    must NOT grant an email-derived role -- the principal still
    authenticates (empty roles is not an error here), it just gets no
    email-derived roles. Group/sub bindings are unaffected."""

    def test_email_verified_true_grants_email_role(self):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="admin", emails=["ageddes75@gmail.com"])],
        )

        roles = authorizer.roles_for(email="ageddes75@gmail.com", email_verified=True)

        assert roles == ["admin"]

    def test_email_verified_false_does_not_grant_email_role(self):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="admin", emails=["ageddes75@gmail.com"])],
        )

        roles = authorizer.roles_for(email="ageddes75@gmail.com", email_verified=False)

        assert roles == []

    def test_email_verified_absent_does_not_grant_email_role_and_logs_warning(self, caplog):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="admin", emails=["ageddes75@gmail.com"])],
        )

        with caplog.at_level(logging.WARNING, logger="forge.security.oidc.authorizer"):
            roles = authorizer.roles_for(email="ageddes75@gmail.com")

        assert roles == []
        assert any(
            record.levelno == logging.WARNING and "email_verified" in record.getMessage()
            for record in caplog.records
        )

    def test_groups_binding_unaffected_by_missing_email_verified(self):
        """A sub/groups binding must still work even when email_verified is
        absent/false -- only the email-derived path is gated."""
        authorizer = _make_authorizer(
            bindings=[
                RoleBinding(role="admin", emails=["ageddes75@gmail.com"]),
                RoleBinding(role="user", groups=["hvs-platform:engineers"]),
            ],
        )

        roles = authorizer.roles_for(
            email="ageddes75@gmail.com",
            email_verified=False,
            groups=["hvs-platform:engineers"],
        )

        assert roles == ["user"]

    def test_sub_binding_unaffected_by_missing_email_verified(self):
        authorizer = _make_authorizer(
            bindings=[
                RoleBinding(role="admin", emails=["ageddes75@gmail.com"]),
                RoleBinding(role="user", subs=["CgdhamdlZGRlcxIGZ2l0aHVi"]),
            ],
        )

        roles = authorizer.roles_for(
            email="ageddes75@gmail.com",
            email_verified=False,
            sub="CgdhamdlZGRlcxIGZ2l0aHVi",
        )

        assert roles == ["user"]

    def test_email_verified_defaults_to_false_when_omitted(self):
        """The keyword defaults fail-safe: omitting email_verified entirely
        must behave identically to passing False."""
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="admin", emails=["ageddes75@gmail.com"])],
        )

        roles = authorizer.roles_for(email="ageddes75@gmail.com")

        assert roles == []


class TestWildcardRole:
    def test_admin_wildcard_grants_all_permissions(self):
        authorizer = _make_authorizer()  # built-in default roles include admin: ["*"]

        permissions = authorizer.permissions_for_roles(["admin"])

        assert "config:write" in permissions
        assert "agent:invoke" in permissions
        assert "tools:invoke" in permissions


class TestRoleLackingPermission:
    def test_role_lacking_permission_denies(self):
        authorizer = _make_authorizer()
        permissions = authorizer.permissions_for_roles(["viewer"])

        assert authorizer.has_permission(permissions, "agent:invoke") is False
        assert authorizer.has_permission(permissions, "config:read") is True


class TestSubBinding:
    def test_sub_binding_grants_role(self):
        authorizer = _make_authorizer(
            bindings=[RoleBinding(role="admin", subs=["CgdhamdlZGRlcxIGZ2l0aHVi"])],
        )

        roles = authorizer.roles_for(sub="CgdhamdlZGRlcxIGZ2l0aHVi")

        assert roles == ["admin"]


class TestHasConvenienceMethod:
    def test_has_reflects_principal_permissions(self):
        from forge_security.oidc.principal import Principal

        authorizer = _make_authorizer()
        principal = Principal(
            kind="user",
            sub="s",
            roles=["admin"],
            permissions=authorizer.permissions_for_roles(["admin"]),
        )

        assert authorizer.has(principal, "config:write") is True

        stranger = Principal(kind="user", sub="s2", roles=[], permissions=frozenset())
        assert authorizer.has(stranger, "config:write") is False
