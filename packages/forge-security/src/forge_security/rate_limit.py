"""In-memory sliding-window rate limiter.

Each identity string (e.g. a SPIFFE ID) has its own window tracked
independently.  The implementation is lock-free for single-threaded
asyncio loops and uses ``time.monotonic`` for clock stability.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    """Outcome of a rate-limit check."""

    allowed: bool
    remaining: int
    reset_after: float  # seconds until the oldest entry expires


class SlidingWindowRateLimiter:
    """Per-identity sliding-window rate limiter.

    Parameters
    ----------
    max_requests:
        Maximum number of requests allowed within *window_seconds*.
    window_seconds:
        Length of the sliding window in seconds.
    """

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0) -> None:
        self._max = max_requests
        self._window = window_seconds
        # identity -> sorted list of monotonic timestamps
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def _prune(self, identity: str, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self._window
        bucket = self._buckets[identity]
        # Find first index >= cutoff
        idx = 0
        while idx < len(bucket) and bucket[idx] < cutoff:
            idx += 1
        if idx:
            del bucket[:idx]

    async def check(self, identity: str) -> RateLimitResult:
        """Check (and record) a request for *identity*.

        Returns a ``RateLimitResult`` indicating whether the request is
        allowed and how many requests remain in the current window.
        """
        now = time.monotonic()
        self._prune(identity, now)
        bucket = self._buckets[identity]

        if len(bucket) >= self._max:
            oldest = bucket[0]
            reset_after = (oldest + self._window) - now
            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_after=max(reset_after, 0.0),
            )

        bucket.append(now)
        remaining = self._max - len(bucket)
        reset_after = (bucket[0] + self._window) - now if bucket else self._window
        return RateLimitResult(
            allowed=True,
            remaining=remaining,
            reset_after=max(reset_after, 0.0),
        )

    async def peek(self, identity: str) -> RateLimitResult:
        """Check remaining quota *without* recording a request."""
        now = time.monotonic()
        self._prune(identity, now)
        bucket = self._buckets[identity]

        used = len(bucket)
        remaining = max(self._max - used, 0)
        allowed = used < self._max
        reset_after = (bucket[0] + self._window) - now if bucket else self._window

        return RateLimitResult(
            allowed=allowed,
            remaining=remaining,
            reset_after=max(reset_after, 0.0),
        )

    def reset(self, identity: str | None = None) -> None:
        """Clear recorded timestamps.

        If *identity* is ``None`` all identities are cleared.
        """
        if identity is None:
            self._buckets.clear()
        else:
            self._buckets.pop(identity, None)
