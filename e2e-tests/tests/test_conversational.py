"""E2E tests for conversational chat endpoint."""

from __future__ import annotations

import httpx


class TestChatEndpoint:
    """POST /v1/chat/completions - conversational chat."""

    def test_chat_returns_503_when_no_agent(self, client: httpx.Client) -> None:
        """Without agent, chat returns 503."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hello!"},
        )
        assert response.status_code in (500, 503)

    def test_chat_validates_missing_message(self, client: httpx.Client) -> None:
        """Missing required 'message' field returns 422."""
        response = client.post(
            "/v1/chat/completions",
            json={},
        )
        assert response.status_code == 422

    def test_chat_accepts_full_request(self, client: httpx.Client) -> None:
        """All optional fields accepted (even if 500)."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "message": "Tell me about AI",
                "session_id": "session-001",
                "stream": False,
                "agent": "assistant",
            },
        )
        assert response.status_code in (500, 503)

    def test_chat_rejects_non_json(self, client: httpx.Client) -> None:
        """Non-JSON body returns 422."""
        response = client.post(
            "/v1/chat/completions",
            content=b"plain text",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422

    def test_chat_stream_string_coerced_to_bool(self, client: httpx.Client) -> None:
        """Pydantic coerces truthy strings to bool for stream field."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "test", "stream": "yes"},
        )
        # "yes" is coerced to True by Pydantic, streaming starts (200) even if agent fails
        assert response.status_code in (200, 500, 503)

    def test_chat_stream_invalid_type_rejected(self, client: httpx.Client) -> None:
        """Non-coercible type for stream returns 422."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "test", "stream": [1, 2, 3]},
        )
        assert response.status_code == 422

    def test_chat_empty_message_accepted(self, client: httpx.Client) -> None:
        """Empty string message is accepted (no min_length validator)."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": ""},
        )
        # Should be 503 (no agent), not 422
        assert response.status_code in (500, 503)
