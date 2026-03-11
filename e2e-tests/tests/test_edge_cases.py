"""E2E tests for edge cases, error handling, and robustness."""

from __future__ import annotations

import httpx


class TestLargePayloads:
    """Test handling of unusual or large payloads."""

    def test_large_intent_string(self, client: httpx.Client) -> None:
        """Very long intent string should be accepted (503 from no agent)."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "x" * 10000},
        )
        assert response.status_code == 503

    def test_large_params_dict(self, client: httpx.Client) -> None:
        """Large params dict should be accepted."""
        big_params = {f"key_{i}": f"value_{i}" for i in range(100)}
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "test", "params": big_params},
        )
        assert response.status_code == 503

    def test_deeply_nested_payload(self, client: httpx.Client) -> None:
        """Deeply nested payload in A2A."""
        nested = {"level": 0}
        current = nested
        for i in range(1, 50):
            current["child"] = {"level": i}
            current = current["child"]
        response = client.post(
            "/a2a/tasks",
            json={"task_type": "nested", "payload": nested},
        )
        assert response.status_code == 503

    def test_large_message(self, client: httpx.Client) -> None:
        """Large chat message should be accepted."""
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Hello " * 5000},
        )
        assert response.status_code == 503


class TestSpecialCharacters:
    """Test special characters in various fields."""

    def test_unicode_intent(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "Translate: こんにちは世界 to English"},
        )
        assert response.status_code == 503

    def test_unicode_message(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/chat/completions",
            json={"message": "Bonjour le monde! 🌍"},
        )
        assert response.status_code == 503

    def test_html_in_intent(self, client: httpx.Client) -> None:
        """HTML in intent should be accepted (not rendered)."""
        response = client.post(
            "/v1/agent/invoke",
            json={"intent": "<script>alert('xss')</script>"},
        )
        assert response.status_code == 503

    def test_special_chars_in_a2a_caller_id(self, client: httpx.Client) -> None:
        response = client.post(
            "/a2a/tasks",
            json={
                "task_type": "test",
                "caller_id": "agent://special/id?with=params&more=stuff",
            },
        )
        assert response.status_code == 503


class TestConcurrentRequests:
    """Test concurrent request handling."""

    def test_concurrent_health_checks(self, client: httpx.Client) -> None:
        """Multiple sequential health checks should all succeed."""
        for _ in range(20):
            response = client.get("/health/live")
            assert response.status_code == 200

    def test_mixed_endpoints_sequential(self, client: httpx.Client) -> None:
        """Mix of different endpoints in sequence."""
        endpoints = [
            ("GET", "/health/live", None),
            ("GET", "/health/ready", None),
            ("GET", "/a2a/agent-card", None),
            ("GET", "/metrics", None),
            ("POST", "/v1/agent/invoke", {"intent": "test"}),
            ("POST", "/v1/chat/completions", {"message": "hi"}),
            ("POST", "/a2a/tasks", {"task_type": "ping"}),
            ("GET", "/health/startup", None),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                response = client.get(path)
            else:
                response = client.post(path, json=body)
            assert response.status_code in (200, 503), (
                f"{method} {path} returned unexpected {response.status_code}"
            )
