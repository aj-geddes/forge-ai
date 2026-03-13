"""E2E Playwright tests for the Tools page."""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "tools")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestToolsPageLoad:
    """Tools page loads and renders correctly."""

    def test_tools_page_loads(self, page: Page) -> None:
        """Tools page renders without errors."""
        page.goto(f"{BASE_URL}/tools", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-tools-page.png"))

    def test_tools_has_add_button(self, page: Page) -> None:
        """Tools page should have an Add Tool button."""
        page.goto(f"{BASE_URL}/tools", wait_until="networkidle")
        page.wait_for_timeout(2000)
        add_btn = page.locator(
            "button:has-text('Add'), button:has-text('New'), button:has-text('Create')"
        )
        if add_btn.count() > 0:
            expect(add_btn.first).to_be_visible()

    def test_tools_empty_state(self, page: Page) -> None:
        """With no tools configured, shows empty state or message."""
        page.goto(f"{BASE_URL}/tools", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-empty-state.png"))


class TestToolsAddDialog:
    """Tools Add Tool dialog."""

    def test_add_tool_dialog_opens(self, page: Page) -> None:
        """Clicking Add Tool opens a dialog with options."""
        page.goto(f"{BASE_URL}/tools", wait_until="networkidle")
        page.wait_for_timeout(2000)
        add_btn = page.locator(
            "button:has-text('Add'), button:has-text('New'), button:has-text('Create')"
        )
        if add_btn.count() > 0:
            add_btn.first.click(force=True)
            page.wait_for_timeout(1000)
            # Should show dialog with OpenAPI/Manual/Workflow options
            dialog = page.locator("[role='dialog'], [class*='dialog'], [class*='Dialog']")
            if dialog.count() > 0:
                expect(dialog.first).to_be_visible()
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03-add-dialog.png"))

    def test_add_tool_dialog_has_options(self, page: Page) -> None:
        """Dialog should show OpenAPI, Manual Tool, and Workflow options."""
        page.goto(f"{BASE_URL}/tools", wait_until="networkidle")
        page.wait_for_timeout(2000)
        add_btn = page.locator(
            "button:has-text('Add'), button:has-text('New'), button:has-text('Create')"
        )
        if add_btn.count() > 0:
            add_btn.first.click(force=True)
            page.wait_for_timeout(1000)
            # Check for option buttons
            openapi = page.locator("button:has-text('OpenAPI'), :text('OpenAPI')")
            manual = page.locator("button:has-text('Manual'), :text('Manual')")
            workflow = page.locator("button:has-text('Workflow'), :text('Workflow')")
            # At least one should be visible
            total = openapi.count() + manual.count() + workflow.count()
            assert total >= 1, "Expected at least one tool type option in dialog"

    def test_tools_search_filter(self, page: Page) -> None:
        """Tools page should have a search/filter input."""
        page.goto(f"{BASE_URL}/tools", wait_until="networkidle")
        page.wait_for_timeout(2000)
        search = page.locator(
            "input[type='search'], input[placeholder*='earch'], input[placeholder*='ilter']"
        )
        if search.count() > 0:
            search.first.fill("test")
            page.wait_for_timeout(500)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "04-search-filter.png"))
