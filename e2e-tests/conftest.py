"""Shared fixtures for E2E tests."""

from __future__ import annotations

import pytest
import httpx


BASE_URL = "http://127.0.0.1:8001"


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """Synchronous HTTP client for API tests."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session")
def async_client() -> httpx.AsyncClient:
    """Async HTTP client for async API tests."""
    return httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)
