"""Tests for the per-owner ACTIVE-token cap on POST /v1/auth/tokens
(security review MEDIUM finding: unbounded self-service minting fills the
shared PVC store and degrades mint/revoke latency for every caller, since
every mint rewrites the whole JSON document under one global lock).

The cap is enforced in ``forge_gateway.routes.tokens.mint_token`` -- a
pre-mint gate that counts the caller's ACTIVE (non-revoked, non-expired)
tokens via ``store.list_for_owner`` and rejects with ``403
too_many_tokens`` before ``mint_user_token`` (and therefore ``store.add``)
is ever called. The limit itself comes from
``security.service_tokens.user_tokens.max_tokens_per_owner``
(``forge_config.schema.UserTokenConfig``), never hardcoded in the route.

Admins are subject to the same cap as everyone else -- there is no
"admin is exempt" carve-out, keeping the enforcement simple and
uniformly greppable.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from _token_fixtures import ADMIN_EMAIL, ADMIN_SUB, USER_EMAIL, USER_SUB, make_app, wire
from fastapi import FastAPI
from forge_gateway import security
from forge_security.oidc.user_tokens import UserTokenRecord


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


async def _mint(client: httpx.AsyncClient, cookie: str, label: str) -> httpx.Response:
    return await client.post("/v1/auth/tokens", json={"label": label}, cookies=_cookies(cookie))


def _active_record(
    owner_sub: str, owner_email: str, *, suffix: str, expired: bool = False
) -> UserTokenRecord:
    """Build a directly-persistable record -- used to seed the store below
    the HTTP layer so tests don't need to make dozens of real HTTP mints
    (and to construct an already-expired record, which the mint endpoint's
    own TTL floor would otherwise make impossible to produce through it)."""
    now = datetime.now(UTC)
    created = now - timedelta(days=1)
    expires = now - timedelta(hours=1) if expired else now + timedelta(days=1)
    digest = hashlib.sha256(f"seed-{owner_sub}-{suffix}".encode()).hexdigest()
    return UserTokenRecord(
        id=f"u_seed{suffix}",
        secret_sha256=digest,
        owner_sub=owner_sub,
        owner_email=owner_email,
        label=f"seed-{suffix}",
        roles=["user"],
        created_at=created,
        expires_at=expires,
    )


class TestMintCap:
    async def test_minting_under_the_cap_succeeds(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, max_tokens_per_owner=3)

        first = await _mint(client, wiring.user_cookie, "one")
        second = await _mint(client, wiring.user_cookie, "two")

        assert first.status_code == 201
        assert second.status_code == 201

    async def test_minting_at_the_cap_is_rejected_403_too_many_tokens(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, max_tokens_per_owner=2)

        first = await _mint(client, wiring.user_cookie, "one")
        second = await _mint(client, wiring.user_cookie, "two")
        third = await _mint(client, wiring.user_cookie, "three")

        assert first.status_code == 201
        assert second.status_code == 201
        assert third.status_code == 403
        assert third.json()["error"] == "too_many_tokens"

    async def test_user_who_revokes_a_token_can_then_mint_again(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, max_tokens_per_owner=1)

        minted = await _mint(client, wiring.user_cookie, "only-slot")
        assert minted.status_code == 201
        token_id = minted.json()["id"]

        blocked = await _mint(client, wiring.user_cookie, "should-fail")
        assert blocked.status_code == 403
        assert blocked.json()["error"] == "too_many_tokens"

        revoke_resp = await client.delete(
            f"/v1/auth/tokens/{token_id}", cookies=_cookies(wiring.user_cookie)
        )
        assert revoke_resp.status_code == 204

        after_revoke = await _mint(client, wiring.user_cookie, "new-slot")
        assert after_revoke.status_code == 201

    async def test_expired_token_does_not_count_toward_cap(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, max_tokens_per_owner=1)
        await wiring.store.add(_active_record(USER_SUB, USER_EMAIL, suffix="expired", expired=True))

        resp = await _mint(client, wiring.user_cookie, "fresh")

        assert resp.status_code == 201

    async def test_cap_is_per_owner(self, client: httpx.AsyncClient, tmp_path: Path) -> None:
        wiring = await wire(tmp_path, max_tokens_per_owner=1)

        alice_first = await _mint(client, wiring.user_cookie, "alice-one")
        alice_second = await _mint(client, wiring.user_cookie, "alice-two")
        bob_first = await _mint(client, wiring.admin_cookie, "bob-one")

        assert alice_first.status_code == 201
        assert alice_second.status_code == 403
        assert bob_first.status_code == 201  # a different owner, unaffected by alice's cap

    async def test_cap_value_comes_from_config(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path, max_tokens_per_owner=5)

        responses = [await _mint(client, wiring.user_cookie, f"t{i}") for i in range(5)]
        sixth = await _mint(client, wiring.user_cookie, "t6")

        assert all(r.status_code == 201 for r in responses)
        assert sixth.status_code == 403
        assert sixth.json()["error"] == "too_many_tokens"

    async def test_default_cap_applies_when_field_omitted(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        wiring = await wire(tmp_path)  # max_tokens_per_owner omitted -> UserTokenConfig default
        for i in range(25):
            await wiring.store.add(_active_record(ADMIN_SUB, ADMIN_EMAIL, suffix=str(i)))

        resp = await _mint(client, wiring.admin_cookie, "one-too-many")

        assert resp.status_code == 403
        assert resp.json()["error"] == "too_many_tokens"
