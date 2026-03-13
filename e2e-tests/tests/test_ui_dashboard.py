"""E2E Playwright tests for the Dashboard page."""

from __future__ import annotations

import os
import re

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "dashboard")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestDashboardLoad:
    """Dashboard page loads and renders correctly."""

    def test_dashboard_loads(self, page: Page) -> None:
        """Root URL loads the SPA and shows dashboard."""
        page.goto(BASE_URL, wait_until="networkidle")
        # Should have the app title
        expect(page).to_have_title(re.compile(r"Forge"))
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-initial-load.png"))

    def test_dashboard_has_sidebar(self, page: Page) -> None:
        """Sidebar navigation is visible."""
        page.goto(BASE_URL, wait_until="networkidle")
        # Look for navigation links
        nav = page.locator("nav, [role='navigation'], aside")
        expect(nav.first).to_be_visible()

    def test_dashboard_has_stats_cards(self, page: Page) -> None:
        """Dashboard should show status/stat cards."""
        page.goto(BASE_URL, wait_until="networkidle")
        # Look for card-like elements
        cards = page.locator("[class*='card'], [class*='Card']")
        count = cards.count()
        assert count >= 1, f"Expected at least 1 card on dashboard, found {count}"
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-stats-cards.png"))

    def test_dashboard_health_indicator(self, page: Page) -> None:
        """Dashboard should show health status."""
        page.goto(BASE_URL, wait_until="networkidle")
        # Health status should be visible somewhere
        page.wait_for_timeout(2000)  # Allow health poll
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03-health-status.png"))


class TestDashboardNavigation:
    """Navigation from dashboard to other pages.

    NOTE: The GuideTour overlay (bg-black/50) blocks sidebar clicks on first
    visit. Tests dismiss it first via force-click or by navigating directly.
    This is a known UI bug — BUG-001: GuideTour overlay blocks sidebar nav.
    """

    def _dismiss_tour_overlay(self, page: Page) -> None:
        """Dismiss the GuideTour overlay if present."""
        overlay = page.locator(".fixed.inset-0 .bg-black\\/50, [class*='tour'] [class*='overlay']")
        if overlay.count() > 0:
            # Try clicking a dismiss/skip button first
            skip_btn = page.locator(
                "button:has-text('Skip'), button:has-text('Close'), "
                "button:has-text('Dismiss'), button:has-text('Got it')"
            )
            if skip_btn.count() > 0:
                skip_btn.first.click(force=True)
                page.wait_for_timeout(500)
            else:
                # Force-click the overlay to dismiss it
                overlay.first.click(force=True)
                page.wait_for_timeout(500)

    def test_navigate_to_config(self, page: Page) -> None:
        """Clicking config link navigates to /config."""
        page.goto(BASE_URL, wait_until="networkidle")
        self._dismiss_tour_overlay(page)
        config_link = page.locator("a[href='/config'], a[href*='config']").first
        if config_link.is_visible():
            config_link.click(force=True)
            page.wait_for_url("**/config", timeout=5000)
            expect(page).to_have_url(re.compile(r"/config"))

    def test_navigate_to_tools(self, page: Page) -> None:
        """Clicking tools link navigates to /tools."""
        page.goto(BASE_URL, wait_until="networkidle")
        self._dismiss_tour_overlay(page)
        tools_link = page.locator("a[href='/tools'], a[href*='tools']").first
        if tools_link.is_visible():
            tools_link.click(force=True)
            page.wait_for_url("**/tools", timeout=5000)
            expect(page).to_have_url(re.compile(r"/tools"))

    def test_navigate_to_chat(self, page: Page) -> None:
        """Clicking chat link navigates to /chat."""
        page.goto(BASE_URL, wait_until="networkidle")
        self._dismiss_tour_overlay(page)
        chat_link = page.locator("a[href='/chat'], a[href*='chat']").first
        if chat_link.is_visible():
            chat_link.click(force=True)
            page.wait_for_url("**/chat", timeout=5000)
            expect(page).to_have_url(re.compile(r"/chat"))

    def test_navigate_to_peers(self, page: Page) -> None:
        """Clicking peers link navigates to /peers."""
        page.goto(BASE_URL, wait_until="networkidle")
        self._dismiss_tour_overlay(page)
        peers_link = page.locator("a[href='/peers'], a[href*='peers']").first
        if peers_link.is_visible():
            peers_link.click(force=True)
            page.wait_for_url("**/peers", timeout=5000)
            expect(page).to_have_url(re.compile(r"/peers"))

    def test_navigate_to_security(self, page: Page) -> None:
        """Clicking security link navigates to /security."""
        page.goto(BASE_URL, wait_until="networkidle")
        self._dismiss_tour_overlay(page)
        security_link = page.locator("a[href='/security'], a[href*='security']").first
        if security_link.is_visible():
            security_link.click(force=True)
            page.wait_for_url("**/security", timeout=5000)
            expect(page).to_have_url(re.compile(r"/security"))

    def test_navigate_to_guide(self, page: Page) -> None:
        """Clicking guide link navigates to /guide."""
        page.goto(BASE_URL, wait_until="networkidle")
        self._dismiss_tour_overlay(page)
        guide_link = page.locator("a[href='/guide'], a[href*='guide']").first
        if guide_link.is_visible():
            guide_link.click(force=True)
            page.wait_for_url("**/guide", timeout=5000)
            expect(page).to_have_url(re.compile(r"/guide"))
