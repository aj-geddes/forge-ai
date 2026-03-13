"""Tests for conversational endpoint including SSE streaming support."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from forge_agent.agent.core import ForgeRunResult
from forge_config.schema import AgentDef, AgentsConfig, ForgeConfig, LLMConfig
from forge_gateway.routes import conversational
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_iter(*items: str) -> AsyncIterator[str]:
    """Create an async iterator from a sequence of strings."""
    for item in items:
        yield item


async def _async_iter_error(*items: str, error: Exception | None = None) -> AsyncIterator[str]:
    """Async iterator that yields items, then raises an exception."""
    for item in items:
        yield item
    if error is not None:
        raise error


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_agent() -> AsyncMock:
    """Mock agent that returns a ForgeRunResult for non-streaming calls."""
    agent = AsyncMock()
    agent.run_conversational.return_value = ForgeRunResult(
        output="Hello! How can I help?",
    )
    return agent


@pytest.fixture()
def app(mock_agent: AsyncMock) -> FastAPI:
    """FastAPI app wired up with the conversational router and mock agent."""
    _app = FastAPI()
    _app.include_router(conversational.router)
    conversational.set_agent(mock_agent)
    conversational.set_config(None)
    yield _app  # type: ignore[misc]
    conversational.set_agent(None)
    conversational.set_config(None)


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    """Synchronous test client for non-streaming tests."""
    return TestClient(app)


@pytest.fixture()
async def async_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async test client for streaming tests (httpx.AsyncClient)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _parse_sse_events(text: str) -> list[str]:
    """Split raw SSE text into individual event lines, filtering blanks."""
    return [ln for ln in text.split("\n\n") if ln.strip()]


def _parse_sse_payload(event_line: str) -> dict:
    """Parse a 'data: {json}' line into a dict."""
    return json.loads(event_line.removeprefix("data: "))


# ---------------------------------------------------------------------------
# 1. stream=False (default): returns normal JSON response
# ---------------------------------------------------------------------------


class TestNonStreamingChat:
    """Existing non-streaming behaviour is preserved when stream=False (default)."""

    def test_chat_success(self, client: TestClient, mock_agent: AsyncMock) -> None:
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi there"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Hello! How can I help?"
        assert data["session_id"]  # Auto-generated

    def test_chat_with_session(self, client: TestClient) -> None:
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "session_id": "sess-123"},
        )
        assert response.status_code == 200
        assert response.json()["session_id"] == "sess-123"

    def test_chat_no_agent(self) -> None:
        app = FastAPI()
        app.include_router(conversational.router)
        conversational.set_agent(None)
        tc = TestClient(app)
        response = tc.post("/v1/chat/completions", json={"message": "Hi"})
        assert response.status_code == 503

    def test_chat_agent_error(self, client: TestClient, mock_agent: AsyncMock) -> None:
        mock_agent.run_conversational.side_effect = RuntimeError("LLM timeout")
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi"},
        )
        assert response.status_code == 500

    def test_stream_false_explicit(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """Explicitly passing stream=False uses the normal JSON path."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hello", "stream": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Hello! How can I help?"
        mock_agent.run_conversational.assert_called_once_with(
            message="Hello",
            session_id=data["session_id"],
            system_prompt_override=None,
            model_name_override=None,
        )

    def test_non_streaming_content_type(self, client: TestClient) -> None:
        """Non-streaming responses return application/json."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi"},
        )
        assert "application/json" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# 2. stream=True: returns SSE with text/event-stream content type
# ---------------------------------------------------------------------------


class TestStreamingContentType:
    """Streaming responses use the correct SSE content type."""

    async def test_streaming_content_type(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("Hello")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers["content-type"].startswith("text/event-stream")

    async def test_streaming_calls_agent_with_stream_flag(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """The agent is called with stream=True when the request asks for streaming."""
        mock_agent.run_conversational.return_value = _async_iter("chunk")
        await async_client.post(
            "/v1/chat/completions",
            json={"message": "Tell me a joke", "stream": True, "session_id": "s-flag"},
        )
        mock_agent.run_conversational.assert_called_once_with(
            message="Tell me a joke",
            session_id="s-flag",
            stream=True,
            system_prompt_override=None,
            model_name_override=None,
        )

    def test_streaming_content_type_sync(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """Verify content type via sync TestClient as well."""
        mock_agent.run_conversational.return_value = _async_iter("Hello")
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")


# ---------------------------------------------------------------------------
# 3. stream=True: each chunk formatted as data: {json}\n\n
# ---------------------------------------------------------------------------


class TestSSEChunkFormat:
    """Each streamed chunk follows the SSE data line protocol."""

    async def test_chunk_is_json_data_line(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("Hello")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        events = _parse_sse_events(response.text)
        # First event is a data chunk, last is [DONE]
        chunk_line = events[0]
        assert chunk_line.startswith("data: ")

        payload = _parse_sse_payload(chunk_line)
        assert payload["chunk"] == "Hello"

    async def test_chunk_contains_session_id(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("Hi")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True, "session_id": "sess-42"},
        )

        events = _parse_sse_events(response.text)
        payload = _parse_sse_payload(events[0])
        assert payload["session_id"] == "sess-42"

    async def test_all_data_lines_contain_valid_json(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """All data lines (except [DONE]) contain valid JSON with a 'chunk' key."""
        mock_agent.run_conversational.return_value = _async_iter("a", "b", "c")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        for line in _parse_sse_events(response.text):
            raw = line.removeprefix("data: ")
            if raw == "[DONE]":
                continue
            parsed = json.loads(raw)
            assert "chunk" in parsed

    def test_chunk_format_via_sync_client(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """SSE format validated with sync TestClient."""
        mock_agent.run_conversational.return_value = _async_iter("Hello", " world")
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True, "session_id": "s1"},
        )
        events = _parse_sse_events(response.text)
        assert len(events) == 3  # two chunks + [DONE]

        chunk0 = _parse_sse_payload(events[0])
        assert chunk0["chunk"] == "Hello"
        assert chunk0["session_id"] == "s1"

        chunk1 = _parse_sse_payload(events[1])
        assert chunk1["chunk"] == " world"


# ---------------------------------------------------------------------------
# 4. stream=True: stream ends with data: [DONE]\n\n
# ---------------------------------------------------------------------------


class TestSSEDoneSentinel:
    """Every stream terminates with a [DONE] sentinel."""

    async def test_stream_ends_with_done(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("Hello", "World")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        assert response.text.rstrip().endswith("data: [DONE]")

    async def test_done_sentinel_exact_format(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """The sentinel is exactly 'data: [DONE]\\n\\n'."""
        mock_agent.run_conversational.return_value = _async_iter("x")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        assert response.text.endswith("data: [DONE]\n\n")

    async def test_done_is_last_event(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """[DONE] is always the very last event in the stream."""
        mock_agent.run_conversational.return_value = _async_iter("a", "b")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        events = _parse_sse_events(response.text)
        assert events[-1] == "data: [DONE]"


# ---------------------------------------------------------------------------
# 5. stream=True: SSE headers present (Cache-Control, Connection)
# ---------------------------------------------------------------------------


class TestSSEHeaders:
    """Streaming responses include SSE-required headers."""

    async def test_cache_control_header(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("hi")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers.get("cache-control") == "no-cache"

    async def test_connection_header(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("hi")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers.get("connection") == "keep-alive"

    async def test_x_accel_buffering_header(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """X-Accel-Buffering: no prevents nginx buffering of SSE."""
        mock_agent.run_conversational.return_value = _async_iter("hi")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers.get("x-accel-buffering") == "no"

    def test_sse_headers_via_sync_client(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """SSE headers present when verified via sync TestClient."""
        mock_agent.run_conversational.return_value = _async_iter("Hi")
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"


# ---------------------------------------------------------------------------
# 6. Error during streaming: error event sent and stream closes
# ---------------------------------------------------------------------------


class TestStreamingErrors:
    """Errors encountered mid-stream are delivered as error events."""

    async def test_error_during_iteration_sends_error_event(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """If the async iterator raises, an error data event is emitted."""
        mock_agent.run_conversational.return_value = _async_iter_error(
            "partial", error=RuntimeError("LLM connection lost")
        )
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        events = _parse_sse_events(response.text)
        # Should have: partial chunk, error chunk, [DONE]
        assert len(events) == 3

        first = _parse_sse_payload(events[0])
        assert first["chunk"] == "partial"

        error_event = _parse_sse_payload(events[1])
        assert "error" in error_event
        assert "LLM connection lost" in error_event["error"]

        assert events[2] == "data: [DONE]"

    async def test_error_event_contains_session_id(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter_error(
            error=ValueError("bad input")
        )
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True, "session_id": "err-sess"},
        )

        events = _parse_sse_events(response.text)
        error_event = _parse_sse_payload(events[0])
        assert error_event["session_id"] == "err-sess"

    async def test_stream_closes_after_error(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """After an error event, only [DONE] follows -- no more data."""
        mock_agent.run_conversational.return_value = _async_iter_error(
            "a", "b", error=RuntimeError("boom")
        )
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        events = _parse_sse_events(response.text)
        # "a", "b", error, [DONE]
        assert len(events) == 4
        assert events[-1] == "data: [DONE]"

        error_payload = _parse_sse_payload(events[-2])
        assert "error" in error_payload

    async def test_agent_startup_error_returns_500(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """If the agent raises before streaming starts, return HTTP 500."""
        mock_agent.run_conversational.side_effect = RuntimeError("Init failed")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.status_code == 500

    async def test_no_agent_returns_503_for_stream(self) -> None:
        """stream=True with no agent configured returns 503."""
        app = FastAPI()
        app.include_router(conversational.router)
        conversational.set_agent(None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi", "stream": True},
            )
            assert response.status_code == 503

    def test_error_during_iteration_via_sync_client(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Sync TestClient: error mid-stream sends error event + [DONE]."""

        async def _failing_stream() -> AsyncIterator[str]:
            yield "partial"
            raise RuntimeError("mid-stream failure")

        mock_agent.run_conversational.return_value = _failing_stream()
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True, "session_id": "s3"},
        )
        events = _parse_sse_events(response.text)
        assert len(events) == 3

        error_event = _parse_sse_payload(events[1])
        assert "error" in error_event
        assert "mid-stream failure" in error_event["error"]

        assert events[2] == "data: [DONE]"


# ---------------------------------------------------------------------------
# 7. Empty stream: just sends [DONE]
# ---------------------------------------------------------------------------


class TestEmptyStream:
    """An agent that yields no chunks still sends a valid [DONE] sentinel."""

    async def test_empty_stream_sends_done(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter()
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        assert response.text.strip() == "data: [DONE]"

    async def test_empty_stream_content_type(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """Even an empty stream uses text/event-stream."""
        mock_agent.run_conversational.return_value = _async_iter()
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers["content-type"].startswith("text/event-stream")

    async def test_empty_stream_has_sse_headers(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter()
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )
        assert response.headers.get("cache-control") == "no-cache"

    async def test_empty_stream_no_data_chunks(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """Empty stream has exactly one event: [DONE]."""
        mock_agent.run_conversational.return_value = _async_iter()
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        events = _parse_sse_events(response.text)
        assert len(events) == 1
        assert events[0] == "data: [DONE]"


# ---------------------------------------------------------------------------
# 8. Multiple chunks: all received in order
# ---------------------------------------------------------------------------


class TestMultipleChunks:
    """Multiple chunks from the agent are delivered in the correct order."""

    async def test_chunks_arrive_in_order(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        mock_agent.run_conversational.return_value = _async_iter("Once", " upon", " a", " time")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Tell me a story", "stream": True},
        )

        events = _parse_sse_events(response.text)
        # 4 chunks + [DONE]
        assert len(events) == 5

        chunks = [_parse_sse_payload(ev)["chunk"] for ev in events[:-1]]
        assert chunks == ["Once", " upon", " a", " time"]

    async def test_many_chunks(self, async_client: AsyncClient, mock_agent: AsyncMock) -> None:
        """Verify correctness with a large number of chunks."""
        expected = [f"word_{i}" for i in range(50)]
        mock_agent.run_conversational.return_value = _async_iter(*expected)
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Go", "stream": True},
        )

        events = _parse_sse_events(response.text)
        # 50 chunks + [DONE]
        assert len(events) == 51

        received = [_parse_sse_payload(ev)["chunk"] for ev in events[:-1]]
        assert received == expected

    async def test_chunks_preserve_whitespace_and_special_chars(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """Chunks with special characters and whitespace are preserved via JSON encoding."""
        special = ['{"key": "value"}', "  spaces  ", "tab\there"]
        mock_agent.run_conversational.return_value = _async_iter(*special)
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Special", "stream": True},
        )

        events = _parse_sse_events(response.text)
        received = [_parse_sse_payload(ev)["chunk"] for ev in events[:-1]]
        assert received == special

    async def test_session_id_consistent_across_chunks(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """All chunks in a stream carry the same session_id."""
        mock_agent.run_conversational.return_value = _async_iter("a", "b", "c")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True, "session_id": "fixed-sess"},
        )

        events = _parse_sse_events(response.text)
        for ev in events[:-1]:
            payload = _parse_sse_payload(ev)
            assert payload["session_id"] == "fixed-sess"

    async def test_auto_generated_session_id_in_stream(
        self, async_client: AsyncClient, mock_agent: AsyncMock
    ) -> None:
        """When no session_id is provided, an auto-generated one appears in all chunks."""
        mock_agent.run_conversational.return_value = _async_iter("hello", "world")
        response = await async_client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True},
        )

        events = _parse_sse_events(response.text)
        session_ids = set()
        for ev in events[:-1]:
            payload = _parse_sse_payload(ev)
            sid = payload["session_id"]
            assert sid  # Non-empty
            session_ids.add(sid)

        # All chunks share the same auto-generated session id
        assert len(session_ids) == 1

    def test_multiple_chunks_via_sync_client(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Sync TestClient: multiple chunks arrive in order."""
        mock_agent.run_conversational.return_value = _async_iter("Hello", " world")
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi", "stream": True, "session_id": "sync-s"},
        )
        events = _parse_sse_events(response.text)
        assert len(events) == 3  # 2 chunks + [DONE]

        chunk0 = _parse_sse_payload(events[0])
        assert chunk0["chunk"] == "Hello"
        assert chunk0["session_id"] == "sync-s"

        chunk1 = _parse_sse_payload(events[1])
        assert chunk1["chunk"] == " world"
        assert chunk1["session_id"] == "sync-s"

        assert events[2] == "data: [DONE]"


# ---------------------------------------------------------------------------
# 9. tools_used and model fields in conversational responses
# ---------------------------------------------------------------------------


class TestConversationalToolsUsedAndModel:
    """Verify tools_used and model fields are populated in chat responses."""

    def test_chat_includes_tools_used_when_tools_called(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When tools are invoked, tools_used contains their names."""
        mock_agent.run_conversational.return_value = ForgeRunResult(
            output="Here are the results.",
            tools_used=["web_search", "summarize"],
            model_name="gpt-4o",
        )
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Search for something"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == ["web_search", "summarize"]

    def test_chat_includes_model_string(self, client: TestClient, mock_agent: AsyncMock) -> None:
        """The model field reflects the LLM model used for the run."""
        mock_agent.run_conversational.return_value = ForgeRunResult(
            output="Response text.",
            model_name="claude-3-opus-20240229",
        )
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "claude-3-opus-20240229"

    def test_chat_empty_tools_used_when_no_tools_called(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When no tools are invoked, tools_used is an empty list."""
        mock_agent.run_conversational.return_value = ForgeRunResult(
            output="Just a plain answer.",
            tools_used=[],
        )
        response = client.post(
            "/v1/chat/completions",
            json={"message": "What is 2+2?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == []

    def test_chat_model_none_when_not_available(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """When no model info is available, model is None."""
        mock_agent.run_conversational.return_value = ForgeRunResult(
            output="Answer.",
            model_name=None,
        )
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] is None

    def test_chat_tools_used_and_model_together(
        self, client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Both tools_used and model are present in the same response."""
        mock_agent.run_conversational.return_value = ForgeRunResult(
            output="Found and analyzed the data.",
            tools_used=["fetch_data", "analyze"],
            model_name="gpt-4o-mini",
        )
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Analyze this"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Found and analyzed the data."
        assert data["tools_used"] == ["fetch_data", "analyze"]
        assert data["model"] == "gpt-4o-mini"

    def test_chat_default_fixture_has_empty_tools_and_no_model(self, client: TestClient) -> None:
        """The default mock agent fixture returns empty tools_used and no model."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hi"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools_used"] == []
        assert data["model"] is None


# ---------------------------------------------------------------------------
# 10. Agent persona routing
# ---------------------------------------------------------------------------


class TestChatPersonaRouting:
    """Tests for agent persona routing in the chat endpoint."""

    @pytest.fixture
    def config_with_personas(self) -> ForgeConfig:
        return ForgeConfig(
            llm=LLMConfig(default_model="gpt-4o"),
            agents=AgentsConfig(
                agents=[
                    AgentDef(
                        name="coder",
                        description="A coding assistant",
                        system_prompt="You are a coding assistant.",
                        model="gpt-4o-mini",
                    ),
                    AgentDef(
                        name="writer",
                        description="A writing assistant",
                        system_prompt="You are a creative writer.",
                    ),
                ]
            ),
        )

    @pytest.fixture
    def persona_client(
        self, mock_agent: AsyncMock, config_with_personas: ForgeConfig
    ) -> TestClient:
        app = FastAPI()
        app.include_router(conversational.router)
        conversational.set_agent(mock_agent)
        conversational.set_config(config_with_personas)
        yield TestClient(app)
        conversational.set_agent(None)
        conversational.set_config(None)

    def test_chat_with_known_persona(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Chatting with a known persona passes overrides to the agent."""
        response = persona_client.post(
            "/v1/chat/completions",
            json={"message": "Help me code", "agent": "coder"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding assistant."
        assert call_kwargs["model_name_override"] == "gpt-4o-mini"

    def test_chat_with_unknown_persona_returns_404(self, persona_client: TestClient) -> None:
        """Chatting with an unknown persona returns 404."""
        response = persona_client.post(
            "/v1/chat/completions",
            json={"message": "Hello", "agent": "nonexistent"},
        )
        assert response.status_code == 404
        assert "Unknown agent persona" in response.json()["detail"]

    def test_chat_without_persona_uses_defaults(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Chatting without agent field passes None overrides."""
        response = persona_client.post(
            "/v1/chat/completions",
            json={"message": "Hello"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs.get("system_prompt_override") is None
        assert call_kwargs.get("model_name_override") is None

    def test_chat_persona_without_model_override(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """A persona with no model override passes None for model_name_override."""
        response = persona_client.post(
            "/v1/chat/completions",
            json={"message": "Write a story", "agent": "writer"},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a creative writer."
        assert call_kwargs["model_name_override"] is None

    def test_chat_persona_no_config_returns_404(self, mock_agent: AsyncMock) -> None:
        """When no config is loaded, any persona name returns 404."""
        app = FastAPI()
        app.include_router(conversational.router)
        conversational.set_agent(mock_agent)
        conversational.set_config(None)
        tc = TestClient(app)
        response = tc.post(
            "/v1/chat/completions",
            json={"message": "Hi", "agent": "coder"},
        )
        assert response.status_code == 404
        conversational.set_agent(None)

    def test_chat_streaming_with_persona(
        self, persona_client: TestClient, mock_agent: AsyncMock
    ) -> None:
        """Streaming with a persona passes the overrides correctly."""
        mock_agent.run_conversational.return_value = _async_iter("Hello")
        response = persona_client.post(
            "/v1/chat/completions",
            json={"message": "Code it", "agent": "coder", "stream": True},
        )
        assert response.status_code == 200

        call_kwargs = mock_agent.run_conversational.call_args.kwargs
        assert call_kwargs["system_prompt_override"] == "You are a coding assistant."
        assert call_kwargs["model_name_override"] == "gpt-4o-mini"
