# Forge AI - Project Instructions

## Architecture

Forge is a config-driven AI agent system using a uv monorepo workspace with four packages:

- **forge-config** - Pydantic config schema, YAML loader, hot-reload, secret resolution
- **forge-security** - AgentWeave integration (identity, signing, audit, rate limiting, trust)
- **forge-agent** - Tool builder (OpenAPI, manual, workflow) + PydanticAI agent core
- **forge-gateway** - FastAPI app exposing REST, MCP, and A2A interfaces

Dependency chain: `forge-config` -> `forge-security` -> `forge-agent` -> `forge-gateway`

## Development

```bash
# Install all dependencies
uv sync

# Run all tests
uv run pytest -v

# Run tests for a specific package
uv run pytest packages/forge-config/tests/ -v

# Lint and format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy packages/
```

## Key Files

- `forge.yaml.example` - Canonical config reference
- `packages/*/src/` - Source code (each package has `src/<package_name>/`)
- `packages/*/tests/` - Tests co-located with packages
- `deploy/helm/forge/` - Helm chart with small/medium/large profiles
- `Dockerfile` - Multi-stage build targeting <200MB

## Conventions

- Python 3.12+, strict mypy, ruff for linting/formatting
- TDD: write tests first, all tests must pass before committing
- Line length: 100 characters
- Async-first: all I/O operations are async
- Pydantic v2 for all data models
- Use `from __future__ import annotations` in all source files

## External Dependencies

- **AgentWeave** (`/home/aj-geddes/dev/claude-projects/agentweave`) - Security framework
- **PydanticAI** - Agent framework with TestModel for testing
- **LiteLLM** - LLM routing (embedded/sidecar/external modes)
- **FastMCP** - MCP tool surface builder
- **FastAPI** - Gateway HTTP framework

## Testing

- Use `pytest-asyncio` with `asyncio_mode = "auto"`
- Use PydanticAI `TestModel` for LLM-dependent tests (no real API calls)
- Mock external services (AgentWeave, APIs) in unit tests
- Test fixtures live in `packages/*/tests/fixtures/`
