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

        When ``_jwt_secret`` is configured, the method attempts to
        decode *caller_id* as a JWT using HS256.  PyJWT's exception
        hierarchy distinguishes three outcomes:

        * **Successful decode** -- the ``sub`` claim is returned as
          the authenticated identity.
        * **Valid JWT structure but verification failed**
          (``InvalidSignatureError``, ``ExpiredSignatureError``, or
          other ``InvalidTokenError`` subclasses that are *not* plain
          ``DecodeError``) -- the token is a real JWT that failed
          validation, so the request is **denied** via
          ``JWTAuthenticationError``.
        * **Not a JWT at all** (``DecodeError``, excluding its
          ``InvalidSignatureError`` subclass) -- the input is not a
          valid JWT (e.g. an API key, semver string, or SPIFFE ID),
          so the method falls through to trust-as-is behaviour.

        When no ``_jwt_secret`` is configured, *caller_id* is trusted
        as-is for dev/testing compatibility and a warning is logged
        once.
        """
        # Ensure our own identity is available
        _our_id = await self._identity.get_identity()

        if self._jwt_secret is not None:
            return self._verify_jwt(caller_id)

        if not self._jwt_warning_logged:
            logger.warning(
                "No jwt_secret configured — caller_id trusted as-is. "
                "Set security.jwt_secret in config for production use."
            )
            self._jwt_warning_logged = True

        return caller_id

    def _verify_jwt(self, token: str) -> str:
        """Attempt to decode *token* as a JWT, falling back to raw identity.

        Uses PyJWT's exception hierarchy to distinguish "not a JWT"
        from "is a JWT but failed verification":

        * ``InvalidSignatureError`` (subclass of ``DecodeError``) is
          caught **first** -- the token has valid JWT structure but
          the signature does not match.  **Denied.**
        * ``ExpiredSignatureError`` / other ``InvalidTokenError`` --
          the token is a real JWT but a claim check failed (expired,
          invalid issuer, etc.).  **Denied.**
        * ``DecodeError`` (excluding ``InvalidSignatureError``) --
          the input is structurally not a JWT (wrong number of
          segments, bad base-64, etc.).  **Fall through** and return
          the raw token as the identity.

        Returns
        -------
        str
            The ``sub`` claim from a valid JWT, or the raw *token*
            string when the input is not a JWT.

        Raises
        ------
        JWTAuthenticationError
            When the token is a structurally valid JWT that fails
            signature or claim verification.
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._jwt_secret,  # type: ignore[arg-type]
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.exceptions.InvalidSignatureError as exc:
            # Valid JWT structure but the signature doesn't match
            # our secret -- this is a real JWT that must be denied.
            # Caught before DecodeError because it is a subclass.
            msg = f"JWT verification failed: {exc}"
            raise JWTAuthenticationError(msg) from exc
        except jwt.exceptions.ExpiredSignatureError as exc:
            # Valid JWT that has expired -- deny.
            msg = f"JWT verification failed: {exc}"
            raise JWTAuthenticationError(msg) from exc
        except jwt.exceptions.DecodeError:
            # Not a valid JWT at all (bad segments, bad base-64,
            # etc.) -- treat the raw caller_id as a plain identity.
            logger.debug(
                "caller_id is not a valid JWT; treating as raw identity",
            )
            return token
        except jwt.exceptions.InvalidTokenError as exc:
            # Any other token-validation failure (invalid issuer,
            # immature signature, etc.) -- the token IS a JWT but
            # fails a claim check.  Deny.
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
