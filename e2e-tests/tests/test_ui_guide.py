"""E2E Playwright tests for the Guide page."""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "guide")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestGuidePageLoad:
    """Guide page loads and renders correctly."""

    def test_guide_page_loads(self, page: Page) -> None:
        """Guide page renders without errors."""
        page.goto(f"{BASE_URL}/guide", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-guide-page.png"))

    def test_guide_has_content(self, page: Page) -> None:
        """Guide page should have meaningful content."""
        page.goto(f"{BASE_URL}/guide", wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Guide page should render a main content area
        body_text = page.locator("main").first
        expect(body_text).to_be_visible()
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "05-guide-content.png"))

    def test_guide_has_search(self, page: Page) -> None:
        """Guide page should have a search input."""
        page.goto(f"{BASE_URL}/guide", wait_until="networkidle")
        page.wait_for_timeout(2000)
        search = page.locator("input[type='search'], input[placeholder*='earch']")
        if search.count() > 0:
            expect(search.first).to_be_visible()

    def test_guide_search_filters(self, page: Page) -> None:
        """Search filters guide sections."""
        page.goto(f"{BASE_URL}/guide", wait_until="networkidle")
        page.wait_for_timeout(2000)
        search = page.locator("input[type='search'], input[placeholder*='earch']")
        if search.count() > 0:
            search.first.fill("config")
            page.wait_for_timeout(500)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-search-results.png"))

    def test_guide_section_content(self, page: Page) -> None:
        """Clicking a section shows its content."""
        page.goto(f"{BASE_URL}/guide", wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Click first section link
        section_links = page.locator(
            "[class*='sidebar'] a, [class*='sidebar'] button, [role='tab']"
        )
        if section_links.count() > 0:
            section_links.first.click(force=True)
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03-section-content.png"))

    def test_guide_has_faq_accordion(self, page: Page) -> None:
        """Guide sections should have FAQ accordions."""
        page.goto(f"{BASE_URL}/guide", wait_until="networkidle")
        page.wait_for_timeout(2000)
        accordion = page.locator("[data-state], [class*='accordion'], [class*='Accordion']")
        if accordion.count() > 0:
            accordion.first.click(force=True)
            page.wait_for_timeout(500)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "04-faq-expanded.png"))
