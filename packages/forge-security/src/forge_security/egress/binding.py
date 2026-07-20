"""Secret -> destination binding (ADR-0006).

The *binding* plane answers a different question from the transport plane.
The transport (``GuardedBackend``) answers "is this address safe to reach at
all" -- it stops SSRF to internal/metadata addresses. The binding plane
answers "may THIS credential attach to THIS validated destination" -- it stops
a valid PUBLIC host receiving a credential bound to a DIFFERENT public host
(confused-deputy / credential-exfiltration). A request to an attacker's public
host passes the transport guard but must fail the binding.

A ``BoundCredential`` pairs the resolved auth headers with the set of hosts
they may travel to. ``enforce_binding`` is called with the FINAL, fully
resolved URL (after every template/param/path substitution) and returns the
headers that may actually be attached -- raising (REJECT) or stripping the
credential (DROP) when the destination is out of bounds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from forge_config.schema import EgressAction, EgressPolicy

from forge_security.egress.classify import host_matches

#: Re-exported so ``allowed_hosts`` has exactly one matcher: the binding and
#: transport planes and the first-line classifier all resolve to this symbol.
__all__ = [
    "BoundCredential",
    "EgressViolationError",
    "credential_binding_from_raw_auth",
    "enforce_binding",
    "host_matches",
]

#: The ``auth.type`` value meaning "this config carries no credential".
_AUTH_TYPE_NONE = "none"


def credential_binding_from_raw_auth(
    auth: Any, *, declared_host: str | None
) -> tuple[bool, frozenset[str]]:
    """THE single implementation of the credential-binding rule (ADR-0006):
    "does this auth config carry a credential, and which hosts is it bound to".

    Two gates ask this question about the same invariant and must never
    diverge: the WRITE-time overlay gate in ``forge_gateway.routes.admin``
    (which sees RAW BASE yaml dicts) and the CONNECT-time
    ``forge_agent.builder.openapi.resolve_bound_credential`` (which sees a
    typed ``AuthConfig``). Both delegate here.

    The rule: an ``auth.type`` of ``none`` (or an absent/unparseable auth
    block) carries no credential. Otherwise the credential is bound to
    ``allowed_hosts`` when that is non-empty, else pinned to *declared_host*
    -- the BASE-declared destination ("pin to where you were pointed"), so
    every pre-existing config is safe with no rewrite.

    Args:
        auth: The raw auth mapping (anything else is treated as "no auth").
        declared_host: Host of the BASE-declared destination URL, if any.

    Returns:
        ``(has_credential, bound_hosts)``, with every host lowercased.
    """
    if not isinstance(auth, Mapping):
        return (False, frozenset())
    if (auth.get("type") or _AUTH_TYPE_NONE) == _AUTH_TYPE_NONE:
        return (False, frozenset())
    allowed = auth.get("allowed_hosts")
    if isinstance(allowed, list) and allowed:
        return (True, frozenset(str(h).lower() for h in allowed))
    if declared_host:
        return (True, frozenset({declared_host.lower()}))
    return (True, frozenset())


@dataclass(frozen=True)
class BoundCredential:
    """Resolved auth headers plus the hosts they are permitted to reach."""

    headers: Mapping[str, str]
    allowed_hosts: frozenset[str]
    action: EgressAction

    def header_keys(self) -> frozenset[str]:
        """The set of header names this credential contributes."""
        return frozenset(self.headers.keys())

    @classmethod
    def none(cls) -> BoundCredential:
        """A no-credential binding (auth type NONE / no resolver)."""
        return cls(headers={}, allowed_hosts=frozenset(), action=EgressAction.REJECT)


class EgressViolationError(Exception):
    """Raised BEFORE a request is sent when a bound credential's destination
    host is not permitted and the effective action is ``REJECT``."""

    def __init__(
        self,
        message: str,
        *,
        host: str | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.host = host
        self.allowed_hosts = allowed_hosts


def enforce_binding(
    final_url: str, cred: BoundCredential, *, policy: EgressPolicy
) -> dict[str, str]:
    """Return the auth headers that may be attached to a request to *final_url*.

    Applies, in order: the global ``policy`` gate (require_https + allow-list),
    then the per-credential binding. On a ``REJECT`` violation raises
    ``EgressViolationError``; on a ``DROP`` violation returns the headers minus
    the credential's own keys (i.e. sends the request without the credential).
    Otherwise returns the credential headers unchanged. Callers layer any
    non-credential config headers on top of the returned dict afterward.
    """
    parsed = urlsplit(final_url)
    scheme = parsed.scheme
    host = parsed.hostname or ""

    # Global policy gate (only when the policy is enabled).
    if policy.enabled:
        if policy.require_https and scheme != "https":
            if policy.default_action == EgressAction.REJECT:
                raise EgressViolationError(
                    f"require_https: refusing non-https destination {final_url!r}",
                    host=host,
                )
            return {}
        if policy.allowed_hosts and not host_matches(host, frozenset(policy.allowed_hosts)):
            if policy.default_action == EgressAction.REJECT:
                raise EgressViolationError(
                    f"host {host!r} not in egress allow-list",
                    host=host,
                    allowed_hosts=frozenset(policy.allowed_hosts),
                )
            return {}

    # Per-credential binding gate.
    if cred.allowed_hosts and not host_matches(host, cred.allowed_hosts):
        if cred.action == EgressAction.REJECT:
            raise EgressViolationError(
                f"credential bound to {sorted(cred.allowed_hosts)} "
                f"may not be sent to host {host!r}",
                host=host,
                allowed_hosts=cred.allowed_hosts,
            )
        # DROP: strip the credential's own headers (send without the secret).
        dropped = cred.header_keys()
        return {k: v for k, v in dict(cred.headers).items() if k not in dropped}

    return dict(cred.headers)
