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
from urllib.parse import urlsplit

from forge_config.schema import EgressAction, EgressPolicy


def host_matches(host: str, patterns: frozenset[str]) -> bool:
    """Case-insensitive host match against a set of patterns.

    A pattern is an exact host, a ``host:port`` (the port is ignored -- the
    caller passes a bare hostname), or a ``*.suffix`` wildcard that matches
    both the apex (``example.com``) and any subdomain (``a.example.com``).
    """
    if not host:
        return False
    hl = host.lower()
    for p in patterns:
        pl = p.lower()
        if pl.startswith("*."):
            if hl == pl[2:] or hl.endswith(pl[1:]):
                return True
        elif ":" in pl:
            if hl == pl.split(":", 1)[0]:
                return True
        elif hl == pl:
            return True
    return False


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
