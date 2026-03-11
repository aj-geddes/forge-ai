"""Trust-policy enforcement for Forge.

``TrustPolicyEnforcer`` evaluates incoming requests against the
``SecurityConfig`` from *forge-config* (allowed origins, rate limits).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from forge_config.schema import SecurityConfig

from forge_security.rate_limit import RateLimitResult, SlidingWindowRateLimiter


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a trust-policy evaluation."""

    allowed: bool
    reason: str


class TrustPolicyEnforcer:
    """Enforce trust policies defined in ``SecurityConfig``.

    Checked policies:
    1. **Origin allow-list** -- the caller's origin must match at least one
       pattern in ``SecurityConfig.allowed_origins``.
    2. **Rate limit** -- per-identity sliding-window rate limiting according
       to ``SecurityConfig.rate_limit_rpm``.

    Parameters
    ----------
    config:
        The ``SecurityConfig`` instance to read policy values from.
    rate_limiter:
        Optional pre-built rate limiter.  When ``None`` one is created
        automatically from the config.
    """

    def __init__(
        self,
        config: SecurityConfig,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self._config = config
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(
            max_requests=config.rate_limit_rpm,
            window_seconds=60.0,
        )

    # -- public API ---------------------------------------------------------

    async def evaluate(
        self,
        identity: str,
        origin: str | None = None,
    ) -> PolicyDecision:
        """Run all policy checks and return a single decision.

        Parameters
        ----------
        identity:
            The caller's identity string (e.g. SPIFFE ID).
        origin:
            The request origin (e.g. HTTP ``Origin`` header).  ``None``
            means the check is skipped.
        """
        # 1. Origin check
        if origin is not None and not self._origin_allowed(origin):
            return PolicyDecision(
                allowed=False,
                reason=f"Origin '{origin}' not in allowed list",
            )

        # 2. Rate limit
        rl_result: RateLimitResult = await self._rate_limiter.check(identity)
        if not rl_result.allowed:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Rate limit exceeded for '{identity}': "
                    f"retry after {rl_result.reset_after:.1f}s"
                ),
            )

        return PolicyDecision(allowed=True, reason="all policies passed")

    async def check_origin(self, origin: str) -> PolicyDecision:
        """Check only the origin allow-list policy."""
        if self._origin_allowed(origin):
            return PolicyDecision(allowed=True, reason="origin allowed")
        return PolicyDecision(
            allowed=False,
            reason=f"Origin '{origin}' not in allowed list",
        )

    async def check_rate_limit(self, identity: str) -> RateLimitResult:
        """Check (and record) rate limit for *identity*."""
        return await self._rate_limiter.check(identity)

    # -- internals ----------------------------------------------------------

    def _origin_allowed(self, origin: str) -> bool:
        for pattern in self._config.allowed_origins:
            if pattern == "*" or fnmatch.fnmatch(origin, pattern):
                return True
        return False
