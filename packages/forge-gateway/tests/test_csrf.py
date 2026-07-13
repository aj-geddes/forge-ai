"""Tests for CSRF protection on cookie-authenticated requests (ADR-0001 SS4.2).

Double-submit token + Origin check apply to cookie-authenticated,
state-changing requests. Bearer/service-token requests carry no ambient
credential and are exempt entirely.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from forge_gateway.middleware.csrf import CSRFMiddleware

SESSION_COOKIE = "forge_session"
CSRF_COOKIE = "forge_csrf"
ALLOWED_ORIGIN = "https://forgeai.hvslocal"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CSRFMiddleware,
        session_cookie_name=SESSION_COOKIE,
        csrf_cookie_name=CSRF_COOKIE,
        allowed_origins=[ALLOWED_ORIGIN],
    )

    @app.post("/v1/admin/config")
    async def write() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/admin/config")
    async def read() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _request(
    method: str,
    path: str,
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies or {}
    ) as ac:
        return await ac.request(method, path, headers=headers or {})


class TestCookieAuthPostWithoutCsrfIsRejected:
    async def test_cookie_auth_post_without_csrf_header_returns_403(self) -> None:
        resp = await _request(
            "POST",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "csrf-token-value"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "csrf_failed"

    async def test_cookie_auth_post_with_mismatched_csrf_returns_403(self) -> None:
        resp = await _request(
            "POST",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "real-csrf-token"},
            headers={"Origin": ALLOWED_ORIGIN, "X-CSRF-Token": "wrong-csrf-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "csrf_failed"

    async def test_cookie_auth_post_missing_origin_returns_403(self) -> None:
        resp = await _request(
            "POST",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "csrf-token-value"},
            headers={"X-CSRF-Token": "csrf-token-value"},
        )
        assert resp.status_code == 403

    async def test_cookie_auth_post_with_untrusted_origin_returns_403(self) -> None:
        resp = await _request(
            "POST",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "csrf-token-value"},
            headers={"Origin": "https://evil.example.com", "X-CSRF-Token": "csrf-token-value"},
        )
        assert resp.status_code == 403


class TestCookieAuthPostWithValidCsrfSucceeds:
    async def test_cookie_auth_post_with_matching_csrf_and_origin_succeeds(self) -> None:
        resp = await _request(
            "POST",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "matching-token"},
            headers={"Origin": ALLOWED_ORIGIN, "X-CSRF-Token": "matching-token"},
        )
        assert resp.status_code == 200


class TestBearerAuthIsCsrfExempt:
    async def test_bearer_auth_post_without_csrf_header_succeeds(self) -> None:
        """Bearer/service-token requests carry no ambient credential -- no
        Origin, no CSRF cookie, nothing to forge -- and must not be blocked."""
        resp = await _request(
            "POST",
            "/v1/admin/config",
            headers={"Authorization": "Bearer forge_sk_ci_whatever"},
        )
        assert resp.status_code == 200

    async def test_bearer_auth_wins_even_with_a_session_cookie_present(self) -> None:
        """If both a bearer header and a session cookie are present, the
        presence of Authorization exempts the request from CSRF -- matches
        resolve_principal's cookie-first precedence being a *server-side*
        concern, while CSRF-exemption is about "is there an ambient
        credential a browser could have attached automatically"."""
        resp = await _request(
            "POST",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "csrf-token-value"},
            headers={"Authorization": "Bearer forge_sk_ci_whatever"},
        )
        assert resp.status_code == 200


class TestSafeMethodsAreExempt:
    async def test_cookie_auth_get_without_csrf_succeeds(self) -> None:
        resp = await _request(
            "GET",
            "/v1/admin/config",
            cookies={SESSION_COOKIE: "some-session-value", CSRF_COOKIE: "csrf-token-value"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert resp.status_code == 200

    async def test_no_session_cookie_at_all_skips_csrf_check(self) -> None:
        """No session cookie means no ambient credential to forge either --
        the request will separately fail authentication (401), but CSRF
        must not be the reason."""
        resp = await _request("POST", "/v1/admin/config")
        assert resp.status_code == 200  # this dummy app has no auth of its own
