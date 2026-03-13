"""Tests for A2A endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import ForgeConfig, ForgeMetadata
from forge_gateway.routes import a2a
from forge_gateway.routes.a2a import AgentCard, build_agent_card


@pytest.fixture
def mock_agent() -> AsyncMock:
    agent = AsyncMock()
    agent.run_structured.return_value = ForgeRunResult(output={"processed": True})
    return agent


@pytest.fixture
def client(mock_agent: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(a2a.router)
    a2a.set_agent(mock_agent)
    yield TestClient(app)
    a2a.set_agent(None)
    a2a.set_agent_card(None)


@pytest.fixture(autouse=True)
def _reset_agent_card() -> Iterator[None]:
    """Reset the module-level agent card after each test."""
    yield
    a2a.set_agent_card(None)


class TestAgentCard:
    def test_default_card(self, client: TestClient) -> None:
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "forge"

    def test_custom_card(self, client: TestClient) -> None:
        a2a.set_agent_card(
            AgentCard(
                name="my-agent",
                description="Custom agent",
                capabilities=["search", "analyze"],
            )
        )
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "my-agent"
        assert "search" in data["capabilities"]


class TestA2ATask:
    def test_submit_task(self, client: TestClient) -> None:
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "search", "payload": {"q": "test"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"] == {"processed": True}

    def test_task_failure(self, client: TestClient, mock_agent: AsyncMock) -> None:
        mock_agent.run_structured.side_effect = RuntimeError("Agent error")
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "fail", "payload": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Agent error" in data["error"]

    def test_submit_task_returns_503_when_no_agent(self) -> None:
        """POST /a2a/tasks returns 503 when the agent is not initialized."""
        app = FastAPI()
        app.include_router(a2a.router)
        a2a.set_agent(None)

        client = TestClient(app)
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "search", "payload": {}},
        )
        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"]


# ---------------------------------------------------------------------------
# build_agent_card() — coverage for lines 50-80 and _has_mcp_support (83-90)
# ---------------------------------------------------------------------------


class TestBuildAgentCard:
    """Tests for the build_agent_card function (covers missed lines 56-80, 85-90)."""

    def test_build_with_non_forge_config_returns_default(self) -> None:
        """When config is not a ForgeConfig, return the default card."""
        card = build_agent_card("not-a-config")
        assert card.name == "forge"
        assert card.description == "Forge AI Agent"
        assert card.capabilities == []

    def test_build_with_full_config(self) -> None:
        """build_agent_card should populate all fields from a ForgeConfig."""
        config = ForgeConfig(
            metadata=ForgeMetadata(
                name="my-forge-agent",
                description="A custom agent for testing",
                version="1.2.3",
            ),
        )
        card = build_agent_card(config)

        assert card.name == "my-forge-agent"
        assert card.description == "A custom agent for testing"
        assert card.version == "1.2.3"
        assert "a2a" in card.protocols
        assert "rest" in card.protocols

    def test_build_uses_name_fallback_for_description(self) -> None:
        """When metadata.description is empty, use '<name> AI Agent' fallback."""
        config = ForgeConfig(
            metadata=ForgeMetadata(
                name="fallback-test",
                description="",
                version="0.5.0",
            ),
        )
        card = build_agent_card(config)

        assert card.description == "fallback-test AI Agent"

    def test_build_with_agent_tools(self) -> None:
        """build_agent_card should extract tool names from the agent registry."""
        from forge_agent import ForgeAgent

        config = ForgeConfig(
            metadata=ForgeMetadata(name="tooled-agent"),
        )

        # Create a mock that passes isinstance(agent, ForgeAgent) checks
        mock_tool_1 = MagicMock()
        mock_tool_1.name = "web_search"
        mock_tool_2 = MagicMock()
        mock_tool_2.name = "file_read"

        mock_registry = MagicMock()
        mock_registry.tools = [mock_tool_1, mock_tool_2]

        mock_agent = MagicMock(spec=ForgeAgent)
        mock_agent.registry = mock_registry

        card = build_agent_card(config, agent=mock_agent)

        assert "web_search" in card.capabilities
        assert "file_read" in card.capabilities
        assert len(card.capabilities) == 2

    def test_build_without_agent_has_empty_capabilities(self) -> None:
        """build_agent_card should return empty capabilities when agent is None."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="no-agent"),
        )
        card = build_agent_card(config, agent=None)

        assert card.capabilities == []

    def test_build_with_non_forge_agent_has_empty_capabilities(self) -> None:
        """build_agent_card should return empty capabilities for non-ForgeAgent."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="wrong-agent"),
        )
        card = build_agent_card(config, agent="not-an-agent")

        assert card.capabilities == []

    def test_build_uses_forge_gateway_url_env(self) -> None:
        """build_agent_card should read the endpoint from FORGE_GATEWAY_URL env var."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="env-test"),
        )
        with patch.dict("os.environ", {"FORGE_GATEWAY_URL": "https://my-forge.example.com"}):
            card = build_agent_card(config)

        assert card.endpoint == "https://my-forge.example.com"

    def test_build_uses_default_endpoint_when_env_not_set(self) -> None:
        """build_agent_card should default to localhost:8000 when env is not set."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="default-endpoint"),
        )
        with patch.dict("os.environ", {}, clear=False):
            # Ensure FORGE_GATEWAY_URL is not set
            import os

            env_without_url = {k: v for k, v in os.environ.items() if k != "FORGE_GATEWAY_URL"}
            with patch.dict("os.environ", env_without_url, clear=True):
                card = build_agent_card(config)

        assert card.endpoint == "http://localhost:8000"

    def test_build_detects_mcp_support(self) -> None:
        """build_agent_card should include 'mcp' in protocols when fastmcp is importable."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="mcp-agent"),
        )
        with patch("forge_gateway.routes.a2a._has_mcp_support", return_value=True):
            card = build_agent_card(config)

        assert "mcp" in card.protocols
        assert "a2a" in card.protocols
        assert "rest" in card.protocols

    def test_build_excludes_mcp_when_not_available(self) -> None:
        """build_agent_card should not include 'mcp' when fastmcp is not importable."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="no-mcp-agent"),
        )
        with patch("forge_gateway.routes.a2a._has_mcp_support", return_value=False):
            card = build_agent_card(config)

        assert "mcp" not in card.protocols
        assert "a2a" in card.protocols
        assert "rest" in card.protocols


class TestHasMcpSupport:
    """Tests for the _has_mcp_support helper (covers lines 85-90)."""

    def test_returns_true_when_fastmcp_importable(self) -> None:
        """_has_mcp_support should return True when fastmcp can be imported."""
        from forge_gateway.routes.a2a import _has_mcp_support

        # fastmcp is installed in our test environment
        result = _has_mcp_support()
        assert result is True

    def test_returns_false_when_fastmcp_not_importable(self) -> None:
        """_has_mcp_support should return False when fastmcp import fails."""
        import builtins

        from forge_gateway.routes.a2a import _has_mcp_support

        original_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "fastmcp":
                raise ImportError("No module named 'fastmcp'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = _has_mcp_support()
            assert result is False


class TestAgentCardEndpointWithSetCard:
    """Test the GET /a2a/agent-card endpoint after set_agent_card is called."""

    def test_returns_populated_card_after_set(self) -> None:
        """GET /a2a/agent-card should return the card set via set_agent_card."""
        app = FastAPI()
        app.include_router(a2a.router)

        card = AgentCard(
            name="populated-agent",
            description="Fully configured agent",
            capabilities=["tool_a", "tool_b", "tool_c"],
            version="2.0.0",
            endpoint="https://forge.example.com",
            protocols=["a2a", "rest", "mcp"],
        )
        a2a.set_agent_card(card)

        client = TestClient(app)
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "populated-agent"
        assert data["description"] == "Fully configured agent"
        assert data["capabilities"] == ["tool_a", "tool_b", "tool_c"]
        assert data["version"] == "2.0.0"
        assert data["endpoint"] == "https://forge.example.com"
        assert data["protocols"] == ["a2a", "rest", "mcp"]

    def test_returns_default_card_when_none_set(self) -> None:
        """GET /a2a/agent-card should return default card when none is set."""
        app = FastAPI()
        app.include_router(a2a.router)
        a2a.set_agent_card(None)

        client = TestClient(app)
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "forge"
        assert data["description"] == "Forge AI Agent"
