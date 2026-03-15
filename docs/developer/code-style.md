---
layout: page
title: Code Style
description: Ruff configuration, mypy strict mode, naming conventions, import ordering, and async patterns for Forge AI.
parent: Developer Guide
nav_order: 6
---

# Code Style

Forge AI enforces consistent style through automated tooling. Ruff handles linting and formatting, mypy enforces strict static typing, and conventions are documented here for areas the tools do not cover.

## Ruff Configuration

Ruff is configured in the root `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "S", "B", "A", "SIM"]
ignore = ["S101", "UP042"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "S105", "S106", "S108"]
"**/tests/**/*.py" = ["S101", "S105", "S106", "S108"]
"**/schema.py" = ["S105"]
```

### Enabled Rule Sets

| Code | Rule Set | Purpose |
|------|----------|---------|
| `E` | pycodestyle errors | Basic style errors |
| `F` | Pyflakes | Undefined names, unused imports, etc. |
| `W` | pycodestyle warnings | Style warnings |
| `I` | isort | Import ordering and grouping |
| `UP` | pyupgrade | Python version upgrade suggestions (3.12+ syntax) |
| `S` | flake8-bandit | Security-related checks |
| `B` | flake8-bugbear | Common bug patterns |
| `A` | flake8-builtins | Shadowing built-in names |
| `SIM` | flake8-simplify | Code simplification suggestions |

### Globally Ignored Rules

| Rule | Reason |
|------|--------|
| `S101` | `assert` is standard in pytest |
| `UP042` | Allows `Optional[X]` alongside `X \| None` |

### Per-File Ignores

Test files additionally ignore:

| Rule | Reason |
|------|--------|
| `S105` | Hardcoded password strings (test fixtures) |
| `S106` | Hardcoded password arguments (test fixtures) |
| `S108` | Hardcoded temp file paths (test fixtures) |

Schema files ignore `S105` because `SecretRef` defaults contain placeholder strings.

### Running Ruff

```bash
# Check for violations
uv run ruff check .

# Auto-fix violations
uv run ruff check . --fix

# Check formatting
uv run ruff format --check .

# Apply formatting
uv run ruff format .
```

## Mypy Configuration

Mypy runs in strict mode with the Pydantic plugin:

```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_configs = true
plugins = ["pydantic.mypy"]
exclude = ["tests/"]

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

### What Strict Mode Enforces

- All functions must have type annotations (parameters and return types)
- No implicit `Any` types
- No untyped function definitions
- No untyped decorators
- Strict equality checking
- Warn on returning `Any` from typed functions
- Warn on unused mypy config entries

### Pydantic Plugin Settings

| Setting | Effect |
|---------|--------|
| `init_forbid_extra` | Pydantic models reject unexpected fields |
| `init_typed` | Model `__init__` requires typed arguments |
| `warn_required_dynamic_aliases` | Warns when dynamic aliases might cause issues |

### Running Mypy

```bash
uv run mypy packages/
```

Tests are excluded from type checking (`exclude = ["tests/"]`) because test code often uses mocks and fixtures that are difficult to type precisely.

## Python Conventions

### Future Annotations

Every Python source file must begin with:

```python
from __future__ import annotations
```

This enables PEP 604 union syntax (`X | None`) and deferred annotation evaluation, which is required for forward references in Pydantic models.

### Line Length

Maximum 100 characters per line. This is enforced by both ruff (`line-length = 100`) and applies to all Python, YAML, and TOML files in the project.

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Modules | `snake_case` | `secret_resolver.py` |
| Classes | `PascalCase` | `ForgeConfig`, `SecurityGate` |
| Functions | `snake_case` | `load_config()`, `build_and_swap()` |
| Constants | `UPPER_SNAKE_CASE` | `_DEV_MODE_IDENTITY`, `_PRIVATE_NETWORKS` |
| Private | Leading underscore | `_resolve_keys()`, `_config` |
| Type aliases | `PascalCase` | `ModelMessage`, `GateResult` |

### Import Ordering

Ruff's `I` (isort) rule enforces import ordering:

1. **Standard library** (`import os`, `from pathlib import Path`)
2. **Third-party packages** (`from fastapi import ...`, `from pydantic import ...`)
3. **First-party packages** (`from forge_config import ...`, `from forge_gateway.models import ...`)

Imports within each group are alphabetically sorted. Example:

```python
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from forge_config.schema import ForgeConfig
from forge_gateway.models import InvokeRequest, InvokeResponse
```

### Docstrings

Use Google-style docstrings for public functions and classes:

```python
def load_config(
    path: str | Path,
    *,
    env_overlay: bool = True,
) -> ForgeConfig:
    """Load and validate a Forge configuration file.

    Args:
        path: Path to the YAML configuration file.
        env_overlay: Whether to substitute environment variables in values.

    Returns:
        Validated ForgeConfig instance.

    Raises:
        ConfigLoadError: If the file cannot be read or parsed.
        ConfigValidationError: If the config fails Pydantic validation.
    """
```

Module-level docstrings describe the module's purpose:

```python
"""YAML configuration loading with environment variable overlay."""
```

### Async Patterns

All I/O operations use `async/await`. Follow these patterns:

**Async function definitions:**

```python
async def initialize(self) -> None:
    """Initialize the agent by building the tool surface."""
    await self._registry.build_and_swap(self._config)
```

**Async context managers:**

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: build tools on startup, drain on shutdown."""
    # Startup
    yield
    # Shutdown
```

**Async iteration:**

```python
async for text in stream.stream_output(debounce_by=None):
    yield text
```

**Scheduling async work from sync callbacks:**

```python
def _schedule_tool_rebuild(config: object, agent: object | None) -> None:
    """Schedule async rebuild from a sync callback."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_rebuild_tool_surface(config, agent))
```

### Error Handling

Use specific exception types. Log exceptions with `logger.exception()` for full tracebacks:

```python
try:
    config = load_config(config_path)
except ConfigLoadError:
    logger.exception("Failed to load config from %s", config_path)
except ConfigValidationError:
    logger.exception("Config validation failed for %s", config_path)
```

For FastAPI routes, raise `HTTPException` with appropriate status codes:

```python
if _forge_agent is None:
    raise HTTPException(status_code=503, detail="Agent not initialized")
```

### Pydantic Models

All data structures at boundaries (config, API, inter-module) use Pydantic v2 `BaseModel`:

```python
class AdminToolInfo(BaseModel):
    """Metadata about a registered tool."""

    name: str
    description: str = ""
    source: str = "configured"
```

Use `Field()` for defaults, descriptions, and validation:

```python
class ForgeConfig(BaseModel):
    metadata: ForgeMetadata = Field(default_factory=ForgeMetadata)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
```

Use `model_validator` for cross-field validation:

```python
@model_validator(mode="after")
def validate_endpoint(self) -> LiteLLMConfig:
    if self.mode in (LiteLLMMode.SIDECAR, LiteLLMMode.EXTERNAL) and not self.endpoint:
        msg = f"endpoint is required when mode is {self.mode.value}"
        raise ValueError(msg)
    return self
```

## TypeScript Conventions (forge-ui)

### Naming

| Element | Convention | Example |
|---------|-----------|---------|
| Files | `PascalCase` for components, `camelCase` for utilities | `Dashboard.tsx`, `useApiClient.ts` |
| Components | `PascalCase` | `ConfigEditor`, `ToolList` |
| Functions | `camelCase` | `fetchConfig()`, `handleSubmit()` |
| Constants | `UPPER_SNAKE_CASE` | `API_BASE_URL` |
| Types/Interfaces | `PascalCase` | `AdminToolInfo`, `ConfigState` |

### Linting and Formatting

```bash
cd packages/forge-ui

# Lint
npm run lint

# Type check
npm run typecheck
```

The frontend uses ESLint with TypeScript and React Hooks plugins, configured in `eslint.config.js`.

## Pre-Commit Summary

Run all checks before every commit:

```bash
# Python
uv run ruff check . && \
uv run ruff format --check . && \
uv run mypy packages/ && \
uv run pytest -v

# Frontend (if modified)
cd packages/forge-ui && \
npm run lint && \
npm run typecheck && \
npm run test
```
