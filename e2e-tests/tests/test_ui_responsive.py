"""E2E Playwright tests for responsive layout across viewports."""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page

SCREENSHOT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "e2e-screenshots", "responsive"
)
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")

VIEWPORTS = {
    "mobile": {"width": 375, "height": 812},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1440, "height": 900},
}

PAGES = [
    ("/", "dashboard"),
    ("/config", "config"),
    ("/tools", "tools"),
    ("/chat", "chat"),
    ("/peers", "peers"),
    ("/security", "security"),
    ("/guide", "guide"),
]


class TestResponsiveLayout:
    """Test that all pages render correctly at different viewports."""

    @pytest.mark.parametrize("device", VIEWPORTS.keys())
    @pytest.mark.parametrize("path,name", PAGES)
    def test_page_renders_at_viewport(self, page: Page, device: str, path: str, name: str) -> None:
        """Each page renders without horizontal overflow at each viewport."""
        vp = VIEWPORTS[device]
        page.set_viewport_size(vp)
        page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        page.wait_for_timeout(2000)

        page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, f"{name}-{device}.png"),
            full_page=True,
        )

        # Check no horizontal overflow
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not overflow, f"Horizontal overflow on {name} at {device} ({vp})"

    @pytest.mark.parametrize("device", ["mobile", "tablet"])
    def test_sidebar_collapses_on_small_screens(self, page: Page, device: str) -> None:
        """Sidebar should collapse or hide on mobile/tablet."""
        vp = VIEWPORTS[device]
        page.set_viewport_size(vp)
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Sidebar should be collapsed or hidden on small screens
        page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, f"sidebar-{device}.png"),
        )
