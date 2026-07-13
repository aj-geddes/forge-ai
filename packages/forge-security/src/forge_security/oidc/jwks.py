"""Async JWKS cache with rate-limited refresh and stale-if-error grace.

PyJWT's ``PyJWKClient`` is deliberately not used here: it fetches keys with
blocking ``urllib`` calls (which would stall the event loop from inside an
async request handler) and its ``cache_keys=True`` LRU has no expiry, so it
can serve revoked keys indefinitely (see ADR-0001 SS8.1 and
https://github.com/jpadilla/pyjwt/issues/1051). This cache owns exactly the
async, TTL-bounded, rate-limited behaviour Forge's failure model needs; the
JWS/claims verification itself remains PyJWT's well-reviewed code (see
``verifier.py``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt

from forge_security.oidc.errors import AuthError

logger = logging.getLogger("forge.security.oidc.jwks")

# ADR-0001 SS7.3: infrastructure failure is always 503, never 401.
_ERROR_IDP_UNAVAILABLE = "identity_provider_unavailable"
_ERROR_UNKNOWN_KEY = "unknown_key"


class JwksCache:
    """Caches a Dex JWKS document and resolves ``kid`` -> verification key.

    Behaviour (ADR-0001 SS8.1):

    * Serves from cache while within ``cache_ttl_seconds`` of the last
      successful fetch.
    * On TTL expiry, lazily refreshes on the next lookup (not rate-limited
      -- this cadence is bounded by the TTL itself, not attacker-triggerable).
    * On a ``kid`` miss (including a cold cache), forces **one** refresh,
      subject to a ``min_refresh_seconds`` floor between forced refreshes --
      without this floor, an unauthenticated attacker sending tokens with
      random ``kid``s could hammer the identity provider (a trivial
      amplification DoS).
    * If a refresh fails but the last successful fetch is within
      ``stale_grace_seconds``, continues serving the stale (but still
      cryptographically valid) cached keys -- RSA public keys do not
      become invalid because our network blipped.
    * If a refresh fails and the cache is cold, or the last successful
      fetch is older than ``stale_grace_seconds``, raises
      ``AuthError(503, "identity_provider_unavailable")`` -- never a 401,
      which would falsely blame the caller's credential.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        jwks_uri: str,
        cache_ttl_seconds: float = 300,
        min_refresh_seconds: float = 30,
        stale_grace_seconds: float = 86400,
        request_timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http_client = http_client
        self._jwks_uri = jwks_uri
        self._cache_ttl_seconds = cache_ttl_seconds
        self._min_refresh_seconds = min_refresh_seconds
        self._stale_grace_seconds = stale_grace_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._clock = clock

        self._jwks: dict[str, dict[str, Any]] | None = None
        self._pyjwks: dict[str, jwt.PyJWK] = {}
        self._last_success_monotonic: float | None = None
        self._last_forced_attempt_monotonic: float | None = None
        self._stale: bool = False

    @property
    def is_stale(self) -> bool:
        """``True`` once a refresh has failed and cached keys are being
        served past their normal TTL (exposed for the ``forge_jwks_stale``
        metric)."""
        return self._stale

    async def get_key(self, kid: str) -> jwt.PyJWK:
        """Resolve *kid* to a verification key, refreshing as needed.

        Raises
        ------
        AuthError
            ``401 unknown_key`` if *kid* is not present after a legitimate
            (or rate-limited-skipped) refresh; ``503
            identity_provider_unavailable`` if the JWKS endpoint cannot be
            reached and there is no usable cached key material.
        """
        now = self._clock()

        if self._jwks is not None and kid in self._jwks and not self._ttl_expired(now):
            return self._build(kid)

        is_kid_miss = self._jwks is None or kid not in self._jwks

        if is_kid_miss:
            if self._jwks is not None and not self._forced_refresh_allowed(now):
                # Rate limited: skip the fetch entirely and answer from what
                # we already know (a stale-but-known key, or a miss).
                if kid in self._jwks:
                    return self._build(kid)
                raise AuthError(401, _ERROR_UNKNOWN_KEY)
            fetched = await self._fetch(now, forced=True)
        else:
            fetched = await self._fetch(now, forced=False)

        if fetched:
            self._stale = False
            if self._jwks is not None and kid in self._jwks:
                return self._build(kid)
            raise AuthError(401, _ERROR_UNKNOWN_KEY)

        # The fetch failed.
        if self._jwks is None:
            raise AuthError(503, _ERROR_IDP_UNAVAILABLE)

        if self._within_stale_grace(now):
            self._stale = True
            if kid in self._jwks:
                return self._build(kid)
            raise AuthError(401, _ERROR_UNKNOWN_KEY)

        raise AuthError(503, _ERROR_IDP_UNAVAILABLE)

    # -- internals ------------------------------------------------------

    def _ttl_expired(self, now: float) -> bool:
        return (
            self._last_success_monotonic is None
            or (now - self._last_success_monotonic) >= self._cache_ttl_seconds
        )

    def _within_stale_grace(self, now: float) -> bool:
        return (
            self._last_success_monotonic is not None
            and (now - self._last_success_monotonic) <= self._stale_grace_seconds
        )

    def _forced_refresh_allowed(self, now: float) -> bool:
        return (
            self._last_forced_attempt_monotonic is None
            or (now - self._last_forced_attempt_monotonic) >= self._min_refresh_seconds
        )

    def _build(self, kid: str) -> jwt.PyJWK:
        assert self._jwks is not None  # noqa: S101 -- internal invariant
        cached = self._pyjwks.get(kid)
        if cached is not None:
            return cached
        pyjwk = jwt.PyJWK(self._jwks[kid])
        self._pyjwks[kid] = pyjwk
        return pyjwk

    async def _fetch(self, now: float, *, forced: bool) -> bool:
        if forced:
            self._last_forced_attempt_monotonic = now
        try:
            response = await self._http_client.get(
                self._jwks_uri, timeout=self._request_timeout_seconds
            )
            response.raise_for_status()
            data = response.json()
            keys = {
                key["kid"]: key
                for key in data.get("keys", [])
                if isinstance(key, dict) and "kid" in key
            }
        except Exception:
            logger.warning("JWKS refresh from %s failed", self._jwks_uri, exc_info=True)
            return False

        self._jwks = keys
        self._pyjwks = {}
        self._last_success_monotonic = now
        return True
