# Forge AI - Project Instructions

## Agent Workflow — Unicorn Team (REQUIRED)

All work in this project MUST use the `unicorn-team` skill ecosystem. When implementing features, fixing bugs, refactoring, or performing any engineering task, invoke the appropriate unicorn-team agents:

- **`unicorn-team:orchestrator`** — Entry point for all multi-step tasks. Use this first to coordinate the team.
- **`unicorn-team:architect`** — System design, ADRs, API contracts, data models.
- **`unicorn-team:developer`** — TDD-first implementation across Python packages.
- **`unicorn-team:qa-security`** — Code review, security analysis, quality gates.
- **`unicorn-team:domain-devops`** — Docker, Helm, CI/CD, Skaffold, observability.
- **`unicorn-team:testing`** — Test strategy, test-first development, coverage.
- **`unicorn-team:python`** — Python-specific idioms, tooling, project structure.
- **`unicorn-team:security`** — Threat modeling, OWASP, input validation, secrets.
- **`unicorn-team:self-verification`** — Pre-commit quality checks.
- **`unicorn-team:technical-debt`** — Debt tracking, prioritization, paydown.
- **`unicorn-team:estimation`** — Task sizing and effort estimates when requested.

### Parallel Agent Execution (REQUIRED)

Always run independent agents in parallel to maximize throughput and quality. When a task involves multiple concerns, launch the relevant agents concurrently rather than sequentially. For example:

- **Feature implementation**: Run `architect` + `developer` + `testing` in parallel for design, code, and test strategy simultaneously.
- **Pre-commit review**: Run `qa-security` + `self-verification` + `python` in parallel to check quality, security, and Python idioms at the same time.
- **Deployment changes**: Run `domain-devops` + `security` in parallel to validate infrastructure and security posture together.
- **Bug fixes**: Run `developer` + `testing` + `code-reading` in parallel to understand the bug, write the fix, and plan test coverage concurrently.

Never serialize agent work that can be parallelized. The orchestrator skill handles coordination, but when invoking agents directly, prefer concurrent execution for any agents that do not depend on each other's output.

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
