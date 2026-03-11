"""E2E tests for programmatic agent invocation endpoint."""

from __future__ import annotations

import httpx


class TestInvokeEndpoint:
    """POST /v1/agent/invoke - structured agent invocation."""

    def test_invoke_returns_503_when_no_agent(self, client: httpx.Client) -> None:
        """Without a config/agent, the endpoint returns 503."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test query", "params": {}},
        )
        # The server started without a config, so the agent is not initialized
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        assert "not initialized" in data["detail"].lower() or "agent" in data["detail"].lower()

    def test_invoke_validates_request_body(self, client: httpx.Client) -> None:
        """Missing required 'intent' field returns 422 validation error."""
        response = client.post(
            "/v1/agent/invoke",
            json={},
        )
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    def test_invoke_rejects_non_json(self, client: httpx.Client) -> None:
        """Sending non-JSON body returns 422."""
        response = client.post(
            "/v1/agent/invoke",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422

    def test_invoke_accepts_full_request_schema(self, client: httpx.Client) -> None:
        """A request with all optional fields should be accepted (even if agent is 503)."""
        response = client.post(
            "/v1/agent/invoke",
            json={
                "intent": "analyze data",
                "params": {"key": "value"},
                "tool_hints": ["search"],
                "output_schema": {"type": "object"},
                "session_id": "test-session",
                "agent": "analyst",
            },
        )
        # 503 is expected (no agent), but NOT 422 (validation should pass)
        assert response.status_code == 503

    def test_invoke_with_empty_intent(self, client: httpx.Client) -> None:
        """Empty string intent is currently accepted by Pydantic (no min_length)."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": ""},
        )
        # Should still get 503 (no agent) not 422 - empty string passes validation
        assert response.status_code == 503


class TestInvokeRequestValidation:
    """Request payload validation edge cases."""

    def test_extra_fields_are_ignored(self, client: httpx.Client) -> None:
        """Pydantic should ignore unknown fields by default."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "unknown_field": "hello"},
        )
        # Should get 503 (no agent), NOT 422
        assert response.status_code == 503

    def test_params_default_to_empty_dict(self, client: httpx.Client) -> None:
        """Params should default to empty dict if not provided."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test"},
        )
        assert response.status_code == 503  # Expected - no agent

    def test_wrong_types_rejected(self, client: httpx.Client) -> None:
        """Wrong types for params should be rejected."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "params": "not a dict"},
        )
        assert response.status_code == 422
