# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Forge AI is a config-driven AI agent system with dynamic MCP tool surfaces. It is a uv monorepo workspace (Python 3.12+) with a dependency chain of four packages:

`forge-config` -> `forge-security` -> `forge-agent` -> `forge-gateway`

- **forge-config** — Pydantic config schema, YAML loader, hot-reload watcher, secret resolution (`${ENV_VAR}` refs), versioning.
- **forge-security** — AgentWeave integration: identity, signing, audit, rate limiting, trust, secrets, middleware.
- **forge-agent** — Tool builders (`builder/`: openapi, manual, workflow, registry) plus the PydanticAI agent core (`agent/`: core, context, llm via LiteLLM in embedded/sidecar/external modes, peers for A2A).
- **forge-gateway** — FastAPI app factory `create_app` in `app.py`. Routes: conversational, programmatic, mcp, a2a, admin, persona, health, metrics. Serves the built React SPA from `static/` (or `/app/static` in Docker).

Also in the repo but outside the uv workspace:

- **packages/forge-ui** — React 19 + TypeScript + Vite 6 + Tailwind 4, with zustand, TanStack Query, react-hook-form + zod, CodeMirror. Feature folders: chat, config, dashboard, guide, login, peers, security, tools. The Vite dev server (port 5173) proxies `/v1`, `/health`, `/metrics` to `http://localhost:8000`.
- **e2e-tests/** — separate pytest + pytest-playwright suite (see E2E Tests below).
- **docs/** — Jekyll documentation site (user/developer/technical).

`forge.yaml` is the single source of truth for a deployment (see `forge.yaml.example`): metadata, llm (LiteLLM model_list, fallbacks), tools (openapi_sources, manual_tools, workflows). Secrets use `${ENV_VAR}` refs resolved by forge-config.

**AgentWeave** is an editable path dependency at `../agentweave` — the sibling directory must exist or `uv sync` fails.

## Commands

```bash
uv sync                                    # install all Python deps
uv run pytest -v                           # all tests
uv run pytest packages/forge-config/tests/ -v                       # one package
uv run pytest packages/forge-config/tests/test_loader.py::test_name -v  # one test
uv run ruff check .
uv run ruff format .
uv run mypy packages/
```

UI (run inside `packages/forge-ui`):

```bash
npm install
npm run dev          # vite dev server on 5173, proxies to gateway on 8000
npm run build        # tsc -b && vite build
npm run lint
npm run typecheck
npm run test         # vitest
npm run test:e2e     # playwright
```

## Run Locally

```bash
FORGE_CONFIG_PATH=path/to/forge.yaml uv run uvicorn forge_gateway.app:create_app --factory --port 8000
```

Or `docker-compose up` (forge on 8000, metrics on 9090, plus redis:7). Skaffold deploys the Helm chart at `deploy/helm/forge` with `values.dev.yaml` and port-forwards 8000.

## E2E Tests

`e2e-tests/` at the repo root is a separate pytest + pytest-playwright suite that runs against an **already-deployed instance** — it does not start the app. Configure with `E2E_BASE_URL` (default `https://forge-ai.hvs`) and `E2E_ADMIN_KEY`. Runs chromium with screenshots on.

## Conventions

- Python 3.12+, strict mypy with the pydantic plugin, ruff line length 100
- TDD: write tests first, all tests must pass before committing
- Async-first: all I/O operations are async
- Pydantic v2 for all data models
- `from __future__ import annotations` in all source files

## Testing

- `pytest-asyncio` with `asyncio_mode = "auto"`
- Use PydanticAI `TestModel` for LLM-dependent tests (no real API calls)
- Mock external services (AgentWeave, APIs) in unit tests
- Test fixtures live in `packages/*/tests/fixtures/`
