"""Forge Security - AgentWeave integration wrapper for Forge AI."""

from forge_security.audit import AuditAction, AuditLogger, ToolCallEvent
from forge_security.identity import (
    ForgeIdentityManager,
    ForgeKeypair,
    MockIdentityProvider,
)
from forge_security.middleware import (
    GateResult,
    JWTAuthenticationError,
    SecurityGate,
)
from forge_security.rate_limit import RateLimitResult, SlidingWindowRateLimiter
from forge_security.secrets import ForgeCompositeSecretResolver, K8sSecretResolver
from forge_security.signing import MessageSigner, SignedMessage
from forge_security.trust import PolicyDecision, TrustPolicyEnforcer

__all__ = [
    # Identity
    "ForgeIdentityManager",
    "ForgeKeypair",
    "MockIdentityProvider",
    # Signing
    "MessageSigner",
    "SignedMessage",
    # Audit
    "AuditLogger",
    "AuditAction",
    "ToolCallEvent",
    # Secrets
    "K8sSecretResolver",
    "ForgeCompositeSecretResolver",
    # Rate limiting
    "SlidingWindowRateLimiter",
    "RateLimitResult",
    # Trust policy
    "TrustPolicyEnforcer",
    "PolicyDecision",
    # Middleware
    "SecurityGate",
    "GateResult",
    "JWTAuthenticationError",
]
