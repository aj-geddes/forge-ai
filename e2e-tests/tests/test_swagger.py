"""E2E tests for Swagger UI and OpenAPI spec."""

from __future__ import annotations

import httpx
from playwright.sync_api import Page, expect


class TestOpenAPISpec:
    """Validate the auto-generated OpenAPI specification."""

    def test_openapi_json_returns_200(self, client: httpx.Client) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_openapi_json_valid_spec(self, client: httpx.Client) -> None:
        spec = client.get("/openapi.json").json()
        assert spec["info"]["title"] == "Forge AI Gateway"
        assert spec["info"]["version"] == "0.1.0"
        assert "paths" in spec

    def test_openapi_contains_all_endpoints(self, client: httpx.Client) -> None:
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        expected_paths = [
            "/health/live",
            "/health/ready",
            "/health/startup",
            "/v1/agent/invoke",
            "/v1/chat/completions",
            "/a2a/agent-card",
            "/a2a/tasks",
            "/metrics",
        ]
        for path in expected_paths:
            assert path in paths, f"Missing path: {path}"

    def test_openapi_schemas_present(self, client: httpx.Client) -> None:
        spec = client.get("/openapi.json").json()
        schemas = spec.get("components", {}).get("schemas", {})
        expected_schemas = [
            "InvokeRequest",
            "InvokeResponse",
            "ConversationRequest",
            "ConversationResponse",
            "HealthResponse",
            "AgentCard",
            "A2ATaskRequest",
            "A2ATaskResponse",
        ]
        for schema_name in expected_schemas:
            assert schema_name in schemas, f"Missing schema: {schema_name}"

    def test_openapi_tags(self, client: httpx.Client) -> None:
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]

        # Verify tag assignments
        assert "health" in paths["/health/live"]["get"].get("tags", [])
        assert "programmatic" in paths["/v1/agent/invoke"]["post"].get("tags", [])
        assert "conversational" in paths["/v1/chat/completions"]["post"].get("tags", [])
        assert "a2a" in paths["/a2a/agent-card"]["get"].get("tags", [])
        assert "metrics" in paths["/metrics"]["get"].get("tags", [])


class TestSwaggerUI:
    """Browser-based Swagger UI tests using Playwright."""

    def test_swagger_ui_loads(self, page: Page) -> None:
        """Swagger UI at /docs should render the API title."""
        page.goto("https://forge-ai.hvs/docs")
        expect(page.locator("h1")).to_contain_text("Forge AI Gateway")
        page.screenshot(path="e2e-screenshots/swagger/01-swagger-loaded.png")

    def test_swagger_shows_all_tag_groups(self, page: Page) -> None:
        """All 5 tag groups should be visible."""
        page.goto("https://forge-ai.hvs/docs")
        for tag in ["health", "programmatic", "conversational", "a2a", "metrics"]:
            locator = page.get_by_role("link", name=tag, exact=True)
            expect(locator).to_be_visible()

    def test_swagger_expand_health_live(self, page: Page) -> None:
        """Clicking a health endpoint should expand its details."""
        page.goto("https://forge-ai.hvs/docs")
        page.get_by_role("button", name="GET /health/live Liveness").click()
        # After expanding, a "Try it out" button should appear
        expect(page.get_by_role("button", name="Try it out")).to_be_visible()
        page.screenshot(path="e2e-screenshots/swagger/02-health-live-expanded.png")

    def test_swagger_try_health_live(self, page: Page) -> None:
        """Execute the /health/live endpoint from Swagger UI."""
        page.goto("https://forge-ai.hvs/docs")
        page.get_by_role("button", name="GET /health/live Liveness").click()
        page.get_by_role("button", name="Try it out").click()
        page.get_by_role("button", name="Execute").click()

        # Wait for the response body to appear (Swagger renders it in a highlight block)
        page.wait_for_selector(".responses-inner .response .microlight", timeout=5000)
        response_blocks = page.locator(".responses-inner .response .microlight")
        # The response body is typically the last microlight block
        found = False
        for i in range(response_blocks.count()):
            text = response_blocks.nth(i).inner_text()
            if '"status"' in text:
                assert '"ok"' in text
                found = True
                break
        assert found, "Could not find response body with status in Swagger UI"
        page.screenshot(path="e2e-screenshots/swagger/03-health-live-executed.png")

    def test_redoc_loads(self, page: Page) -> None:
        """ReDoc at /redoc should render."""
        page.goto("https://forge-ai.hvs/redoc")
        expect(page.locator("h1")).to_contain_text("Forge AI Gateway")
        page.screenshot(path="e2e-screenshots/swagger/04-redoc.png")


class TestSwaggerResponsive:
    """Responsive layout tests for Swagger UI."""

    VIEWPORTS = {
        "mobile": {"width": 375, "height": 812},
        "tablet": {"width": 768, "height": 1024},
        "desktop": {"width": 1440, "height": 900},
    }

    def test_swagger_mobile_viewport(self, page: Page) -> None:
        page.set_viewport_size(self.VIEWPORTS["mobile"])
        page.goto("https://forge-ai.hvs/docs")
        expect(page.locator("h1")).to_be_visible()
        page.screenshot(
            path="e2e-screenshots/responsive/swagger-mobile.png",
            full_page=True,
        )

    def test_swagger_tablet_viewport(self, page: Page) -> None:
        page.set_viewport_size(self.VIEWPORTS["tablet"])
        page.goto("https://forge-ai.hvs/docs")
        expect(page.locator("h1")).to_be_visible()
        page.screenshot(
            path="e2e-screenshots/responsive/swagger-tablet.png",
            full_page=True,
        )

    def test_swagger_desktop_viewport(self, page: Page) -> None:
        page.set_viewport_size(self.VIEWPORTS["desktop"])
        page.goto("https://forge-ai.hvs/docs")
        expect(page.locator("h1")).to_be_visible()
        page.screenshot(
            path="e2e-screenshots/responsive/swagger-desktop.png",
            full_page=True,
        )
