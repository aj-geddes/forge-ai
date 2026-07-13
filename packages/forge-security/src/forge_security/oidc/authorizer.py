"""Claims -> roles -> permissions authorization (ADR-0001 SS6). Deny by
default: a principal matching no binding, or holding a role that lacks
the requested permission, has no access.
"""

from __future__ import annotations

from collections.abc import Iterable

from forge_config.schema import AuthorizationConfig, Permission

from forge_security.oidc.principal import Principal

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
        sub: str | None = None,
    ) -> list[str]:
        """Return the sorted union of roles granted by every binding that
        matches the given claims.

        ``groups``/``subs`` match case-sensitively; ``emails`` match
        case-insensitively (ADR-0001 SS6.3). A principal matching no
        binding falls back to ``default_role`` (``None`` by default --
        deny)."""
        group_set = set(groups)
        email_lower = email.lower() if email else None

        matched: set[str] = set()
        for binding in self._config.bindings:
            if group_set & set(binding.groups):
                matched.add(binding.role)
                continue
            if email_lower is not None and email_lower in {e.lower() for e in binding.emails}:
                matched.add(binding.role)
                continue
            if sub is not None and sub in binding.subs:
                matched.add(binding.role)
                continue

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
