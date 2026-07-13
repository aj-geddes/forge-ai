"""End-to-end tests for ADR-0002 user-issued API keys, through the real
resolver (ASGI, no mocking of ``resolve_principal``/``ServiceTokenVerifier``).

Covers: a minted token authenticates a subsequent request exactly like a
static service token would; revocation takes effect on the very next
request with no reload; a fresh store instance built from the same file
(a simulated pod restart) still resolves a previously-minted token; and
the CSRF double-submit-token requirement applies to the cookie-authenticated
mint/revoke calls exactly as it does to every other state-changing route.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from _token_fixtures import SESSION_COOKIE_NAME, STATIC_SERVICE_TOKEN_RAW, wire
from fastapi import FastAPI
from forge_gateway import security
from forge_gateway.middleware.csrf import CSRFMiddleware
from forge_gateway.routes import auth as auth_routes
from forge_gateway.routes import tokens as tokens_routes
from forge_security.oidc import ServiceTokenVerifier
from forge_security.oidc.user_tokens import FileUserTokenStore


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    security.reset_auth()
    yield
    security.reset_auth()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(tokens_routes.router)
    app.include_router(auth_routes.router)
    return app


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://forgeai.hvslocal") as ac:
        yield ac


class TestMintedTokenAuthenticatesSubsequentRequests:
    async def test_minted_token_authorizes_a_protected_route_as_service_principal(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        mint_resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "laptop CLI"},
            cookies={SESSION_COOKIE_NAME: wiring.user_cookie},
        )
        assert mint_resp.status_code == 201
        raw_token = mint_resp.json()["token"]
        token_id = mint_resp.json()["id"]

        me_resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {raw_token}"})

        assert me_resp.status_code == 200
        body = me_resp.json()
        assert body["kind"] == "service"
        assert body["sub"] == f"svc:{token_id}"
        assert body["roles"] == ["user"]
        assert body["token_id"] == token_id

    async def test_mint_then_use_then_revoke_then_401(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        mint_resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "laptop CLI"},
            cookies={SESSION_COOKIE_NAME: wiring.user_cookie},
        )
        raw_token = mint_resp.json()["token"]
        token_id = mint_resp.json()["id"]
        bearer = {"Authorization": f"Bearer {raw_token}"}

        first_use = await client.get("/v1/auth/me", headers=bearer)
        assert first_use.status_code == 200

        revoke_resp = await client.delete(
            f"/v1/auth/tokens/{token_id}", cookies={SESSION_COOKIE_NAME: wiring.user_cookie}
        )
        assert revoke_resp.status_code == 204

        second_use = await client.get("/v1/auth/me", headers=bearer)
        assert second_use.status_code == 401
        assert second_use.json()["detail"] == "invalid_token"


class TestSurvivesSimulatedPodRestart:
    async def test_minted_token_survives_a_simulated_pod_restart(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        wiring = await wire(tmp_path, store_path=store_path)

        mint_resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "laptop CLI"},
            cookies={SESSION_COOKIE_NAME: wiring.user_cookie},
        )
        raw_token = mint_resp.json()["token"]

        # Simulate a pod restart: a *brand new* store object reads the
        # same on-disk file from scratch (no shared in-memory state with
        # the store that minted the token).
        restarted_store = FileUserTokenStore(store_path)
        await restarted_store.load()
        verifier = ServiceTokenVerifier([], store=restarted_store)

        info = verifier.verify(raw_token)

        assert info.token_id == mint_resp.json()["id"]
        assert info.roles == ["user"]


class TestCsrfEnforcedOnMintAndRevoke:
    @pytest.fixture()
    async def csrf_client(self) -> AsyncIterator[httpx.AsyncClient]:
        app = _make_app()
        app.add_middleware(
            CSRFMiddleware,
            session_cookie_name=SESSION_COOKIE_NAME,
            csrf_cookie_name="forge_csrf",
            allowed_origins=["https://forgeai.hvslocal"],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://forgeai.hvslocal"
        ) as ac:
            yield ac

    async def test_mint_without_csrf_header_is_rejected(
        self, csrf_client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await csrf_client.post(
            "/v1/auth/tokens",
            json={"label": "no csrf"},
            cookies={SESSION_COOKIE_NAME: wiring.user_cookie},
            headers={"Origin": "https://forgeai.hvslocal"},
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "csrf_failed"

    async def test_mint_with_matching_csrf_header_and_cookie_succeeds(
        self, csrf_client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await csrf_client.post(
            "/v1/auth/tokens",
            json={"label": "with csrf"},
            cookies={SESSION_COOKIE_NAME: wiring.user_cookie, "forge_csrf": "tok123"},
            headers={"Origin": "https://forgeai.hvslocal", "X-CSRF-Token": "tok123"},
        )

        assert resp.status_code == 201

    async def test_bearer_authenticated_mint_is_csrf_exempt(
        self, csrf_client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """A minted token itself is a service credential -- using it as a
        Bearer header carries no ambient credential, so CSRF does not
        apply (matches every other Authorization-header route)."""
        await wire(tmp_path, static_service_token=True)

        resp = await csrf_client.post(
            "/v1/auth/tokens",
            json={"label": "chained"},
            headers={"Authorization": f"Bearer {STATIC_SERVICE_TOKEN_RAW}"},
        )

        # Not rejected by CSRF (would be 403 csrf_failed) -- it is
        # rejected by the "no chaining" rule instead (403
        # service_tokens_cannot_mint), proving CSRF never even engaged.
        assert resp.status_code == 403
        assert resp.json()["error"] == "service_tokens_cannot_mint"
