---
layout: page
title: Developer Guide
description: Developer documentation for building, extending, and contributing to the Forge AI agent system.
nav_order: 1
has_children: true
---

# Developer Guide

This documentation is for engineers who build, extend, and maintain the Forge AI platform. It covers architecture, local development setup, API reference, testing strategy, contribution workflow, and code style conventions.

## Audience

- **Backend engineers** working on the Python monorepo (forge-config, forge-security, forge-agent, forge-gateway).
- **Frontend engineers** building the React admin UI (forge-ui).
- **DevOps engineers** managing deployment via Docker, Helm, and Skaffold.
- **Contributors** submitting pull requests or extending the tool surface.

## What is Forge AI?

Forge AI is a config-driven AI agent system. You write a `forge.yaml` file that declares which LLM models to use, which tools to expose (from OpenAPI specs, manual definitions, or multi-step workflows), and how security is enforced. The system builds a live tool surface, creates a PydanticAI agent, and exposes it through REST, MCP, and A2A interfaces via a FastAPI gateway.

## Documentation Map

| Page | Description |
|------|-------------|
| [Architecture](architecture) | Package dependency chain, component breakdown, data flow, design decisions |
| [Development Setup](setup) | Prerequisites, installation, running locally, frontend dev server |
| [API Reference](api-reference) | Complete endpoint documentation with request/response examples |
| [Testing](testing) | Test framework, running tests, mock patterns, E2E test suite |
| [Contributing](contributing) | TDD workflow, PR process, commit conventions |
| [Code Style](code-style) | Ruff config, mypy strict mode, naming conventions, async patterns |

## Quick Start

```bash
# Clone and install
git clone https://github.com/aj-geddes/forge-ai.git
cd forge-ai
uv sync

# Run tests
uv run pytest -v

# Start the gateway
cp forge.yaml.example forge.yaml
uv run uvicorn forge_gateway.app:create_app --factory --host 0.0.0.0 --port 8000

# Start the frontend dev server (separate terminal)
cd packages/forge-ui
npm ci
npm run dev
```

## Key Files

| Path | Purpose |
|------|---------|
| `forge.yaml.example` | Canonical configuration reference |
| `pyproject.toml` | Workspace root with uv, ruff, mypy, and pytest settings |
| `packages/*/src/` | Source code for each Python package |
| `packages/*/tests/` | Unit tests co-located with packages |
| `packages/forge-ui/` | React admin dashboard |
| `e2e-tests/` | End-to-end test suite |
| `deploy/helm/forge/` | Helm chart with sizing profiles |
| `Dockerfile` | Multi-stage build (Node + Python + runtime) |
