"""OPA authorization for the workload plane (ADR-0004 SS5, SS8).

Builds the ``context`` sub-document of the OPA input contract and drives
the fail-closed authorization check:

::

    decision = await opa.check(caller_id=peer_spiffe_id,
                                resource=my_spiffe_id,
                                action="a2a:task",
                                context={"task_type": ..., "tool": ...,
                                         "peer_trust_level": ...})
    if not decision.allowed:
        raise WorkloadForbidden(...)   # 403, fail closed

``caller_spiffe_id``/``resource_spiffe_id`` and the derived
``caller_trust_domain``/``resource_trust_domain`` (ADR-0004 SS5) are
built by ``OPAProvider`` itself from the ``caller_id``/``resource``
arguments -- this module does not duplicate that derivation.

**Fail closed, twice over**: ``OPAProvider`` is constructed with
``default_deny=True`` (see ``providers.py``), so an unreachable OPA
already comes back as ``allowed=False`` rather than raising. This module
adds a second, independent layer: *any* exception raised by the
authorization provider's ``check`` -- including from a caller-supplied
fake/test provider that raises directly, or a future provider
implementation that doesn't share ``OPAProvider``'s internal
default-deny handling -- is itself treated as a deny, audited as a deny,
and surfaced as :class:`WorkloadForbidden`. There is no path here that
turns a provider failure into an allow.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, Protocol

from forge_security.oidc.principal import Principal
from forge_security.workload.errors import WorkloadForbidden

logger = logging.getLogger("forge.security.workload.authz")

PeerTrustLevel = Literal["high", "medium", "low"]


class AuthzDecisionLike(Protocol):
    """Structural shape of an authorization decision -- satisfied by both
    agentweave's real ``AuthzDecision`` and any test double."""

    allowed: bool
    reason: str


class AuthorizationCheck(Protocol):
    """The one method this module relies on from an authorization
    provider -- satisfied directly by agentweave's ``OPAProvider`` and,
    via ``providers._MockAuthorizationAdapter``, by ``MockAuthorizationProvider``."""

    async def check(
        self,
        caller_id: str,
        resource: str,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> AuthzDecisionLike: ...


class AuditTrailLike(Protocol):
    """The one method this module relies on from an audit trail."""

    async def record_auth_check(
        self,
        caller_id: str,
        action: str,
        resource: str,
        decision: str,
        duration: float,
        reason: str = "",
    ) -> None: ...


def build_authz_context(
    *,
    task_type: str | None = None,
    tool: str | None = None,
    peer_trust_level: PeerTrustLevel | None = None,
) -> dict[str, Any]:
    """Build the OPA ``context`` sub-document (ADR-0004 SS5)."""
    return {"task_type": task_type, "tool": tool, "peer_trust_level": peer_trust_level}


async def authorize_workload(
    principal: Principal,
    action: str,
    resource: str,
    *,
    authz: AuthorizationCheck,
    audit: AuditTrailLike,
    task_type: str | None = None,
    tool: str | None = None,
    peer_trust_level: PeerTrustLevel | None = None,
) -> AuthzDecisionLike:
    """Authorize ``principal`` performing ``action`` on ``resource``.

    Always audits the outcome (``record_auth_check``) -- allow, explicit
    deny, or provider-failure deny alike.

    Raises
    ------
    WorkloadForbidden
        (403) if OPA denies the action, or if the authorization provider
        raises any exception while being queried (fail closed).
    """
    context = build_authz_context(task_type=task_type, tool=tool, peer_trust_level=peer_trust_level)
    start = time.monotonic()

    try:
        decision = await authz.check(principal.sub, resource, action, context)
    except Exception as exc:  # fail closed: any provider failure is a deny, never an allow
        duration = time.monotonic() - start
        logger.error(
            "workload plane: authorization provider raised, denying by default", exc_info=exc
        )
        await audit.record_auth_check(
            caller_id=principal.sub,
            action=action,
            resource=resource,
            decision="deny",
            duration=duration,
            reason=f"authorization provider unavailable: {exc}",
        )
        raise WorkloadForbidden("authorization_provider_unavailable", reason=str(exc)) from exc

    duration = time.monotonic() - start
    await audit.record_auth_check(
        caller_id=principal.sub,
        action=action,
        resource=resource,
        decision="allow" if decision.allowed else "deny",
        duration=duration,
        reason=decision.reason,
    )

    if not decision.allowed:
        raise WorkloadForbidden("opa_denied", reason=decision.reason)

    return decision
