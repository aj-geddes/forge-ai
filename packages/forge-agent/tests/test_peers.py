"""Tests for PeerCaller A2A communication."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from forge_agent.agent.peers import (
    PeerCaller,
    PeerCallError,
    PeerNotFoundError,
)
from forge_config.schema import PeerAgent, TrustLevel


def _make_peer(
    name: str = "test-peer",
    endpoint: str = "https://peer.example.com",
    trust_level: TrustLevel = TrustLevel.HIGH,
    capabilities: list[str] | None = None,
) -> PeerAgent:
    """Create a PeerAgent config for testing."""
    return PeerAgent(
        name=name,
        endpoint=endpoint,
        trust_level=trust_level,
        capabilities=capabilities or ["query", "report"],
    )


def _make_success_response() -> httpx.Response:
    """Create a mock httpx.Response with a successful A2A result."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {
        "status": "completed",
        "result": {"answer": 42},
        "error": None,
    }
    response.raise_for_status = MagicMock()
    return response


def _make_error_response(status_code: int = 500, text: str = "Internal Server Error") -> Any:
    """Create a mock httpx.Response that raises on raise_for_status."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message=text,
        request=MagicMock(spec=httpx.Request),
        response=response,
    )
    return response


class TestPeerCallerInit:
    """Tests for PeerCaller initialization and peer lookup."""

    def test_peer_names(self) -> None:
        peers = [_make_peer("alpha"), _make_peer("beta")]
        caller = PeerCaller(peers)
        assert sorted(caller.peer_names) == ["alpha", "beta"]

    def test_get_peer_found(self) -> None:
        peer = _make_peer("data-forge")
        caller = PeerCaller([peer])
        result = caller.get_peer("data-forge")
        assert result.name == "data-forge"

    def test_get_peer_not_found_raises(self) -> None:
        caller = PeerCaller([_make_peer("alpha")])
        with pytest.raises(PeerNotFoundError, match="Peer agent 'missing'"):
            caller.get_peer("missing")

    def test_empty_peers(self) -> None:
        caller = PeerCaller([])
        assert caller.peer_names == []
        with pytest.raises(PeerNotFoundError):
            caller.get_peer("any")


class TestPeerCallerCallPeer:
    """Tests for PeerCaller.call_peer with mocked httpx."""

    @pytest.mark.anyio
    async def test_successful_call(self) -> None:
        peer = _make_peer("data-forge", endpoint="https://data-forge.example.com")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_success_response()

        caller = PeerCaller([peer], caller_id="my-forge", http_client=mock_client)
        result = await caller.call_peer("data-forge", "data_query", {"sql": "SELECT 1"})

        assert result.status == "completed"
        assert result.result == {"answer": 42}
        assert result.error is None

        # Verify the POST was made to the correct URL.
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://data-forge.example.com/a2a/tasks"

        # Verify the request body.
        body = call_args[1]["json"]
        assert body["task_type"] == "data_query"
        assert body["payload"] == {"sql": "SELECT 1"}
        assert body["caller_id"] == "my-forge"

    @pytest.mark.anyio
    async def test_call_with_none_payload(self) -> None:
        peer = _make_peer("data-forge")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_success_response()

        caller = PeerCaller([peer], http_client=mock_client)
        result = await caller.call_peer("data-forge", "ping")

        assert result.status == "completed"
        body = mock_client.post.call_args[1]["json"]
        assert body["payload"] == {}

    @pytest.mark.anyio
    async def test_call_unknown_peer_raises(self) -> None:
        caller = PeerCaller([_make_peer("alpha")])
        with pytest.raises(PeerNotFoundError, match="'missing'"):
            await caller.call_peer("missing", "task", {})

    @pytest.mark.anyio
    async def test_call_http_error_raises_peer_call_error(self) -> None:
        peer = _make_peer("data-forge")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_error_response(500, "Server Error")

        caller = PeerCaller([peer], http_client=mock_client)
        with pytest.raises(PeerCallError, match="HTTP 500"):
            await caller.call_peer("data-forge", "task", {})

    @pytest.mark.anyio
    async def test_call_network_error_raises_peer_call_error(self) -> None:
        peer = _make_peer("data-forge")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")

        caller = PeerCaller([peer], http_client=mock_client)
        with pytest.raises(PeerCallError, match="Failed to call peer"):
            await caller.call_peer("data-forge", "task", {})

    @pytest.mark.anyio
    async def test_endpoint_trailing_slash_handled(self) -> None:
        peer = _make_peer("data-forge", endpoint="https://data-forge.example.com/")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_success_response()

        caller = PeerCaller([peer], http_client=mock_client)
        await caller.call_peer("data-forge", "task", {})

        url = mock_client.post.call_args[0][0]
        assert url == "https://data-forge.example.com/a2a/tasks"


class TestPeerCallerBuildTools:
    """Tests for PeerCaller.build_tools."""

    def test_builds_tools_for_each_peer(self) -> None:
        peers = [
            _make_peer("data-forge", capabilities=["data_query", "reporting"]),
            _make_peer("security-forge", capabilities=["threat_analysis"]),
        ]
        caller = PeerCaller(peers)
        tools = caller.build_tools()

        assert len(tools) == 2
        tool_names = [t.name for t in tools]
        assert "peer_data_forge" in tool_names
        assert "peer_security_forge" in tool_names

    def test_tool_names_sanitize_hyphens(self) -> None:
        peer = _make_peer("my-cool-peer")
        caller = PeerCaller([peer])
        tools = caller.build_tools()
        assert tools[0].name == "peer_my_cool_peer"

    def test_empty_peers_builds_no_tools(self) -> None:
        caller = PeerCaller([])
        tools = caller.build_tools()
        assert tools == []

    @pytest.mark.anyio
    async def test_tool_execution_delegates_to_call_peer(self) -> None:
        peer = _make_peer("data-forge")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_success_response()

        caller = PeerCaller([peer], caller_id="test", http_client=mock_client)
        tools = caller.build_tools()

        # Find the tool and call its function directly.
        tool = tools[0]
        assert tool.name == "peer_data_forge"
        assert tool.takes_ctx is False

        # Execute the underlying function (takes_ctx=False, so no ctx arg).
        result = await tool.function(
            task_type="data_query",
            payload={"sql": "SELECT 1"},
        )

        assert result["status"] == "completed"
        assert result["result"] == {"answer": 42}

        # Verify the HTTP call was made.
        mock_client.post.assert_called_once()

    @pytest.mark.anyio
    async def test_tool_execution_with_default_payload(self) -> None:
        peer = _make_peer("data-forge")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_success_response()

        caller = PeerCaller([peer], http_client=mock_client)
        tools = caller.build_tools()
        tool = tools[0]

        # No ctx arg since takes_ctx=False.
        result = await tool.function(
            task_type="ping",
        )

        assert result["status"] == "completed"
        body = mock_client.post.call_args[1]["json"]
        assert body["payload"] == {}


class TestPeerCallerInRegistry:
    """Tests that peer tools integrate with the ToolSurfaceRegistry."""

    @pytest.mark.anyio
    async def test_registry_builds_peer_tools(self) -> None:
        from forge_agent.builder.registry import ToolSurfaceRegistry
        from forge_config.schema import AgentsConfig, ForgeConfig

        config = ForgeConfig(
            agents=AgentsConfig(
                peers=[
                    PeerAgent(
                        name="data-forge",
                        endpoint="https://data-forge.example.com",
                        trust_level=TrustLevel.HIGH,
                        capabilities=["data_query"],
                    ),
                ]
            )
        )

        registry = ToolSurfaceRegistry()
        await registry.build_and_swap(config)

        tool_names = [t.name for t in registry.tools]
        assert "peer_data_forge" in tool_names

    @pytest.mark.anyio
    async def test_registry_no_peers_no_peer_tools(self) -> None:
        from forge_agent.builder.registry import ToolSurfaceRegistry
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        registry = ToolSurfaceRegistry()
        await registry.build_and_swap(config)

        tool_names = [t.name for t in registry.tools]
        assert not any(name.startswith("peer_") for name in tool_names)
