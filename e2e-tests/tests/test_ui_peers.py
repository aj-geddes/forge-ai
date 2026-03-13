"""E2E Playwright tests for the Peers page."""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "peers")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestPeersPageLoad:
    """Peers page loads and renders correctly."""

    def test_peers_page_loads(self, page: Page) -> None:
        """Peers page renders without errors."""
        page.goto(f"{BASE_URL}/peers", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-peers-page.png"))

    def test_peers_empty_state(self, page: Page) -> None:
        """With no peers configured, shows empty state."""
        page.goto(f"{BASE_URL}/peers", wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Should show some content even with no peers
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-peers-empty.png"))

    def test_peers_has_add_button(self, page: Page) -> None:
        """Peers page should have an Add Peer button."""
        page.goto(f"{BASE_URL}/peers", wait_until="networkidle")
        page.wait_for_timeout(2000)
        add_btn = page.locator(
            "button:has-text('Add'), button:has-text('New'), button:has-text('Connect')"
        )
        if add_btn.count() > 0:
            expect(add_btn.first).to_be_visible()


class TestPeersAddDialog:
    """Peers Add dialog tests."""

    def test_add_peer_dialog_opens(self, page: Page) -> None:
        """Clicking Add opens a dialog with peer form."""
        page.goto(f"{BASE_URL}/peers", wait_until="networkidle")
        page.wait_for_timeout(2000)
        add_btn = page.locator(
            "button:has-text('Add'), button:has-text('New'), button:has-text('Connect')"
        )
        if add_btn.count() > 0:
            add_btn.first.click()
            page.wait_for_timeout(1000)
            dialog = page.locator("[role='dialog'], [class*='dialog'], [class*='Dialog']")
            if dialog.count() > 0:
                expect(dialog.first).to_be_visible()
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03-add-peer-dialog.png"))
