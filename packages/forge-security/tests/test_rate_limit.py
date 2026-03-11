"""Tests for forge_security.rate_limit."""

import time

from forge_security.rate_limit import SlidingWindowRateLimiter


class TestSlidingWindowRateLimiter:
    async def test_allows_within_limit(self):
        rl = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(5):
            result = await rl.check("agent-1")
            assert result.allowed is True

    async def test_denies_over_limit(self):
        rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            await rl.check("agent-1")
        result = await rl.check("agent-1")
        assert result.allowed is False
        assert result.remaining == 0

    async def test_remaining_decreases(self):
        rl = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
        r1 = await rl.check("a")
        assert r1.remaining == 4
        r2 = await rl.check("a")
        assert r2.remaining == 3

    async def test_independent_identities(self):
        rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)
        await rl.check("alice")
        await rl.check("alice")
        # alice is exhausted
        assert (await rl.check("alice")).allowed is False
        # bob should still be fine
        assert (await rl.check("bob")).allowed is True

    async def test_reset_single_identity(self):
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
        await rl.check("x")
        assert (await rl.check("x")).allowed is False
        rl.reset("x")
        assert (await rl.check("x")).allowed is True

    async def test_reset_all(self):
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
        await rl.check("a")
        await rl.check("b")
        rl.reset()
        assert (await rl.check("a")).allowed is True
        assert (await rl.check("b")).allowed is True

    async def test_peek_does_not_consume(self):
        rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)
        peek1 = await rl.peek("agent")
        assert peek1.remaining == 2
        peek2 = await rl.peek("agent")
        assert peek2.remaining == 2  # unchanged

    async def test_reset_after_is_positive(self):
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=10.0)
        await rl.check("z")
        result = await rl.check("z")
        assert result.reset_after > 0
        assert result.reset_after <= 10.0

    async def test_window_expiry(self):
        """Requests outside the window should not count."""
        rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.05)
        await rl.check("t")
        await rl.check("t")
        # wait for window to expire
        time.sleep(0.06)
        result = await rl.check("t")
        assert result.allowed is True
