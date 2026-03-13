"""FastAPI-compatible security middleware.

``SecurityGate`` composes authentication, authorisation, auditing and
rate-limiting into a single callable that can be used as a FastAPI
dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import jwt
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
    """Raised when JWT token verification fails."""


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
        jwt_secret: str | None = None,
    ) -> None:
        self._identity = identity_manager
        self._trust = trust_enforcer
        self._audit = audit_logger
        self._authz = authz_provider
        self._jwt_secret: str | None = jwt_secret
        self._jwt_warning_logged: bool = False

    # -- factory ------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: SecurityConfig,
        authz_provider: AuthorizationProviderProtocol | None = None,
        jwt_secret: str | None = None,
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
            jwt_secret=jwt_secret,
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
        """Resolve and return the verified caller identity string.

        When ``_jwt_secret`` is configured and *caller_id* looks like a
        JWT (contains two dots), the token is decoded and verified using
        HS256.  The ``sub`` claim is returned as the authenticated
        identity.

        When no ``_jwt_secret`` is configured, the method falls through
        to trust-as-is behaviour for dev/testing compatibility and logs
        a warning once.
        """
        # Ensure our own identity is available
        _our_id = await self._identity.get_identity()

        looks_like_jwt = caller_id.count(".") == 2

        if self._jwt_secret is not None and looks_like_jwt:
            return self._verify_jwt(caller_id)

        if self._jwt_secret is None and not self._jwt_warning_logged:
            logger.warning(
                "No jwt_secret configured — caller_id trusted as-is. "
                "Set security.jwt_secret in config for production use."
            )
            self._jwt_warning_logged = True

        return caller_id

    def _verify_jwt(self, token: str) -> str:
        """Decode a JWT token and return the ``sub`` claim.

        Raises ``GateResult`` denial indirectly by re-raising
        ``jwt.PyJWTError`` so the caller can handle it.
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._jwt_secret,  # type: ignore[arg-type]
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            msg = f"JWT verification failed: {exc}"
            raise JWTAuthenticationError(msg) from exc

        sub: str | None = payload.get("sub")
        if not sub:
            msg = "JWT missing required 'sub' claim"
            raise JWTAuthenticationError(msg)

        return sub

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
