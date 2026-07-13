"""Shared pytest fixtures for forge-gateway tests.

Most tests in this package exercise business logic (persona routing, SSE
streaming, admin CRUD, MCP dispatch, ...) that is orthogonal to
authentication. By default every test runs with the auth subsystem wired
in ``dev_insecure`` mode (an admin principal with every permission) so
those tests don't need to construct real credentials.

Tests that exercise authentication/authorization directly
(``test_auth_enforcement.py``, ``test_auth_routes.py``, ``test_csrf.py``,
``test_authorization.py``) explicitly call ``security.reset_auth()`` or
``security.configure_auth(...)`` with a specific, non-permissive setup
within the test itself -- this fixture only establishes the default for
everything else.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from forge_config.schema import AuthorizationConfig
from forge_gateway import security
from forge_gateway.routes import health
from forge_security.oidc import Authorizer

# Makes this package's tests/ directory importable via a plain
# `from _token_fixtures import ...` (mirrors forge-security/tests/conftest.py),
# independent of any cross-package "tests" package-name collision under the
# monorepo's --import-mode=importlib pytest configuration.
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)


@pytest.fixture(autouse=True)
def _default_permissive_auth() -> Iterator[None]:
    security.configure_auth(
        session_codec=None,
        service_token_verifier=None,
        oidc_verifier=None,
        authorizer=Authorizer(AuthorizationConfig()),
        dev_insecure=True,
    )
    health.set_auth_healthy(True)
    health.set_auth_mode("dev_insecure")
    yield
    security.reset_auth()
    health.set_auth_healthy(True)
    health.set_auth_mode("enforce")
