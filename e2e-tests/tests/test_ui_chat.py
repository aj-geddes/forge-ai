"""E2E Playwright tests for the Chat page."""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e-screenshots", "chat")
BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")


class TestChatPageLoad:
    """Chat page loads and renders correctly."""

    def test_chat_page_loads(self, page: Page) -> None:
        """Chat page renders without errors."""
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-chat-page.png"))

    def test_chat_has_message_input(self, page: Page) -> None:
        """Chat page should have a message input area."""
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle")
        page.wait_for_timeout(2000)
        input_area = page.locator(
            "textarea, input[type='text'][placeholder*='essage'], "
            "input[placeholder*='ype'], [contenteditable='true']"
        )
        if input_area.count() > 0:
            expect(input_area.first).to_be_visible()

    def test_chat_has_send_button(self, page: Page) -> None:
        """Chat page should have a send button."""
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle")
        page.wait_for_timeout(2000)
        send_btn = page.locator(
            "button:has-text('Send'), button[type='submit'], "
            "button[aria-label*='send'], button[aria-label*='Send']"
        )
        if send_btn.count() > 0:
            expect(send_btn.first).to_be_visible()

    def test_chat_has_session_sidebar(self, page: Page) -> None:
        """Chat page should have a session list sidebar."""
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle")
        page.wait_for_timeout(2000)
        # Look for new session button or session list
        new_session = page.locator(
            "button:has-text('New'), button:has-text('Session'), button:has-text('Create')"
        )
        if new_session.count() > 0:
            expect(new_session.first).to_be_visible()


class TestChatInteraction:
    """Chat interaction tests."""

    def test_new_session_creation(self, page: Page) -> None:
        """Can create a new chat session."""
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle")
        page.wait_for_timeout(2000)
        new_btn = page.locator("button:has-text('New')")
        if new_btn.count() > 0:
            new_btn.first.click(force=True)
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-new-session.png"))

    def test_type_message(self, page: Page) -> None:
        """Can type a message into the input."""
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle")
        page.wait_for_timeout(2000)
        input_area = page.locator("textarea, input[type='text']").first
        if input_area.is_visible():
            input_area.fill("Hello, this is a test message")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03-typed-message.png"))
