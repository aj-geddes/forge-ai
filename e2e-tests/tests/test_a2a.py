"""E2E tests for Agent-to-Agent (A2A) endpoints."""

from __future__ import annotations

import httpx


class TestAgentCard:
    """GET /a2a/agent-card - agent discovery."""

    def test_agent_card_returns_200(self, client: httpx.Client) -> None:
        response = client.get("/a2a/agent-card")
        assert response.status_code == 200

    def test_agent_card_default_values(self, client: httpx.Client) -> None:
        """Default card should have name='forge' and a description."""
        data = client.get("/a2a/agent-card").json()
        assert data["name"] == "forge"
        assert data["description"] == "Forge AI Agent"
        assert isinstance(data["capabilities"], list)
        assert data["version"] == "0.1.0"
        assert "endpoint" in data

    def test_agent_card_response_schema(self, client: httpx.Client) -> None:
        """All required fields present with correct types."""
        data = client.get("/a2a/agent-card").json()
        assert isinstance(data["name"], str)
        assert isinstance(data["description"], str)
        assert isinstance(data["capabilities"], list)
        assert isinstance(data["version"], str)
        assert isinstance(data["endpoint"], str)

    def test_agent_card_is_idempotent(self, client: httpx.Client) -> None:
        """Multiple calls should return the same card."""
        card1 = client.get("/a2a/agent-card").json()
        card2 = client.get("/a2a/agent-card").json()
        assert card1 == card2

    def test_agent_card_content_type(self, client: httpx.Client) -> None:
        response = client.get("/a2a/agent-card")
        assert "application/json" in response.headers["content-type"]


class TestA2ATaskSubmission:
    """POST /a2a/tasks - task submission."""

    def test_submit_task_returns_failed_status(self, client: httpx.Client) -> None:
        """Without LLM, task returns 200 with failed status in body."""
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "search", "payload": {"query": "test"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"

    def test_submit_task_validates_missing_task_type(self, client: httpx.Client) -> None:
        """Missing required 'task_type' field returns 422."""
        response = client.post(
            "/a2a/tasks",
            json={"payload": {}},
        )
        assert response.status_code == 422

    def test_submit_task_accepts_full_request(self, client: httpx.Client) -> None:
        """Full A2A request accepted."""
        response = client.post(
            "/a2a/tasks",
            json={
                "task_type": "analyze",
                "payload": {"data": [1, 2, 3]},
                "caller_id": "external-agent-xyz",
            },
        )
        assert response.status_code == 200

    def test_submit_task_minimal_request(self, client: httpx.Client) -> None:
        """Minimal request with just task_type."""
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "ping"},
        )
        assert response.status_code == 200

    def test_submit_task_rejects_non_json(self, client: httpx.Client) -> None:
        response = client.post(
            "/a2a/tasks",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
