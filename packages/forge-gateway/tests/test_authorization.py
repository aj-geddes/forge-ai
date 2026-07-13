"""Tests for claims -> roles -> permissions authorization (ADR-0001 SS6).

Authentication answers *who*; these tests prove authenticated is not the
same as authorized: a caller with no matching role binding gets 403, not
401 -- and a role that doesn't carry the needed permission also gets 403.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import AuthorizationConfig, ServiceToken
from forge_gateway import security
from forge_gateway.routes import admin, conversational
from forge_security.oidc import Authorizer, ServiceTokenVerifier

_DEFAULT_ROLES = {
    "viewer": ["config:read", "metrics:read"],
    "user": ["config:read", "metrics:read", "agent:invoke", "tools:invoke"],
    "admin": ["*"],
}


def _token(token_id: str, plaintext: str, roles: list[str]) -> ServiceToken:
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return ServiceToken(id=token_id, secret_sha256=digest, roles=roles)


USER_TOKEN = "forge_sk_user_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"  # noqa: S105
VIEWER_TOKEN = "forge_sk_viewer_bbcdefghijklmnopqrstuvwxyz0123456789ABCDEF"  # noqa: S105
NO_ROLE_TOKEN = "forge_sk_norole_cccdefghijklmnopqrstuvwxyz0123456789ABCDEF"  # noqa: S105


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    security.reset_auth()
    yield
    security.reset_auth()
    admin.set_state(config=None, config_path="")


def _wire(*, bindings_as_roles: dict[str, list[str]] | None = None) -> None:
    """Wire three service tokens with distinct roles: user, viewer, and a
    token whose role carries no permissions at all (no matching binding
    equivalent for the service-token path, which declares roles inline)."""
    security.configure_auth(
        session_codec=None,
        service_token_verifier=ServiceTokenVerifier(
            [
                _token("user-svc", USER_TOKEN, roles=["user"]),
                _token("viewer-svc", VIEWER_TOKEN, roles=["viewer"]),
                _token("norole-svc", NO_ROLE_TOKEN, roles=[]),
            ]
        ),
        oidc_verifier=None,
        authorizer=Authorizer(AuthorizationConfig(roles=bindings_as_roles or dict(_DEFAULT_ROLES))),
        dev_insecure=False,
    )


@pytest.fixture()
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_conversational.return_value = ForgeRunResult(output="hi")
    return agent


def _conversational_app(agent: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(conversational.router)
    conversational.set_agent(agent)
    conversational.set_config(None)
    return app


def _admin_app() -> FastAPI:
    from forge_config.schema import ForgeConfig

    app = FastAPI()
    app.include_router(admin.router)
    admin.set_state(config=ForgeConfig(), config_path="/tmp/forge.yaml", agent=None)  # noqa: S108
    return app


async def _get(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path, **kwargs)


async def _post(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(path, **kwargs)


async def _put(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.put(path, **kwargs)


class TestUserRoleCanChatButNotWriteConfig:
    async def test_user_role_can_chat(self, mock_agent: AsyncMock) -> None:
        _wire()
        resp = await _post(
            _conversational_app(mock_agent),
            "/v1/chat/completions",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200

    async def test_user_role_cannot_write_config(self) -> None:
        _wire()
        resp = await _put(
            _admin_app(),
            "/v1/admin/config",
            json={"config": {}},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"


class TestAdminRoleCanWriteConfig:
    async def test_admin_role_can_write_config(self) -> None:
        from forge_config.schema import ForgeConfig

        security.configure_auth(
            session_codec=None,
            service_token_verifier=ServiceTokenVerifier(
                [_token("admin-svc", "forge_sk_admin_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", ["admin"])]
            ),
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig(roles=dict(_DEFAULT_ROLES))),
            dev_insecure=False,
        )
        resp = await _put(
            _admin_app(),
            "/v1/admin/config",
            json={"config": ForgeConfig().model_dump(mode="json")},
            headers={"Authorization": "Bearer forge_sk_admin_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"},
        )
        assert resp.status_code == 200


class TestViewerCanReadConfigButNotChat:
    async def test_viewer_can_read_config(self) -> None:
        _wire()
        resp = await _get(
            _admin_app(), "/v1/admin/config", headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
        )
        assert resp.status_code == 200

    async def test_viewer_cannot_chat(self, mock_agent: AsyncMock) -> None:
        _wire()
        resp = await _post(
            _conversational_app(mock_agent),
            "/v1/chat/completions",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
        assert resp.status_code == 403


class TestNoMatchingRoleBindingDeniesWith403NotUnauthorized:
    """A caller who authenticates successfully but matches no role/permission
    is FORBIDDEN, not UNAUTHORIZED -- authenticated is not authorized."""

    async def test_service_token_with_no_roles_gets_403_not_401(
        self, mock_agent: AsyncMock
    ) -> None:
        _wire()
        resp = await _post(
            _conversational_app(mock_agent),
            "/v1/chat/completions",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {NO_ROLE_TOKEN}"},
        )
        assert resp.status_code == 403
        assert resp.status_code != 401


class TestServiceTokenScopedToAgentInvokeCannotReachAdmin:
    async def test_service_token_scoped_to_agent_invoke_cannot_reach_admin(self) -> None:
        agent_only_token = "forge_sk_agentonly_ddddddddddddddddddddddddddddddddd"  # noqa: S105
        security.configure_auth(
            session_codec=None,
            service_token_verifier=ServiceTokenVerifier(
                [_token("agent-only", agent_only_token, roles=["agent-invoke-only"])]
            ),
            oidc_verifier=None,
            authorizer=Authorizer(
                AuthorizationConfig(roles={"agent-invoke-only": ["agent:invoke"]})
            ),
            dev_insecure=False,
        )
        resp = await _get(
            _admin_app(),
            "/v1/admin/config",
            headers={"Authorization": f"Bearer {agent_only_token}"},
        )
        assert resp.status_code == 403
