"""Tests for the FastAPI app factory."""

from forge_gateway.app import create_app


class TestCreateApp:
    def test_app_creation(self) -> None:
        app = create_app()
        assert app.title == "Forge AI Gateway"

    def test_routes_registered(self) -> None:
        app = create_app()
        paths = [r.path for r in app.routes]
        assert "/health/live" in paths
        assert "/health/ready" in paths
        assert "/v1/agent/invoke" in paths
        assert "/v1/chat/completions" in paths
        assert "/a2a/agent-card" in paths
        assert "/metrics" in paths
