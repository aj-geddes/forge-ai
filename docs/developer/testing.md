---
layout: page
title: Testing
description: Test framework, running tests, mock patterns, coverage, and E2E testing for the Forge AI project.
parent: Developer Guide
nav_order: 4
---

# Testing

Forge AI uses pytest as the test framework with pytest-asyncio for async test support. The project follows a test-driven development (TDD) workflow: write tests first, then implement until all tests pass.

## Test Framework

| Tool | Purpose |
|------|---------|
| **pytest** (8.0+) | Test runner and framework |
| **pytest-asyncio** (0.25+) | Async test support with `asyncio_mode = "auto"` |
| **pytest-cov** (6.0+) | Coverage reporting |
| **pytest-mock** (3.14+) | Mock fixture integration |
| **PydanticAI TestModel** | Deterministic LLM responses without real API calls |
| **httpx** | Async/sync HTTP client for E2E tests |

### pytest Configuration

The root `pyproject.toml` defines global pytest settings:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["packages/*/tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "--strict-markers -v --import-mode=importlib"
```

Key settings:
- `asyncio_mode = "auto"` -- async test functions are automatically recognized without `@pytest.mark.asyncio`
- `testpaths` -- tests are discovered from all package test directories
- `--import-mode=importlib` -- uses importlib for module imports, avoiding path conflicts in the monorepo

## Running Tests

### All Tests

```bash
uv run pytest -v
```

### Tests for a Specific Package

```bash
# forge-config
uv run pytest packages/forge-config/tests/ -v

# forge-security
uv run pytest packages/forge-security/tests/ -v

# forge-agent
uv run pytest packages/forge-agent/tests/ -v

# forge-gateway
uv run pytest packages/forge-gateway/tests/ -v
```

### A Single Test File

```bash
uv run pytest packages/forge-gateway/tests/test_health.py -v
```

### A Single Test Function

```bash
uv run pytest packages/forge-gateway/tests/test_health.py::test_liveness -v
```

### With Coverage

```bash
# All packages
uv run pytest --cov=packages --cov-report=term-missing -v

# Single package
uv run pytest packages/forge-config/tests/ --cov=forge_config --cov-report=term-missing -v
```

### Watch Mode (re-run on changes)

```bash
uv run pytest --watch  # requires pytest-watch plugin
```

## Test Organization

Tests are co-located with their packages:

```
packages/
├── forge-config/tests/
│   ├── __init__.py
│   ├── test_schema.py            # Pydantic model validation tests
│   ├── test_loader.py            # YAML loading and env substitution tests
│   ├── test_watcher.py           # ConfigWatcher tests
│   ├── test_secret_resolver.py   # Secret resolution tests
│   └── test_versioning.py        # Config versioning tests
│
├── forge-security/tests/
│   ├── __init__.py
│   ├── test_middleware.py        # SecurityGate pipeline tests
│   ├── test_identity.py          # Identity management tests
│   ├── test_signing.py           # Message signing tests
│   ├── test_audit.py             # Audit logging tests
│   ├── test_rate_limit.py        # Rate limiter tests
│   ├── test_trust.py             # Trust policy tests
│   └── test_secrets.py           # Secret resolver tests
│
├── forge-agent/tests/
│   ├── __init__.py
│   ├── test_core.py              # ForgeAgent lifecycle and run tests
│   ├── test_llm.py               # LLMRouter configuration tests
│   ├── test_context.py           # ConversationContext tests
│   ├── test_peers.py             # PeerCaller tests
│   ├── test_registry.py          # ToolSurfaceRegistry tests
│   ├── test_openapi_builder.py   # OpenAPI tool builder tests
│   └── test_workflow_builder.py  # Workflow tool builder tests
│
└── forge-gateway/tests/
    ├── __init__.py
    ├── test_app.py               # Application factory tests
    ├── test_health.py            # Health endpoint tests
    ├── test_admin.py             # Admin API tests
    ├── test_programmatic.py      # Invoke endpoint tests
    ├── test_conversational.py    # Chat endpoint tests
    ├── test_a2a.py               # A2A protocol tests
    ├── test_mcp.py               # MCP server tests
    ├── test_metrics.py           # Metrics endpoint tests
    ├── test_auth.py              # Admin auth tests
    ├── test_security_gate.py     # SecurityGate dependency tests
    ├── test_cors.py              # CORS middleware tests
    ├── test_schema.py            # JSON Schema conversion tests
    └── test_persona.py           # Agent persona resolution tests
```

## Mock Patterns

### PydanticAI TestModel

For agent tests that would normally call an LLM, use PydanticAI's `TestModel` to get deterministic responses without real API calls:

```python
from pydantic_ai.models.test import TestModel
from forge_agent import ForgeAgent
from forge_config.schema import ForgeConfig

async def test_agent_conversational():
    config = ForgeConfig()  # defaults
    agent = ForgeAgent(config, model_override=TestModel())
    await agent.initialize()

    result = await agent.run_conversational("Hello")
    assert result.output is not None
```

`TestModel` returns predictable responses, making tests fast and reproducible. No `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is needed.

### Mocking AgentWeave

AgentWeave is an external dependency that should be mocked in unit tests:

```python
from unittest.mock import AsyncMock, MagicMock, patch

async def test_security_gate():
    mock_gate = MagicMock()
    mock_gate.return_value = GateResult(allowed=True, identity="test-user")

    with patch("forge_gateway.security._security_gate", mock_gate):
        # Test route behavior with security enabled
        ...
```

### Mocking httpx for External APIs

Tool builders and peer callers use httpx for HTTP requests. Mock the client for deterministic tests:

```python
from unittest.mock import AsyncMock, patch

async def test_openapi_builder():
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"openapi": "3.0.0", "paths": {}}

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        # Test OpenAPI spec fetching
        ...
```

### FastAPI TestClient

Gateway route tests use FastAPI's `TestClient` or httpx `AsyncClient`:

```python
from fastapi.testclient import TestClient
from forge_gateway.app import create_app

def test_liveness():
    app = create_app()
    client = TestClient(app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

### Config Test Fixtures

Create minimal config fixtures for focused tests:

```python
import pytest
from forge_config.schema import ForgeConfig, ForgeMetadata

@pytest.fixture
def minimal_config() -> ForgeConfig:
    return ForgeConfig(
        metadata=ForgeMetadata(name="test-agent"),
    )
```

For tests that need YAML files, place them in `packages/*/tests/fixtures/` and load via `pathlib.Path`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

def test_load_config():
    config = load_config(FIXTURES / "valid.yaml")
    assert config.metadata.name == "test"
```

## Ruff Ignores for Test Files

The root `pyproject.toml` disables certain ruff rules in test directories:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "S105", "S106", "S108"]
"**/tests/**/*.py" = ["S101", "S105", "S106", "S108"]
```

| Rule | Description | Why Ignored |
|------|-------------|-------------|
| S101 | Use of `assert` | Standard in pytest |
| S105 | Hardcoded password string | Test fixtures use fake credentials |
| S106 | Hardcoded password in function argument | Same as above |
| S108 | Hardcoded temp file path | Test fixtures use temp paths |

## E2E Tests

End-to-end tests live in the `e2e-tests/` directory at the repository root. They test the full system by making HTTP requests against a running gateway instance.

### Structure

```
e2e-tests/
├── conftest.py                # Shared fixtures (base URL, admin key, HTTP clients)
├── pytest.ini                 # E2E-specific pytest config
└── tests/
    ├── test_health.py         # Health endpoint E2E tests
    ├── test_api_admin.py      # Admin API E2E tests
    ├── test_programmatic.py   # Invoke endpoint E2E tests
    ├── test_conversational.py # Chat endpoint E2E tests
    ├── test_a2a.py            # A2A protocol E2E tests
    ├── test_metrics.py        # Metrics endpoint E2E tests
    ├── test_middleware.py     # Middleware E2E tests
    ├── test_swagger.py        # OpenAPI docs E2E tests
    ├── test_edge_cases.py     # Edge case E2E tests
    ├── test_ui_dashboard.py   # UI dashboard E2E tests
    ├── test_ui_config.py      # UI config page E2E tests
    ├── test_ui_tools.py       # UI tools page E2E tests
    ├── test_ui_chat.py        # UI chat page E2E tests
    ├── test_ui_peers.py       # UI peers page E2E tests
    ├── test_ui_security.py    # UI security page E2E tests
    ├── test_ui_guide.py       # UI guide page E2E tests
    └── test_ui_responsive.py  # UI responsive layout E2E tests
```

### Configuration

E2E tests use environment variables for configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `E2E_BASE_URL` | `https://forge-ai.hvs` | Base URL of the running gateway |
| `E2E_ADMIN_KEY` | `forge-e2e-test-key-2026` | Admin API key for authenticated requests |

### Running E2E Tests

```bash
# Ensure the gateway is running first
cd e2e-tests

# Run all E2E tests
uv run pytest tests/ -v

# Run only API tests
uv run pytest tests/test_health.py tests/test_api_admin.py -v

# Run only UI tests
uv run pytest tests/test_ui_dashboard.py -v
```

### E2E Test Fixtures

The `conftest.py` provides session-scoped HTTP clients:

- `client` -- httpx sync client for API tests
- `admin_client` -- httpx sync client with admin API key pre-configured
- `async_client` -- httpx async client
- `base_url` -- the gateway's base URL
- `admin_api_key` -- the admin API key string

## Frontend Tests

The `forge-ui` package uses Vitest for unit tests and Playwright for E2E browser tests:

```bash
cd packages/forge-ui

# Unit tests
npm run test

# E2E browser tests
npm run test:e2e

# Type checking
npm run typecheck
```
