"""Tests for forge_security.oidc.jwks.JwksCache (ADR-0001 SS8.1)."""

from __future__ import annotations

import httpx
import pytest
from _oidc_fixtures import RSAKeyPair, jwks_document
from forge_security.oidc.errors import AuthError
from forge_security.oidc.jwks import JwksCache

JWKS_URI = "https://dex.hvslocal/dex/keys"


class _FakeClock:
    """A controllable monotonic clock for deterministic TTL/rate-limit tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _CountingHandler:
    """An httpx MockTransport handler that serves a fixed, ordered sequence
    of responses, one per request, and counts how many requests were made."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        if not self._responses:
            msg = (
                f"_CountingHandler exhausted its scripted responses on call "
                f"#{self.call_count} -- the test expected fewer JWKS fetches "
                f"than the cache actually performed."
            )
            raise AssertionError(msg)
        return self._responses.pop(0)


def _ok_response(*keypairs: RSAKeyPair) -> httpx.Response:
    return httpx.Response(200, json=jwks_document(*keypairs))


def _error_response() -> httpx.Response:
    return httpx.Response(503, text="upstream unavailable")


def _make_cache(
    handler: _CountingHandler,
    *,
    clock: _FakeClock,
    cache_ttl_seconds: float = 300,
    min_refresh_seconds: float = 30,
    stale_grace_seconds: float = 86400,
) -> JwksCache:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JwksCache(
        http_client=client,
        jwks_uri=JWKS_URI,
        cache_ttl_seconds=cache_ttl_seconds,
        min_refresh_seconds=min_refresh_seconds,
        stale_grace_seconds=stale_grace_seconds,
        clock=clock,
    )


class TestFetchAndCache:
    async def test_fetches_and_caches_keys(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp)])
        cache = _make_cache(handler, clock=clock)

        key = await cache.get_key("kid-1")

        assert key is not None
        assert handler.call_count == 1

    async def test_serves_from_cache_within_ttl(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp)])
        cache = _make_cache(handler, clock=clock, cache_ttl_seconds=300)

        await cache.get_key("kid-1")
        clock.advance(100)  # still within TTL
        await cache.get_key("kid-1")

        assert handler.call_count == 1


class TestTTLExpiry:
    async def test_refetches_after_ttl_expiry(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp), _ok_response(kp)])
        cache = _make_cache(handler, clock=clock, cache_ttl_seconds=300)

        await cache.get_key("kid-1")
        clock.advance(301)  # past TTL
        await cache.get_key("kid-1")

        assert handler.call_count == 2


class TestKidMissRotation:
    async def test_kid_miss_triggers_forced_refresh_and_then_resolves(self):
        old_kp = RSAKeyPair.generate("kid-old")
        new_kp = RSAKeyPair.generate("kid-new")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(old_kp), _ok_response(old_kp, new_kp)])
        cache = _make_cache(handler, clock=clock, min_refresh_seconds=30)

        await cache.get_key("kid-old")  # warms cache with only kid-old (a forced fetch)
        clock.advance(31)  # past the forced-refresh rate-limit floor
        key = await cache.get_key("kid-new")  # miss -> forced refresh -> found

        assert key is not None
        assert handler.call_count == 2

    async def test_kid_miss_refresh_is_rate_limited_by_min_refresh_seconds(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp)])
        cache = _make_cache(handler, clock=clock, min_refresh_seconds=30)

        with pytest.raises(AuthError) as exc1:
            await cache.get_key("nonexistent-kid")
        assert exc1.value.status == 401
        assert exc1.value.code == "unknown_key"
        assert handler.call_count == 1  # the forced refresh happened once

        clock.advance(5)  # still within min_refresh_seconds
        with pytest.raises(AuthError) as exc2:
            await cache.get_key("nonexistent-kid")
        assert exc2.value.code == "unknown_key"
        assert handler.call_count == 1  # no second fetch -- rate limited

    async def test_unknown_kid_after_refresh_raises_auth_error_401_unknown_key(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp)])
        cache = _make_cache(handler, clock=clock)

        with pytest.raises(AuthError) as exc:
            await cache.get_key("totally-unknown")

        assert exc.value.status == 401
        assert exc.value.code == "unknown_key"


class TestStaleIfError:
    async def test_fetch_failure_with_warm_cache_serves_stale_keys(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp), _error_response()])
        cache = _make_cache(handler, clock=clock, cache_ttl_seconds=300, stale_grace_seconds=86400)

        await cache.get_key("kid-1")  # warm the cache
        clock.advance(301)  # TTL expired -> lazy refresh attempted
        key = await cache.get_key("kid-1")  # refresh fails, but within stale grace

        assert key is not None
        assert cache.is_stale is True

    async def test_fetch_failure_past_stale_grace_raises_503(self):
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp), _error_response()])
        cache = _make_cache(handler, clock=clock, cache_ttl_seconds=300, stale_grace_seconds=3600)

        await cache.get_key("kid-1")
        clock.advance(300 + 3600 + 1)  # past both TTL and stale grace

        with pytest.raises(AuthError) as exc:
            await cache.get_key("kid-1")

        assert exc.value.status == 503
        assert exc.value.code == "identity_provider_unavailable"


class TestColdCacheFailure:
    async def test_cold_cache_fetch_failure_raises_503_not_401(self):
        clock = _FakeClock()
        handler = _CountingHandler([_error_response()])
        cache = _make_cache(handler, clock=clock)

        with pytest.raises(AuthError) as exc:
            await cache.get_key("any-kid")

        assert exc.value.status == 503
        assert exc.value.code == "identity_provider_unavailable"


class TestRateLimitedSkipEdgeCases:
    async def test_rate_limited_skip_still_resolves_a_kid_that_is_actually_cached(self):
        """A forced-refresh request that is rate-limited-skipped must still
        succeed if the requested kid was already present in the cache
        (e.g. a second concurrent caller asking for the same, now-known
        kid before its own forced refresh could run)."""
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp)])
        cache = _make_cache(handler, clock=clock, min_refresh_seconds=30)

        # Warm the cache and immediately exhaust the forced-refresh budget
        # with a genuine miss.
        with pytest.raises(AuthError):
            await cache.get_key("nonexistent-kid")

        # Still within the rate-limit window, but this kid IS cached.
        key = await cache.get_key("kid-1")
        assert key is not None
        assert handler.call_count == 1

    async def test_stale_grace_serves_known_kid_but_still_misses_unknown_kid(self):
        """Within stale grace, a genuinely unknown kid still raises
        unknown_key rather than being served from a cache that never had
        it."""
        kp = RSAKeyPair.generate("kid-1")
        clock = _FakeClock()
        handler = _CountingHandler([_ok_response(kp), _error_response()])
        cache = _make_cache(handler, clock=clock, cache_ttl_seconds=300, stale_grace_seconds=86400)

        await cache.get_key("kid-1")
        clock.advance(301)  # TTL expired -> lazy refresh -> fails -> stale grace

        with pytest.raises(AuthError) as exc:
            await cache.get_key("never-existed")

        assert exc.value.status == 401
        assert exc.value.code == "unknown_key"
