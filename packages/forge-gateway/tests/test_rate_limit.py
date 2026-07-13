"""Tests wiring ``security.rate_limit_rpm`` to an actual limiter.

Previously ``rate_limit_rpm`` existed in the config schema and in the
ADR-0001 failure matrix (429 ``rate_limited``) but nothing in the gateway
ever enforced it. This suite covers:

* identity-keyed limiting on the authenticated request path
  (``forge_gateway.security.get_principal``, which every protected route
  goes through via ``require_permission`` / ``enforce_mcp_security``);
* independent buckets per principal (not a shared/global bucket, not an
  IP-only bucket);
* ``/health/*`` is never subject to any limiter;
* IP-keyed limiting on the pre-authentication ``/auth/login`` ->
  ``/auth/callback`` flow (the documented abuse vector: hammering
  ``/auth/callback`` with garbage authorization codes);
* fail-open behaviour: a bug inside the limiter must not take the service
  down -- it is a throttle, not an authorization gate.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import Depends, FastAPI
from forge_config.schema import AuthorizationConfig, ServiceToken
from forge_gateway import rate_limit, security
from forge_gateway.routes import auth as auth_routes
from forge_gateway.routes import health
from forge_security.oidc import Authorizer, Principal, ServiceTokenVerifier

TOKEN_ALICE = "forge_sk_alice_" + "a" * 43  # noqa: S105
TOKEN_BOB = "forge_sk_bob_" + "b" * 43  # noqa: S105

# Module-level singleton so the Depends(...) call happens once at import
# time, not on every request (ruff B008).
_require_tools_invoke = Depends(security.require_permission("tools:invoke"))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """This suite wires auth/rate-limits explicitly per test, opting out of
    the repo-wide permissive ``dev_insecure`` default (see conftest.py)."""
    security.reset_auth()
    rate_limit.reset_rate_limiting()
    yield
    security.reset_auth()
    rate_limit.reset_rate_limiting()


def _wire_two_service_tokens(*, rate_limit_rpm: int | None) -> None:
    security.configure_auth(
        session_codec=None,
        service_token_verifier=ServiceTokenVerifier(
            [
                ServiceToken(id="alice", secret_sha256=_digest(TOKEN_ALICE), roles=["admin"]),
                ServiceToken(id="bob", secret_sha256=_digest(TOKEN_BOB), roles=["admin"]),
            ]
        ),
        oidc_verifier=None,
        authorizer=Authorizer(AuthorizationConfig()),
        dev_insecure=False,
        rate_limit_rpm=rate_limit_rpm,
    )


def _protected_app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/protected")
    async def _protected(principal: Principal = _require_tools_invoke) -> dict[str, str]:
        return {"sub": principal.sub}

    app.include_router(health.router)
    return app


async def _get(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path, **kwargs)


class TestIdentityRateLimiting:
    async def test_principal_under_limit_passes(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=2)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        resp = await _get(app, "/v1/protected", headers=headers)

        assert resp.status_code == 200

    async def test_principal_exceeding_limit_gets_429(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=2)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        await _get(app, "/v1/protected", headers=headers)
        await _get(app, "/v1/protected", headers=headers)
        resp = await _get(app, "/v1/protected", headers=headers)

        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limited"

    async def test_429_includes_retry_after_header(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        await _get(app, "/v1/protected", headers=headers)
        resp = await _get(app, "/v1/protected", headers=headers)

        assert resp.status_code == 429
        assert "retry-after" in {h.lower() for h in resp.headers}

    async def test_two_principals_have_independent_buckets(self) -> None:
        """Alice exhausting her budget must not affect Bob -- buckets are
        keyed on identity, not shared globally."""
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = _protected_app()
        alice_headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}
        bob_headers = {"Authorization": f"Bearer {TOKEN_BOB}"}

        await _get(app, "/v1/protected", headers=alice_headers)
        alice_second = await _get(app, "/v1/protected", headers=alice_headers)
        bob_first = await _get(app, "/v1/protected", headers=bob_headers)

        assert alice_second.status_code == 429
        assert bob_first.status_code == 200

    async def test_keyed_on_identity_not_ip_alone(self) -> None:
        """Two distinct principals hitting the endpoint through the same
        transport (and therefore effectively the same/no client IP in this
        test harness) must still get independent budgets -- proving the
        key is the resolved principal identity, not the request's IP."""
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = _protected_app()
        alice_headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}
        bob_headers = {"Authorization": f"Bearer {TOKEN_BOB}"}

        alice_resp = await _get(app, "/v1/protected", headers=alice_headers)
        bob_resp = await _get(app, "/v1/protected", headers=bob_headers)

        # Both are each principal's *first* request -- both must succeed,
        # which would be impossible if identity collapsed to a shared
        # per-IP bucket at rpm=1.
        assert alice_resp.status_code == 200
        assert bob_resp.status_code == 200

    async def test_disabled_by_default_when_rate_limit_rpm_not_configured(self) -> None:
        """``configure_auth`` without ``rate_limit_rpm`` must not enable
        limiting -- this is what keeps the rest of the test suite (which
        wires auth via the autouse dev_insecure fixture) unaffected."""
        _wire_two_service_tokens(rate_limit_rpm=None)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        for _ in range(10):
            resp = await _get(app, "/v1/protected", headers=headers)
            assert resp.status_code == 200


class TestHealthNeverLimited:
    async def test_health_live_never_rate_limited(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        # Exhaust Alice's identity budget on the protected route.
        await _get(app, "/v1/protected", headers=headers)
        exhausted = await _get(app, "/v1/protected", headers=headers)
        assert exhausted.status_code == 429

        # /health/* is never gated by get_principal, so it must be
        # unaffected regardless of any exhausted budget.
        for _ in range(5):
            resp = await _get(app, "/health/live")
            assert resp.status_code == 200


class TestAuthFlowIPRateLimiting:
    def _auth_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(auth_routes.router)
        app.include_router(health.router)
        return app

    async def test_login_under_limit_passes_through_to_handler(self) -> None:
        """Under the limit, the request reaches the real handler -- which
        returns 503 oidc_not_configured here because no OIDC is wired in
        this test, proving the *rate limiter* did not block it."""
        rate_limit.configure_rate_limiting(2)
        app = self._auth_app()

        resp = await _get(app, "/auth/login")

        assert resp.status_code == 503

    async def test_login_exceeding_limit_gets_429(self) -> None:
        rate_limit.configure_rate_limiting(2)
        app = self._auth_app()

        await _get(app, "/auth/login")
        await _get(app, "/auth/login")
        resp = await _get(app, "/auth/login")

        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limited"

    async def test_callback_exceeding_limit_gets_429(self) -> None:
        rate_limit.configure_rate_limiting(1)
        app = self._auth_app()

        await _get(app, "/auth/callback")
        resp = await _get(app, "/auth/callback")

        assert resp.status_code == 429

    async def test_login_and_callback_share_independent_budget_from_protected_routes(
        self,
    ) -> None:
        """The auth-flow (IP-keyed) limiter and the identity-keyed limiter
        are independent -- exhausting one must not affect the other."""
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = FastAPI()
        app.include_router(auth_routes.router)

        @app.get("/v1/protected")
        async def _protected(principal: Principal = _require_tools_invoke) -> dict[str, str]:
            return {"sub": principal.sub}

        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}
        protected_resp = await _get(app, "/v1/protected", headers=headers)
        login_resp = await _get(app, "/auth/login")

        assert protected_resp.status_code == 200
        assert login_resp.status_code == 503  # reached the real handler, not 429


class TestFailOpen:
    async def test_limiter_internal_error_fails_open_and_logs(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        broken_check = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "forge_security.rate_limit.SlidingWindowRateLimiter.check",
            broken_check,
        ):
            resp = await _get(app, "/v1/protected", headers=headers)

        assert resp.status_code == 200
        broken_check.assert_called()

    async def test_limiter_internal_error_logs_exception(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=1)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        with (
            patch(
                "forge_security.rate_limit.SlidingWindowRateLimiter.check",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(rate_limit.logger, "exception") as mock_log,
        ):
            resp = await _get(app, "/v1/protected", headers=headers)

        assert resp.status_code == 200
        mock_log.assert_called_once()


class TestResetAuthClearsRateLimiting:
    async def test_reset_auth_disables_rate_limiting(self) -> None:
        _wire_two_service_tokens(rate_limit_rpm=1)
        security.reset_auth()
        _wire_two_service_tokens(rate_limit_rpm=None)
        app = _protected_app()
        headers = {"Authorization": f"Bearer {TOKEN_ALICE}"}

        for _ in range(5):
            resp = await _get(app, "/v1/protected", headers=headers)
            assert resp.status_code == 200
