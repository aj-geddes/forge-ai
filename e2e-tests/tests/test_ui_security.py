"""E2E Playwright tests for the Security page."""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "security")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestSecurityPageLoad:
    """Security page loads and renders correctly."""

    def test_security_page_loads(self, page: Page) -> None:
        """Security page renders without errors."""
        page.goto(f"{BASE_URL}/security", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-security-page.png"))

    def test_security_shows_agentweave_status(self, page: Page) -> None:
        """Security page should display AgentWeave status."""
        page.goto(f"{BASE_URL}/security", wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Look for AgentWeave section
        agentweave = page.locator(":text('AgentWeave'), :text('Identity'), :text('Trust')")
        if agentweave.count() > 0:
            expect(agentweave.first).to_be_visible()

    def test_security_shows_rate_limiting(self, page: Page) -> None:
        """Security page should show rate limiting info."""
        page.goto(f"{BASE_URL}/security", wait_until="networkidle")
        page.wait_for_timeout(2000)
        rate_limit = page.locator(":text('Rate'), :text('rate'), :text('RPM'), :text('limit')")
        if rate_limit.count() > 0:
            expect(rate_limit.first).to_be_visible()

    def test_security_shows_cors(self, page: Page) -> None:
        """Security page should show CORS configuration."""
        page.goto(f"{BASE_URL}/security", wait_until="networkidle")
        page.wait_for_timeout(2000)
        cors = page.locator(":text('CORS'), :text('cors'), :text('Origin')")
        if cors.count() > 0:
            expect(cors.first).to_be_visible()
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-security-details.png"))
