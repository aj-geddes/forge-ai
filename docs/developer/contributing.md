---
layout: page
title: Contributing
description: Contribution workflow, TDD process, PR guidelines, and commit conventions for the Forge AI project.
parent: Developer Guide
nav_order: 5
---

# Contributing

This guide covers the development workflow for contributing to Forge AI, from writing your first test to submitting a pull request.

## TDD Workflow

All contributions follow test-driven development. The process is:

1. **Write a failing test** that describes the expected behavior
2. **Run the test** to confirm it fails for the right reason
3. **Implement the minimum code** to make the test pass
4. **Refactor** while keeping all tests green
5. **Run the full suite** before committing

```bash
# Step 1-2: Write test, confirm it fails
uv run pytest packages/forge-config/tests/test_schema.py::test_new_feature -v

# Step 3: Implement
# ... edit source files ...

# Step 4: Refactor and verify
uv run pytest packages/forge-config/tests/test_schema.py -v

# Step 5: Full suite
uv run pytest -v
```

All tests must pass before code is committed. No exceptions.

## Pre-Commit Checks

Before committing, run all quality checks:

### Linting

```bash
# Check for linting issues
uv run ruff check .

# Auto-fix what can be fixed
uv run ruff check . --fix

# Format all files
uv run ruff format .
```

Ruff enforces the rules configured in the root `pyproject.toml`. See the [Code Style](code-style) guide for details.

### Type Checking

```bash
uv run mypy packages/
```

Mypy runs in strict mode. All public functions must have type annotations. Tests are excluded from type checking.

### Tests

```bash
uv run pytest -v
```

### All Checks at Once

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy packages/ && uv run pytest -v
```

## Branch Naming

Use descriptive branch names with a category prefix:

| Prefix | Usage |
|--------|-------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `refactor/` | Code restructuring without behavior change |
| `docs/` | Documentation changes |
| `test/` | Test additions or fixes |
| `chore/` | Dependency updates, CI changes, tooling |

Examples:
- `feat/workflow-conditional-steps`
- `fix/rate-limiter-window-reset`
- `refactor/extract-tool-builder-base`

## Commit Messages

Use conventional commit format:

```
type(scope): short description

Optional longer description explaining the "why" of the change.
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or updating tests |
| `docs` | Documentation changes |
| `chore` | Maintenance (deps, CI, config) |
| `perf` | Performance improvement |

### Scopes

| Scope | Package/Area |
|-------|-------------|
| `config` | forge-config |
| `security` | forge-security |
| `agent` | forge-agent |
| `gateway` | forge-gateway |
| `ui` | forge-ui |
| `e2e` | E2E tests |
| `ci` | CI/CD pipeline |
| `helm` | Helm chart |

### Examples

```
feat(agent): add conditional step execution to workflow builder

fix(security): replace fragile JWT dot-counting with decode-based detection

refactor(agent,gateway): remove dead output module, add auth unit tests

docs(developer): add API reference documentation
```

## Pull Request Process

### 1. Create a Feature Branch

```bash
git checkout -b feat/my-feature main
```

### 2. Develop with TDD

Write tests, implement, refactor. Commit frequently with clear messages.

### 3. Verify All Checks Pass

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/
uv run pytest -v
```

### 4. Push and Open a PR

```bash
git push -u origin feat/my-feature
```

Open a pull request against `main`. The PR should include:

- **Title**: Short, descriptive (under 70 characters)
- **Summary**: What changed and why (1-3 bullet points)
- **Test plan**: How the changes were tested

### 5. Code Review

All PRs require review. Reviewers check:

- Tests cover the new behavior
- Type annotations are complete
- Ruff and mypy pass
- No unnecessary dependencies added
- Documentation is updated if the change affects the public API
- Security implications are considered

### 6. Merge

PRs are merged to `main` after approval and all checks pass.

## Adding a New Feature

### Backend (Python)

1. Determine which package owns the feature (config, security, agent, or gateway)
2. Write tests in `packages/<package>/tests/`
3. Implement in `packages/<package>/src/<package_name>/`
4. Export from `__init__.py` if the feature is part of the public API
5. Update type annotations and docstrings

### Frontend (React)

1. Write tests in the appropriate `__tests__` or `.test.ts` file
2. Implement components in `packages/forge-ui/src/`
3. Run `npm run typecheck` and `npm run lint`

### API Endpoint

1. Define request/response models in `packages/forge-gateway/src/forge_gateway/models.py`
2. Create or update the route handler in `packages/forge-gateway/src/forge_gateway/routes/`
3. Add the router to `app.py` if it is a new router
4. Write gateway tests in `packages/forge-gateway/tests/`
5. Update the API reference documentation

## Conventions Checklist

Before submitting a PR, verify:

- [ ] `from __future__ import annotations` at the top of every new Python source file
- [ ] All lines under 100 characters
- [ ] All I/O operations are async
- [ ] Pydantic v2 models for any new data structures
- [ ] No hardcoded secrets or API keys in source
- [ ] Tests use `TestModel` instead of real LLM calls
- [ ] External services (AgentWeave, HTTP APIs) are mocked in unit tests
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run mypy packages/` passes
- [ ] `uv run pytest -v` passes
