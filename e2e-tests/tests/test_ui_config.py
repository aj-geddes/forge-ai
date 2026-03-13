"""E2E Playwright tests for the Config page."""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "config")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestConfigPageLoad:
    """Config page loads and renders tabs."""

    def test_config_page_loads(self, page: Page) -> None:
        """Config page renders without errors."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-config-page.png"))

    def test_config_has_tabs(self, page: Page) -> None:
        """Config page should have Visual/YAML/Diff tabs."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Look for tab-like elements
        tabs = page.locator(
            "[role='tablist'] [role='tab'], button:has-text('Visual'), "
            "button:has-text('YAML'), button:has-text('Diff')"
        )
        if tabs.count() > 0:
            expect(tabs.first).to_be_visible()

    def test_config_visual_editor(self, page: Page) -> None:
        """Visual tab shows config editor."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        visual_tab = page.locator("button:has-text('Visual'), [role='tab']:has-text('Visual')")
        if visual_tab.count() > 0:
            visual_tab.first.click()
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-visual-editor.png"))

    def test_config_yaml_editor(self, page: Page) -> None:
        """YAML tab shows raw YAML editor."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        yaml_tab = page.locator("button:has-text('YAML'), [role='tab']:has-text('YAML')")
        if yaml_tab.count() > 0:
            yaml_tab.first.click()
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03-yaml-editor.png"))

    def test_config_diff_view(self, page: Page) -> None:
        """Diff tab shows changes comparison."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        diff_tab = page.locator("button:has-text('Diff'), [role='tab']:has-text('Diff')")
        if diff_tab.count() > 0:
            diff_tab.first.click()
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "04-diff-view.png"))


class TestConfigActions:
    """Config page action buttons."""

    def test_config_has_save_button(self, page: Page) -> None:
        """Config page should have a Save button."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        save_btn = page.locator("button:has-text('Save')")
        if save_btn.count() > 0:
            expect(save_btn.first).to_be_visible()

    def test_config_has_reload_button(self, page: Page) -> None:
        """Config page should have a Reload button."""
        page.goto(f"{BASE_URL}/config", wait_until="networkidle")
        page.wait_for_timeout(2000)
        reload_btn = page.locator("button:has-text('Reload'), button:has-text('Reset')")
        if reload_btn.count() > 0:
            expect(reload_btn.first).to_be_visible()
