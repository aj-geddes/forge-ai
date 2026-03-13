"""Shared fixtures for E2E tests."""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "https://forge-ai.hvs")
ADMIN_API_KEY = os.getenv("E2E_ADMIN_KEY", "forge-e2e-test-key-2026")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "e2e-screenshots")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def admin_api_key() -> str:
    return ADMIN_API_KEY


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """Synchronous HTTP client for API tests."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0, verify=False) as c:  # noqa: S501
        yield c


@pytest.fixture(scope="session")
def admin_client() -> httpx.Client:
    """HTTP client with admin API key."""
    with httpx.Client(
        base_url=BASE_URL,
        timeout=10.0,
        verify=False,  # noqa: S501
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    ) as c:
        yield c


@pytest.fixture(scope="session")
def async_client() -> httpx.AsyncClient:
    """Async HTTP client for async API tests."""
    return httpx.AsyncClient(base_url=BASE_URL, timeout=10.0, verify=False)  # noqa: S501


VIEWPORTS = {
    "mobile": {"width": 375, "height": 812},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1440, "height": 900},
}


@pytest.fixture()
def browser_context_args() -> dict:  # type: ignore[type-arg]
    """Accept self-signed certificates from the HVS cluster CA."""
    return {"ignore_https_errors": True}
