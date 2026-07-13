"""FastAPI-compatible security middleware.

``SecurityGate`` composes authorisation, auditing and rate-limiting into a
single callable that can be used as a FastAPI dependency.

.. deprecated::
   As of ADR-0001 (Dex OIDC authentication), ``SecurityGate`` no longer
   performs *credential verification*. It previously decoded ``caller_id``
   as an HS256 JWT with ``verify_aud=False`` and, on any non-JWT input,
   silently trusted the raw string as an identity -- this was the root
   cause of the ``X-Caller-ID`` authentication bypass described in
   ADR-0001 SS1.2. Both code paths have been deleted.

   Authentication is now the sole responsibility of
   ``forge_security.oidc.resolve_principal``, which produces a
   cryptographically verified ``Principal``. Callers of this class MUST
   only ever pass an already-verified ``Principal.sub`` (or equivalent)
   as ``caller_id`` -- never an unauthenticated request header or query
   parameter. ``SecurityGate`` is retained only for its trust-policy
   (origin/rate-limit), authorization-provider and audit-logging
   composition; new code should prefer ``forge_security.oidc`` directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from forge_config.schema import SecurityConfig

from forge_security.audit import AuditLogger, ToolCallEvent
from forge_security.identity import ForgeIdentityManager
from forge_security.trust import PolicyDecision, TrustPolicyEnforcer

logger = logging.getLogger("forge.security.middleware")

# ---------------------------------------------------------------------------
# Protocols for pluggable authorization
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthorizationProviderProtocol(Protocol):
    """Minimal protocol matching AgentWeave ``AuthorizationProvider.check``."""

    async def check(
        self,
        caller_id: str,
        resource: str,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Return an object with an ``allowed`` boolean attribute."""
        ...


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """Outcome of a ``SecurityGate`` evaluation."""

    allowed: bool
    identity: str
    reason: str
    audit_event: ToolCallEvent | None = None


class JWTAuthenticationError(Exception):
    """Raised when ``SecurityGate.authenticate`` cannot resolve an identity.

    .. deprecated::
       No longer raised for JWT-specific failures (JWT verification has
       been removed from this class -- see the module docstring). Kept as
       a narrow exception type for identity-resolution failures inside
       the trust/authz/audit pipeline.
    """


# ---------------------------------------------------------------------------
# SecurityGate
# ---------------------------------------------------------------------------


class SecurityGate:
    """Compose Forge security checks into a single callable.

    Usage as a FastAPI dependency::

        gate = SecurityGate.from_config(security_config)

        @app.post("/tool/{tool_name}")
        async def call_tool(tool_name: str, result: GateResult = Depends(gate)):
            if not result.allowed:
                raise HTTPException(403, result.reason)
            ...

    Parameters
    ----------
    identity_manager:
        The ``ForgeIdentityManager`` used to authenticate callers.
    trust_enforcer:
        The ``TrustPolicyEnforcer`` used to check origin and rate-limit
        policies.
    audit_logger:
        The ``AuditLogger`` for recording security events.
    authz_provider:
        Optional authorization back-end (e.g. AgentWeave OPA provider).
    """

    def __init__(
        self,
        identity_manager: ForgeIdentityManager,
        trust_enforcer: TrustPolicyEnforcer,
        audit_logger: AuditLogger,
        authz_provider: AuthorizationProviderProtocol | None = None,
    ) -> None:
        self._identity = identity_manager
        self._trust = trust_enforcer
        self._audit = audit_logger
        self._authz = authz_provider

    # -- factory ------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: SecurityConfig,
        authz_provider: AuthorizationProviderProtocol | None = None,
    ) -> SecurityGate:
        """Build a ``SecurityGate`` from a ``SecurityConfig``."""
        identity_mgr = ForgeIdentityManager(
            trust_domain=config.agentweave.trust_domain,
            agent_name="forge-gateway",
        )
        trust_enforcer = TrustPolicyEnforcer(config)
        audit_logger = AuditLogger()
        return cls(
            identity_manager=identity_mgr,
            trust_enforcer=trust_enforcer,
            audit_logger=audit_logger,
            authz_provider=authz_provider,
        )

    # -- main entry point ---------------------------------------------------

    async def __call__(
        self,
        caller_id: str,
        tool_name: str,
        *,
        origin: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> GateResult:
        """Run the full security pipeline.

        Steps
        -----
        1. **Authenticate** -- resolve the caller identity.
        2. **Trust policy** -- check origin allow-list and rate limit.
        3. **Authorize** -- (optional) check fine-grained authorization.
        4. **Audit** -- log the decision.

        Returns a ``GateResult`` summarising the outcome.
        """
        # 1. Authenticate (JWT verification when configured)
        try:
            identity = await self.authenticate(caller_id)
        except JWTAuthenticationError as exc:
            event = await self._audit.log_tool_call(
                caller_id=caller_id,
                tool_name=tool_name,
                allowed=False,
                reason=str(exc),
            )
            return GateResult(
                allowed=False,
                identity=caller_id,
                reason=str(exc),
                audit_event=event,
            )

        # 2. Trust policy (origin + rate limit)
        policy: PolicyDecision = await self._trust.evaluate(
            identity=identity,
            origin=origin,
        )
        if not policy.allowed:
            event = await self._audit.log_tool_call(
                caller_id=identity,
                tool_name=tool_name,
                allowed=False,
                reason=policy.reason,
            )
            return GateResult(
                allowed=False,
                identity=identity,
                reason=policy.reason,
                audit_event=event,
            )

        # 3. Authorize (optional)
        if self._authz is not None:
            authz_result = await self.authorize_tool_call(identity, tool_name, context=context)
            if not authz_result.allowed:
                event = await self._audit.log_tool_call(
                    caller_id=identity,
                    tool_name=tool_name,
                    allowed=False,
                    reason=authz_result.reason,
                )
                return GateResult(
                    allowed=False,
                    identity=identity,
                    reason=authz_result.reason,
                    audit_event=event,
                )

        # 4. Audit success
        event = await self._audit.log_tool_call(
            caller_id=identity,
            tool_name=tool_name,
            allowed=True,
            reason="all checks passed",
        )

        return GateResult(
            allowed=True,
            identity=identity,
            reason="all checks passed",
            audit_event=event,
        )

    # -- individual checks --------------------------------------------------

    async def authenticate(self, caller_id: str) -> str:
        """Return the already-verified caller identity.

        This method performs **no credential verification**. It exists
        only to touch the identity manager (ensuring this gate's own
        identity is available) and to give the ``__call__`` pipeline a
        single, overridable seam. *caller_id* MUST already be a
        cryptographically verified identity -- e.g. a ``Principal.sub``
        produced by ``forge_security.oidc.resolve_principal`` -- never an
        unauthenticated request header or query parameter. Feeding
        unauthenticated input here reintroduces the ADR-0001 SS1.2
        ``X-Caller-ID`` bypass.
        """
        # Ensure our own identity is available.
        await self._identity.get_identity()
        return caller_id

    async def authorize_tool_call(
        self,
        caller_id: str,
        tool_name: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Delegate to the authorization provider."""
        if self._authz is None:
            return PolicyDecision(allowed=True, reason="no authz provider configured")

        decision = await self._authz.check(
            caller_id=caller_id,
            resource=tool_name,
            action="tool_call",
            context=context,
        )
        return PolicyDecision(
            allowed=decision.allowed,
            reason=getattr(decision, "reason", ""),
        )

    async def audit_tool_call(
        self,
        caller_id: str,
        tool_name: str,
        *,
        allowed: bool = True,
        reason: str = "",
    ) -> ToolCallEvent:
        """Record a tool-call audit event."""
        return await self._audit.log_tool_call(
            caller_id=caller_id,
            tool_name=tool_name,
            allowed=allowed,
            reason=reason,
        )

    async def check_rate_limit(self, identity: str) -> bool:
        """Return ``True`` if the identity is within rate limits."""
        result = await self._trust.check_rate_limit(identity)
        return result.allowed
