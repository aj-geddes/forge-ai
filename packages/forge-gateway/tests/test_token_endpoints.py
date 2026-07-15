"""Tests for POST/GET/DELETE /v1/auth/tokens (ADR-0002 SS5, SS6, SS12.3).

Exercises the route-logic enforcement this package owns on top of the
``forge_security.oidc.user_tokens`` core: the anti-escalation check, the
"no chaining" kind guard, unknown-role/TTL/label validation, owner-vs-admin
scoping, and fail-closed behaviour for a disabled feature or an
unavailable store.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from _token_fixtures import (
    ADMIN_EMAIL,
    STATIC_SERVICE_TOKEN_DIGEST,
    STATIC_SERVICE_TOKEN_RAW,
    USER_EMAIL,
    USER_SUB,
    make_app,
    wire,
)
from fastapi import FastAPI
from forge_config.schema import ServiceToken
from forge_gateway import security
from forge_security.oidc import SERVICE_TOKEN_PREFIX, Authorizer, ServiceTokenVerifier
from forge_security.oidc.user_tokens import UserTokenRecord, UserTokenStoreUnavailableError


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    security.reset_auth()
    yield
    security.reset_auth()


@pytest.fixture()
def app() -> FastAPI:
    return make_app()


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://forgeai.hvslocal") as ac:
        yield ac


def _cookies(cookie_value: str) -> dict[str, str]:
    return {"forge_session": cookie_value}


class _FlakyStore:
    """A minimal ``UserTokenStore`` stand-in that reports ``available =
    True`` (so ``_feature_store``'s pre-check passes) but raises
    ``UserTokenStoreUnavailableError`` from the mutating calls -- exercises
    the defensive TOCTOU-window catch in ``routes/tokens.py`` around
    ``mint_user_token``/``store.revoke`` (the store could, in principle,
    flip unavailable between the pre-check and the mutation)."""

    available = True

    def __init__(self, existing: UserTokenRecord | None = None) -> None:
        self._existing = existing

    async def load(self) -> None:
        return None

    def get_by_digest(self, digest: str) -> UserTokenRecord | None:  # noqa: ARG002
        return None

    def get_by_id(self, token_id: str) -> UserTokenRecord | None:
        if self._existing is not None and self._existing.id == token_id:
            return self._existing
        return None

    def list_for_owner(self, owner_sub: str) -> list[UserTokenRecord]:  # noqa: ARG002
        return []

    def list_all(self) -> list[UserTokenRecord]:
        return []

    async def add(self, record: UserTokenRecord) -> None:  # noqa: ARG002
        raise UserTokenStoreUnavailableError("simulated TOCTOU failure")

    async def revoke(self, token_id: str, *, by_owner: str) -> bool:  # noqa: ARG002
        raise UserTokenStoreUnavailableError("simulated TOCTOU failure")


# ---------------------------------------------------------------------------
# POST /v1/auth/tokens -- mint
# ---------------------------------------------------------------------------


class TestMint:
    async def test_mint_returns_raw_token_once_and_list_never_returns_it(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "laptop CLI"},
            cookies=_cookies(wiring.user_cookie),
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["token"].startswith("forge_sk_")
        assert set(body) == {"id", "token", "label", "roles", "created_at", "expires_at"}

        list_resp = await client.get("/v1/auth/tokens", cookies=_cookies(wiring.user_cookie))
        assert list_resp.status_code == 200
        listed = list_resp.json()["tokens"]
        assert len(listed) == 1
        assert "token" not in listed[0]
        assert "secret" not in listed[0]
        assert "secret_sha256" not in listed[0]

    async def test_mint_with_subset_roles_succeeds(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "ci bot", "roles": ["viewer"]},
            cookies=_cookies(wiring.user_cookie),
        )

        assert resp.status_code == 201
        assert resp.json()["roles"] == ["viewer"]

    async def test_mint_defaults_to_minter_roles_when_none_requested(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens", json={"label": "laptop"}, cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 201
        assert resp.json()["roles"] == ["user"]

    async def test_mint_with_permission_exceeding_owner_is_rejected_403_escalation_denied(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "sneaky", "roles": ["admin"]},
            cookies=_cookies(wiring.user_cookie),
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "escalation_denied"

    async def test_admin_can_mint_an_admin_scoped_token(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "admin CLI", "roles": ["admin"]},
            cookies=_cookies(wiring.admin_cookie),
        )

        assert resp.status_code == 201
        assert resp.json()["roles"] == ["admin"]

    async def test_service_token_principal_cannot_mint_403(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        await wire(tmp_path, static_service_token=True)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "chained"},
            headers={"Authorization": f"Bearer {STATIC_SERVICE_TOKEN_RAW}"},
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "service_tokens_cannot_mint"

    async def test_unbound_user_with_zero_roles_cannot_mint_403(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens", json={"label": "useless"}, cookies=_cookies(wiring.unbound_cookie)
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    async def test_unknown_role_rejected_400(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "typo", "roles": ["superadmin"]},
            cookies=_cookies(wiring.user_cookie),
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "unknown_role"

    @pytest.mark.parametrize("label", ["", "x" * 65, "bad\nlabel", "\t"])
    async def test_invalid_label_rejected_400(
        self, client: httpx.AsyncClient, tmp_path: Path, label: str
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens", json={"label": label}, cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_label"

    async def test_requested_ttl_over_cap_rejected_400(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, max_ttl_seconds=7_776_000)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "too long", "ttl_seconds": 7_776_001},
            cookies=_cookies(wiring.user_cookie),
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "ttl_out_of_range"

    async def test_requested_ttl_under_min_rejected_400(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.post(
            "/v1/auth/tokens",
            json={"label": "too short", "ttl_seconds": 60},
            cookies=_cookies(wiring.user_cookie),
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "ttl_out_of_range"

    async def test_default_ttl_applied_when_ttl_omitted(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, default_ttl_seconds=3600)

        resp = await client.post(
            "/v1/auth/tokens", json={"label": "default ttl"}, cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 201
        body = resp.json()
        created = body["created_at"]
        expires = body["expires_at"]
        assert created != expires  # sanity: an actual expiry was computed

    async def test_mint_when_user_tokens_disabled_returns_404(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, user_tokens_enabled=False)

        resp = await client.post(
            "/v1/auth/tokens", json={"label": "nope"}, cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 404

    async def test_token_store_unavailable_returns_503_and_does_not_break_static_tokens(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store_path.write_text("{not valid json")
        wiring = await wire(tmp_path, static_service_token=True, store_path=store_path)
        assert wiring.store.available is False  # corrupt file -> unavailable

        for method, path in (("POST", "/v1/auth/tokens"), ("GET", "/v1/auth/tokens")):
            resp = await client.request(
                method,
                path,
                json={"label": "x"} if method == "POST" else None,
                cookies=_cookies(wiring.user_cookie),
            )
            assert resp.status_code == 503
            assert resp.json()["error"] == "token_store_unavailable"

        # The corrupt dynamic store degrades only the user-token feature --
        # static-token credential verification (ADR-0001, untouched by
        # this ADR) still succeeds; the overall auth subsystem stays
        # healthy so OIDC/static-token routes keep serving traffic.
        info = ServiceTokenVerifier(
            [ServiceToken(id="svc1", secret_sha256=STATIC_SERVICE_TOKEN_DIGEST, roles=["admin"])]
        ).verify(STATIC_SERVICE_TOKEN_RAW)
        assert info.token_id == "svc1"
        assert security.is_auth_healthy() is True

    async def test_mint_never_logs_the_raw_secret(
        self, client: httpx.AsyncClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        wiring = await wire(tmp_path)

        with caplog.at_level("DEBUG"):
            resp = await client.post(
                "/v1/auth/tokens",
                json={"label": "audit-check"},
                cookies=_cookies(wiring.user_cookie),
            )

        body = resp.json()
        raw_token = body["token"]
        token_id = body["id"]
        # forge_sk_<token_id>_<secret> (ADR-0002 SS7.1); derive the secret
        # from this known prefix, not rsplit("_", 1) -- the base64url
        # secret can itself contain '_', so rsplit can grab only a short
        # coincidental tail that spuriously collides with unrelated log
        # text (e.g. the hex token_id), producing a flaky false leak.
        id_prefix = f"{SERVICE_TOKEN_PREFIX}{token_id}_"
        assert raw_token.startswith(id_prefix)
        secret = raw_token[len(id_prefix) :]
        assert len(secret) == 43
        assert all(secret not in record.getMessage() for record in caplog.records)

    async def test_mint_race_store_becomes_unavailable_during_mint_returns_503(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        security.configure_auth(
            session_codec=wiring.session_codec,
            service_token_verifier=ServiceTokenVerifier([], store=_FlakyStore()),
            oidc_verifier=None,
            authorizer=Authorizer(wiring.authorization_config),
            cookie_name="forge_session",
            dev_insecure=False,
            user_token_store=_FlakyStore(),
            user_token_config=security.get_user_token_config(),
            authorization_config=wiring.authorization_config,
        )

        resp = await client.post(
            "/v1/auth/tokens", json={"label": "race"}, cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 503
        assert resp.json()["error"] == "token_store_unavailable"


# ---------------------------------------------------------------------------
# GET /v1/auth/tokens -- list
# ---------------------------------------------------------------------------


class TestList:
    async def _mint(self, client: httpx.AsyncClient, cookie: str, label: str = "t") -> str:
        resp = await client.post("/v1/auth/tokens", json={"label": label}, cookies=_cookies(cookie))
        assert resp.status_code == 201
        return str(resp.json()["id"])

    async def test_list_is_scoped_to_owner_by_default(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        await self._mint(client, wiring.user_cookie, "alice-token")
        await self._mint(client, wiring.admin_cookie, "bob-token")

        resp = await client.get("/v1/auth/tokens", cookies=_cookies(wiring.user_cookie))

        assert resp.status_code == 200
        tokens = resp.json()["tokens"]
        assert len(tokens) == 1
        assert tokens[0]["label"] == "alice-token"
        assert "owner_email" not in tokens[0] or tokens[0]["owner_email"] is None

    async def test_admin_can_list_all_with_all_true(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        await self._mint(client, wiring.user_cookie, "alice-token")
        await self._mint(client, wiring.admin_cookie, "bob-token")

        resp = await client.get(
            "/v1/auth/tokens", params={"all": "true"}, cookies=_cookies(wiring.admin_cookie)
        )

        assert resp.status_code == 200
        tokens = resp.json()["tokens"]
        assert len(tokens) == 2
        labels = {t["label"] for t in tokens}
        assert labels == {"alice-token", "bob-token"}
        emails = {t["owner_email"] for t in tokens}
        assert emails == {USER_EMAIL, ADMIN_EMAIL}

    async def test_non_admin_all_true_is_silently_scoped_to_own(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        await self._mint(client, wiring.user_cookie, "alice-token")
        await self._mint(client, wiring.admin_cookie, "bob-token")

        resp = await client.get(
            "/v1/auth/tokens", params={"all": "true"}, cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 200
        tokens = resp.json()["tokens"]
        assert len(tokens) == 1
        assert tokens[0]["label"] == "alice-token"

    async def test_list_when_user_tokens_disabled_returns_404(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, user_tokens_enabled=False)

        resp = await client.get("/v1/auth/tokens", cookies=_cookies(wiring.user_cookie))

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/auth/tokens/{id} -- revoke
# ---------------------------------------------------------------------------


class TestRevoke:
    async def _mint_id(self, client: httpx.AsyncClient, cookie: str, label: str = "t") -> str:
        resp = await client.post("/v1/auth/tokens", json={"label": label}, cookies=_cookies(cookie))
        assert resp.status_code == 201
        return str(resp.json()["id"])

    async def test_user_cannot_revoke_another_users_token_returns_404(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        bobs_token_id = await self._mint_id(client, wiring.admin_cookie, "bobs")

        resp = await client.delete(
            f"/v1/auth/tokens/{bobs_token_id}", cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 404

    async def test_revoke_unknown_id_returns_404(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)

        resp = await client.delete(
            "/v1/auth/tokens/u_doesnotexist", cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 404

    async def test_owner_can_revoke_their_own_token(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        token_id = await self._mint_id(client, wiring.user_cookie, "mine")

        resp = await client.delete(
            f"/v1/auth/tokens/{token_id}", cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 204
        assert resp.content == b""

    async def test_admin_can_revoke_any_token(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        alice_token_id = await self._mint_id(client, wiring.user_cookie, "alices")

        resp = await client.delete(
            f"/v1/auth/tokens/{alice_token_id}", cookies=_cookies(wiring.admin_cookie)
        )

        assert resp.status_code == 204

    async def test_revoking_an_already_revoked_token_returns_404(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        token_id = await self._mint_id(client, wiring.user_cookie, "mine")
        first = await client.delete(
            f"/v1/auth/tokens/{token_id}", cookies=_cookies(wiring.user_cookie)
        )
        assert first.status_code == 204

        second = await client.delete(
            f"/v1/auth/tokens/{token_id}", cookies=_cookies(wiring.user_cookie)
        )

        assert second.status_code == 404

    async def test_revoke_when_user_tokens_disabled_returns_404(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, user_tokens_enabled=False)

        resp = await client.delete(
            "/v1/auth/tokens/u_whatever", cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 404

    async def test_revoke_race_store_becomes_unavailable_during_revoke_returns_503(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)
        now = datetime.now(UTC)
        existing = UserTokenRecord(
            id="u_racecondition",
            secret_sha256="b" * 64,
            owner_sub=USER_SUB,
            owner_email=USER_EMAIL,
            label="racey",
            roles=["user"],
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        flaky_store = _FlakyStore(existing=existing)
        security.configure_auth(
            session_codec=wiring.session_codec,
            service_token_verifier=ServiceTokenVerifier([], store=flaky_store),
            oidc_verifier=None,
            authorizer=Authorizer(wiring.authorization_config),
            cookie_name="forge_session",
            dev_insecure=False,
            user_token_store=flaky_store,
            user_token_config=security.get_user_token_config(),
            authorization_config=wiring.authorization_config,
        )

        resp = await client.delete(
            "/v1/auth/tokens/u_racecondition", cookies=_cookies(wiring.user_cookie)
        )

        assert resp.status_code == 503
        assert resp.json()["error"] == "token_store_unavailable"
