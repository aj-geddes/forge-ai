"""Claims -> roles -> permissions authorization (ADR-0001 SS6). Deny by
default: a principal matching no binding, or holding a role that lacks
the requested permission, has no access.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from forge_config.schema import AuthorizationConfig, Permission

from forge_security.oidc.principal import Principal

logger = logging.getLogger("forge.security.oidc.authorizer")

_PERMISSION_WILDCARD = "*"
_ALL_PERMISSIONS = frozenset(p.value for p in Permission)


class Authorizer:
    """Resolves claims to roles and roles to permissions for a given
    :class:`~forge_config.schema.AuthorizationConfig`."""

    def __init__(self, config: AuthorizationConfig) -> None:
        self._config = config

    def roles_for(
        self,
        *,
        groups: Iterable[str] = (),
        email: str | None = None,
        email_verified: bool = False,
        sub: str | None = None,
    ) -> list[str]:
        """Return the sorted union of roles granted by every binding that
        matches the given claims.

        ``groups``/``subs`` match case-sensitively; ``emails`` match
        case-insensitively (ADR-0001 SS6.3). A principal matching no
        binding falls back to ``default_role`` (``None`` by default --
        deny).

        Security-review finding #2 (HIGH): an ``email`` claim is only used
        to match an email-based binding when *email_verified* is ``True``.
        A GitHub-connector email is user-settable and, absent this check,
        an attacker could set their profile email to an admin's and get
        the admin role bound to it. This defaults fail-safe (``False``):
        the caller must explicitly thread a verified ``email_verified``
        claim through for the email path to grant anything. Missing or
        false ``email_verified`` only disables the *email*-derived role(s)
        -- ``groups``/``sub`` bindings for the same principal are
        unaffected, and the principal still authenticates with whatever
        (possibly empty) role set that leaves it with."""
        group_set = set(groups)
        email_lower = email.lower() if email else None

        matched: set[str] = set()
        unverified_email_match = False
        for binding in self._config.bindings:
            if group_set & set(binding.groups):
                matched.add(binding.role)
                continue
            if email_lower is not None and email_lower in {e.lower() for e in binding.emails}:
                if email_verified:
                    matched.add(binding.role)
                    continue
                # Matched by email, but email_verified is missing/false:
                # do NOT grant this binding's role via email. Fall through
                # to the sub check below in case this same binding also
                # matches by sub -- an unverified email must never be the
                # sole reason a role is withheld from an otherwise-valid
                # sub match.
                unverified_email_match = True
            if sub is not None and sub in binding.subs:
                matched.add(binding.role)
                continue

        if unverified_email_match:
            logger.warning(
                "Skipped email-derived role binding for %r: email_verified "
                "claim is missing or false. An email claim MUST have "
                "email_verified=true to grant email-based roles (sub=%r, "
                "groups=%r). If this is a first login, verify Dex's "
                "connector actually sets email_verified=true.",
                email,
                sub,
                sorted(group_set),
            )

        if not matched and self._config.default_role:
            matched.add(self._config.default_role)

        return sorted(matched)

    def permissions_for_roles(self, roles: Iterable[str]) -> frozenset[str]:
        """Expand *roles* to their union of permissions. The wildcard
        permission ``"*"`` (e.g. the built-in ``admin`` role) expands to
        every permission in the closed set."""
        perms: set[str] = set()
        for role in roles:
            perms.update(self._config.roles.get(role, []))
        if _PERMISSION_WILDCARD in perms:
            return _ALL_PERMISSIONS
        return frozenset(perms)

    @staticmethod
    def has_permission(permissions: frozenset[str], permission: str) -> bool:
        """Return ``True`` if *permission* is present in *permissions*."""
        return permission in permissions

    def has(self, principal: Principal, permission: str) -> bool:
        """Return ``True`` if *principal* holds *permission*."""
        return self.has_permission(principal.permissions, permission)
