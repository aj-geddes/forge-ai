"""End-to-end proof that the MCP surface actually works at runtime.

These tests exercise the *real* ``create_app()`` + ``lifespan()`` wiring
(not a hand-rolled test app) over an ``httpx.ASGITransport``, to prove:

1. The gateway's own lifespan actually runs the mounted FastMCP ASGI app's
   lifespan, so ``tools/list`` genuinely works. Before the fix, FastMCP's
   streamable-HTTP session manager was never started, and every call to
   ``/mcp`` failed with "Task group is not initialized."
2. The ``/mcp`` mount enforces the same bypass-free ``tools:invoke``
   authorization used by the programmatic (``/v1``) routes (ADR-0001) --
   it does not bypass auth just because it's a raw ASGI mount.
3. Rebuilding the tool surface (e.g. via config hot-reload) is actually
   reflected by the live ``/mcp`` endpoint, not just an inert module-level
   reference.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from forge_agent.agent.core import ForgeAgent
from forge_config.schema import (
    ForgeConfig,
    ForgeMetadata,
    SecurityConfig,
    ServiceToken,
    ServiceTokenConfig,
)
from forge_gateway.app import create_app, lifespan
from forge_gateway.routes import mcp as mcp_module
from pydantic_ai.tools import Tool as PydanticAITool

SERVICE_TOKEN = "forge_sk_e2e-tester_abcdefghijklmnopqrstuvwxyz0123456789ABCD"  # noqa: S105
_SERVICE_TOKEN_DIGEST = hashlib.sha256(SERVICE_TOKEN.encode("utf-8")).hexdigest()
AUTH_HEADERS = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def _greet(name: str) -> str:
    """Greet a person by name."""
    return f"Hello, {name}!"


def _farewell(name: str) -> str:
    """Say goodbye to a person by name."""
    return f"Goodbye, {name}!"


GREET_TOOL = PydanticAITool(_greet, name="greet", description="Greet a person by name")
FAREWELL_TOOL = PydanticAITool(
    _farewell, name="farewell", description="Say goodbye to a person by name"
)


def _make_fake_initialize(tools: list[PydanticAITool[None]]) -> Any:
    """Build a replacement ``ForgeAgent.initialize`` that seeds a fixed tool set.

    Avoids exercising the real tool-building pipeline (OpenAPI fetches, LLM
    client construction, etc.) so these tests stay hermetic and fast while
    still running through a genuine ``ForgeAgent`` instance (so the
    ``isinstance(agent, ForgeAgent)`` check in ``_init_mcp_server`` passes
    naturally, without needing to monkeypatch the class itself).
    """

    async def _fake_initialize(self: ForgeAgent) -> None:
        await self._registry.force_swap(list(tools), "test-version")

    return _fake_initialize


def _service_token_config() -> ForgeConfig:
    """A config authenticated via a single admin-scoped service token, with
    OIDC disabled entirely (a legal, machine-only ADR-0001 deployment)."""
    return ForgeConfig(
        metadata=ForgeMetadata(name="e2e-test"),
        security=SecurityConfig(
            oidc={"enabled": False},
            service_tokens=ServiceTokenConfig(
                enabled=True,
                tokens=[
                    ServiceToken(
                        id="e2e-tester", secret_sha256=_SERVICE_TOKEN_DIGEST, roles=["admin"]
                    )
                ],
            ),
            allowed_origins=["https://forgeai.hvslocal"],
        ),
    )


def _asgi_http_client_factory(app: Any) -> Any:
    """Build an ``httpx_client_factory`` for FastMCP's client transport that
    talks directly to *app* in-process via ``httpx.ASGITransport``, instead
    of opening a real network socket."""

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            **kwargs,
        )

    return factory


@pytest.fixture(autouse=True)
def _reset_security_and_mcp_state() -> Any:
    from forge_gateway import security

    yield
    security.reset_auth()
    mcp_module._mcp_server = None
    mcp_module._active_asgi_app = None
    mcp_module._active_lifespan = None


class TestMCPServesToolsOverRealASGILifespan:
    """Proves the gateway's lifespan actually starts the FastMCP ASGI app.

    This is the test that fails against the pre-fix code: mounting the
    FastMCP ASGI app without running its lifespan leaves its streamable-HTTP
    session manager task group uninitialized, and every request to /mcp
    raises a RuntimeError.
    """

    async def test_tools_list_returns_configured_tools(self, tmp_path: Any) -> None:
        config = _service_token_config()
        config_path = tmp_path / "forge.yaml"
        config_path.write_text("metadata:\n  name: e2e-test\n")

        with (
            patch.dict(os.environ, {"FORGE_CONFIG_PATH": str(config_path)}),
            patch("forge_config.load_config", return_value=config),
            patch.object(ForgeAgent, "initialize", _make_fake_initialize([GREET_TOOL])),
        ):
            app = create_app()
            async with lifespan(app):
                transport = StreamableHttpTransport(
                    url="http://testserver/mcp/",
                    headers=AUTH_HEADERS,
                    httpx_client_factory=_asgi_http_client_factory(app),
                )
                client = Client(transport)
                async with client:
                    tools = await client.list_tools()

        names = {t.name for t in tools}
        assert "greet" in names

    async def test_tool_call_invokes_underlying_function(self, tmp_path: Any) -> None:
        """A real tools/call over the live MCP endpoint executes the tool."""
        config = _service_token_config()
        config_path = tmp_path / "forge.yaml"
        config_path.write_text("metadata:\n  name: e2e-test\n")

        with (
            patch.dict(os.environ, {"FORGE_CONFIG_PATH": str(config_path)}),
            patch("forge_config.load_config", return_value=config),
            patch.object(ForgeAgent, "initialize", _make_fake_initialize([GREET_TOOL])),
        ):
            app = create_app()
            async with lifespan(app):
                transport = StreamableHttpTransport(
                    url="http://testserver/mcp/",
                    headers=AUTH_HEADERS,
                    httpx_client_factory=_asgi_http_client_factory(app),
                )
                client = Client(transport)
                async with client:
                    result = await client.call_tool("greet", {"name": "World"})

        assert "Hello, World!" in result.content[0].text


class TestMCPMountEnforcesAuthentication:
    """The /mcp mount rejects unauthenticated calls exactly like /v1 routes do."""

    async def test_rejects_unauthenticated_request(self, tmp_path: Any) -> None:
        config = _service_token_config()
        config_path = tmp_path / "forge.yaml"
        config_path.write_text("metadata:\n  name: e2e-test\n")

        with (
            patch.dict(os.environ, {"FORGE_CONFIG_PATH": str(config_path)}),
            patch("forge_config.load_config", return_value=config),
            patch.object(ForgeAgent, "initialize", _make_fake_initialize([GREET_TOOL])),
        ):
            app = create_app()
            async with lifespan(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as ac:
                    resp = await ac.post("/mcp/", json={})

        assert resp.status_code == 401

    async def test_rejects_wrong_service_token(self, tmp_path: Any) -> None:
        config = _service_token_config()
        config_path = tmp_path / "forge.yaml"
        config_path.write_text("metadata:\n  name: e2e-test\n")

        with (
            patch.dict(os.environ, {"FORGE_CONFIG_PATH": str(config_path)}),
            patch("forge_config.load_config", return_value=config),
            patch.object(ForgeAgent, "initialize", _make_fake_initialize([GREET_TOOL])),
        ):
            app = create_app()
            async with lifespan(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as ac:
                    resp = await ac.post(
                        "/mcp/",
                        json={},
                        headers={"Authorization": "Bearer forge_sk_wrong_00000000000000000"},
                    )

        assert resp.status_code == 401

    async def test_allows_request_with_valid_service_token(self, tmp_path: Any) -> None:
        config = _service_token_config()
        config_path = tmp_path / "forge.yaml"
        config_path.write_text("metadata:\n  name: e2e-test\n")

        with (
            patch.dict(os.environ, {"FORGE_CONFIG_PATH": str(config_path)}),
            patch("forge_config.load_config", return_value=config),
            patch.object(ForgeAgent, "initialize", _make_fake_initialize([GREET_TOOL])),
        ):
            app = create_app()
            async with lifespan(app):
                transport = StreamableHttpTransport(
                    url="http://testserver/mcp/",
                    headers=AUTH_HEADERS,
                    httpx_client_factory=_asgi_http_client_factory(app),
                )
                client = Client(transport)
                async with client:
                    tools = await client.list_tools()

        assert {t.name for t in tools} == {"greet"}


class TestMCPRebuildReflectsLiveSurface:
    """A tool-surface rebuild is actually reflected by the live /mcp endpoint."""

    async def test_rebuild_and_activate_changes_what_mcp_serves(self, tmp_path: Any) -> None:
        config = _service_token_config()
        config_path = tmp_path / "forge.yaml"
        config_path.write_text("metadata:\n  name: e2e-test\n")

        with (
            patch.dict(os.environ, {"FORGE_CONFIG_PATH": str(config_path)}),
            patch("forge_config.load_config", return_value=config),
            patch.object(ForgeAgent, "initialize", _make_fake_initialize([GREET_TOOL])),
        ):
            app = create_app()
            async with lifespan(app):
                transport = StreamableHttpTransport(
                    url="http://testserver/mcp/",
                    headers=AUTH_HEADERS,
                    httpx_client_factory=_asgi_http_client_factory(app),
                )

                client = Client(transport)
                async with client:
                    before = {t.name for t in await client.list_tools()}

                new_registry = AsyncMock()
                new_registry.tools = [FAREWELL_TOOL]
                await mcp_module.rebuild_and_activate(new_registry)

                client_after = Client(transport)
                async with client_after:
                    after = {t.name for t in await client_after.list_tools()}

        assert before == {"greet"}
        assert after == {"farewell"}
