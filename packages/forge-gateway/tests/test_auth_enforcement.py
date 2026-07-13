"""Regression suite for the live unauthenticated-deployment vulnerability
(ADR-0001 SS1.2): every agent-driving surface must require a verified
credential, and the historical ``X-Caller-ID`` header / ``caller_id``
query-parameter bypasses must no longer authenticate anything, anywhere.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import AuthMode, AuthorizationConfig, SecurityAuthConfig, ServiceToken
from forge_gateway import security
from forge_gateway.routes import a2a, admin, conversational, health, programmatic
from forge_gateway.routes import mcp as mcp_module
from forge_security.oidc import Authorizer, ServiceTokenVerifier

SERVICE_TOKEN = "forge_sk_ci_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHI"  # noqa: S105
_DIGEST = hashlib.sha256(SERVICE_TOKEN.encode("utf-8")).hexdigest()
AUTH_HEADERS = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    """This suite is entirely about auth, so it opts out of the repo-wide
    permissive ``dev_insecure`` default (see conftest.py) and wires the
    auth subsystem explicitly, per test."""
    security.reset_auth()
    yield
    security.reset_auth()
    admin.set_state(config=None, config_path="")


def _wire_admin_role_service_token() -> None:
    security.configure_auth(
        session_codec=None,
        service_token_verifier=ServiceTokenVerifier(
            [ServiceToken(id="ci", secret_sha256=_DIGEST, roles=["admin"])]
        ),
        oidc_verifier=None,
        authorizer=Authorizer(AuthorizationConfig()),
        dev_insecure=False,
    )


@pytest.fixture()
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_conversational.return_value = ForgeRunResult(output="hi")
    agent.run_structured.return_value = ForgeRunResult(output={"ok": True})
    return agent


def _conversational_app(agent: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(conversational.router)
    conversational.set_agent(agent)
    conversational.set_config(None)
    return app


def _programmatic_app(agent: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(programmatic.router)
    programmatic.set_agent(agent)
    programmatic.set_config(None)
    return app


def _a2a_app(agent: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(a2a.router)
    a2a.set_agent(agent)
    return app


def _admin_app() -> FastAPI:
    from forge_config.schema import ForgeConfig

    app = FastAPI()
    app.include_router(admin.router)
    admin.set_state(config=ForgeConfig(), config_path="/tmp/forge.yaml", agent=None)  # noqa: S108
    return app


async def _post(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(path, **kwargs)


async def _get(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path, **kwargs)


# ---------------------------------------------------------------------------
# THE bug: unauthenticated POST /v1/chat/completions
# ---------------------------------------------------------------------------


class TestChatCompletionsRequiresAuth:
    async def test_chat_completions_without_credentials_returns_401(
        self, mock_agent: AsyncMock
    ) -> None:
        _wire_admin_role_service_token()
        resp = await _post(
            _conversational_app(mock_agent), "/v1/chat/completions", json={"message": "hi"}
        )
        assert resp.status_code == 401
        mock_agent.run_conversational.assert_not_called()

    async def test_chat_completions_with_valid_service_token_succeeds(
        self, mock_agent: AsyncMock
    ) -> None:
        _wire_admin_role_service_token()
        resp = await _post(
            _conversational_app(mock_agent),
            "/v1/chat/completions",
            json={"message": "hi"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200


class TestCallerIdBypassIsDeleted:
    """ADR-0001 SS1.2/SS5.1: X-Caller-ID and caller_id no longer authenticate."""

    async def test_chat_completions_with_x_caller_id_header_returns_401(
        self, mock_agent: AsyncMock
    ) -> None:
        _wire_admin_role_service_token()
        resp = await _post(
            _conversational_app(mock_agent),
            "/v1/chat/completions",
            json={"message": "hi"},
            headers={"X-Caller-ID": "admin"},
        )
        assert resp.status_code == 401
        mock_agent.run_conversational.assert_not_called()

    async def test_chat_completions_with_caller_id_query_param_returns_401(
        self, mock_agent: AsyncMock
    ) -> None:
        _wire_admin_role_service_token()
        resp = await _post(
            _conversational_app(mock_agent),
            "/v1/chat/completions?caller_id=admin",
            json={"message": "hi"},
        )
        assert resp.status_code == 401
        mock_agent.run_conversational.assert_not_called()

    async def test_admin_config_with_caller_id_query_param_returns_401(self) -> None:
        _wire_admin_role_service_token()
        resp = await _get(_admin_app(), "/v1/admin/config?caller_id=admin")
        assert resp.status_code == 401


class TestOtherSurfacesRequireAuth:
    async def test_mcp_mount_without_credentials_returns_401(self, mock_agent: AsyncMock) -> None:
        _wire_admin_role_service_token()
        app = FastAPI()
        app.mount("/mcp", mcp_module.get_mcp_mount_app())
        resp = await _post(app, "/mcp/", json={})
        assert resp.status_code == 401

    async def test_a2a_task_without_credentials_returns_401(self, mock_agent: AsyncMock) -> None:
        _wire_admin_role_service_token()
        resp = await _post(
            _a2a_app(mock_agent), "/a2a/tasks", json={"task_type": "x", "payload": {}}
        )
        assert resp.status_code == 401

    async def test_a2a_agent_card_stays_public(self, mock_agent: AsyncMock) -> None:
        """Discovery is deliberately unauthenticated (ADR-0001 SS6.1)."""
        _wire_admin_role_service_token()
        resp = await _get(_a2a_app(mock_agent), "/a2a/agent-card")
        assert resp.status_code == 200

    async def test_admin_without_credentials_returns_401(self) -> None:
        _wire_admin_role_service_token()
        resp = await _get(_admin_app(), "/v1/admin/config")
        assert resp.status_code == 401

    async def test_health_endpoints_are_public(self) -> None:
        _wire_admin_role_service_token()
        app = FastAPI()
        app.include_router(health.router)
        for path in ("/health/live", "/health/ready", "/health/startup"):
            resp = await _get(app, path)
            assert resp.status_code in (200, 503)  # never 401


# ---------------------------------------------------------------------------
# Fail-closed: total auth failure -> NOT READY / 503, never a silent open
# ---------------------------------------------------------------------------


class TestFailClosed:
    async def test_auth_init_failure_marks_not_ready_and_returns_503_not_200(self) -> None:
        from forge_gateway.app import _init_auth

        health.set_auth_healthy(True)
        try:
            await _init_auth(None)  # no config at all -> total failure
            assert security.is_auth_healthy() is False

            app = FastAPI()
            app.include_router(health.router)
            health.set_ready(True)
            resp = await _get(app, "/health/ready")
            assert resp.status_code == 503
            assert resp.json()["status"] != "ready"
        finally:
            health.set_auth_healthy(True)
            health.set_ready(False)

    async def test_missing_security_config_defaults_to_enforce_and_denies(
        self, mock_agent: AsyncMock
    ) -> None:
        """A ForgeConfig with no explicit security block still enforces --
        the absence of configuration can never mean the absence of
        authentication (ADR-0001 SS7.1)."""
        from forge_config.schema import ForgeConfig
        from forge_gateway.app import _init_auth

        await _init_auth(ForgeConfig())
        try:
            assert security.is_auth_healthy() is True
            resp = await _post(
                _conversational_app(mock_agent), "/v1/chat/completions", json={"message": "hi"}
            )
            assert resp.status_code == 401
        finally:
            health.set_auth_healthy(True)

    def test_dev_insecure_requires_both_config_and_env_var(self) -> None:
        from forge_config.schema import SecurityConfig

        enforce_cfg = SecurityConfig(auth=SecurityAuthConfig(mode=AuthMode.ENFORCE))
        dev_cfg = SecurityConfig(auth=SecurityAuthConfig(mode=AuthMode.DEV_INSECURE))

        # Neither switch: inactive.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_DEV_INSECURE", None)
            assert security.is_dev_insecure_active(enforce_cfg) is False
            assert security.is_dev_insecure_active(dev_cfg) is False

        # Env var alone (config still says enforce): inactive.
        with patch.dict(os.environ, {"FORGE_DEV_INSECURE": "1"}):
            assert security.is_dev_insecure_active(enforce_cfg) is False

        # Config alone (no env var): inactive.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_DEV_INSECURE", None)
            assert security.is_dev_insecure_active(dev_cfg) is False

        # Both switches: active.
        with patch.dict(os.environ, {"FORGE_DEV_INSECURE": "1"}):
            assert security.is_dev_insecure_active(dev_cfg) is True

    async def test_dev_insecure_sets_x_forge_insecure_mode_header(self) -> None:
        from forge_config.schema import AuthorizationConfig as AuthzCfg
        from forge_gateway.security import DevInsecureHeaderMiddleware

        security.configure_auth(
            session_codec=None,
            service_token_verifier=ServiceTokenVerifier([]),
            oidc_verifier=None,
            authorizer=Authorizer(AuthzCfg()),
            dev_insecure=True,
        )

        app = FastAPI()
        app.include_router(health.router)
        app.add_middleware(DevInsecureHeaderMiddleware)

        resp = await _get(app, "/health/live")
        assert resp.headers.get("x-forge-insecure-mode") == "true"

    async def test_no_insecure_header_when_not_dev_insecure(self) -> None:
        from forge_gateway.security import DevInsecureHeaderMiddleware

        _wire_admin_role_service_token()

        app = FastAPI()
        app.include_router(health.router)
        app.add_middleware(DevInsecureHeaderMiddleware)

        resp = await _get(app, "/health/live")
        assert "x-forge-insecure-mode" not in resp.headers
