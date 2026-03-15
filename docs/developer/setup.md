---
layout: page
title: Development Setup
description: Prerequisites, installation, local development, and troubleshooting for the Forge AI development environment.
parent: Developer Guide
nav_order: 2
---

# Development Setup

This guide walks you through setting up a local Forge AI development environment from scratch.

## Prerequisites

| Tool | Minimum Version | Purpose |
|------|----------------|---------|
| **Python** | 3.12+ | Runtime for all backend packages |
| **uv** | Latest | Python package manager and workspace tool |
| **Node.js** | 22+ | Frontend build toolchain |
| **npm** | Bundled with Node.js | Frontend dependency management |
| **Docker** | 20+ | Container builds (optional, for deployment testing) |
| **kubectl** | 1.28+ | Kubernetes interaction (optional, for cluster deployment) |
| **Helm** | 3.12+ | Chart-based deployment (optional, for cluster deployment) |

### Installing uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv

# Verify
uv --version
```

## Clone and Install

### Backend (Python)

```bash
git clone https://github.com/aj-geddes/forge-ai.git
cd forge-ai

# Install all workspace packages and dev dependencies
uv sync
```

`uv sync` reads the workspace-level `pyproject.toml`, resolves all four packages (`forge-config`, `forge-security`, `forge-agent`, `forge-gateway`) plus dev dependencies, and creates a virtual environment at `.venv/`.

### Frontend (React)

```bash
cd packages/forge-ui
npm ci
```

This installs the exact locked dependencies from `package-lock.json`.

### AgentWeave (External Dependency)

The `forge-security` package depends on AgentWeave, which is referenced as a local path dependency at `../agentweave` (relative to the repository root). Make sure the AgentWeave repo is cloned as a sibling directory:

```bash
# From the parent directory of forge-ai
git clone <agentweave-repo-url> agentweave
```

The `pyproject.toml` workspace root configures this path:

```toml
[tool.uv.sources]
agentweave = { path = "../agentweave", editable = true }
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FORGE_CONFIG_PATH` | `forge.yaml` | Path to the configuration file |
| `FORGE_ADMIN_KEY` | *(none)* | Admin API key for the gateway's admin endpoints |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `OPENAI_API_KEY` | *(none)* | OpenAI API key (if using OpenAI models) |
| `ANTHROPIC_API_KEY` | *(none)* | Anthropic API key (if using Claude models) |
| `FORGE_GATEWAY_URL` | `http://localhost:8000` | Public URL for A2A agent card discovery |
| `AGENTWEAVE_IDENTITY_SECRET` | *(none)* | Secret for AgentWeave identity management |

### Minimal .env Setup

Create a `.env` file at the repository root (it is gitignored):

```bash
# Required for LLM calls
OPENAI_API_KEY=sk-...

# Required for admin endpoints
FORGE_ADMIN_KEY=your-dev-admin-key

# Optional
LOG_LEVEL=DEBUG
```

## Configuration File

Copy the example configuration and customize it:

```bash
cp forge.yaml.example forge.yaml
```

The config file uses `${VAR}` and `${VAR:default}` syntax for environment variable substitution. Secret references use the `SecretRef` pattern:

```yaml
security:
  api_keys:
    enabled: true
    keys:
      - source: env
        name: FORGE_ADMIN_KEY
```

See `forge.yaml.example` for the complete reference.

## Running Locally

### Start the Gateway

```bash
uv run uvicorn forge_gateway.app:create_app \
  --factory \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
```

The `--factory` flag tells uvicorn to call `create_app()` to get the FastAPI application. The `--reload` flag enables auto-restart on Python source changes (development only).

The gateway will:
1. Load `forge.yaml` (from `FORGE_CONFIG_PATH` or current directory)
2. Initialize the `SecurityGate` (or fall back to dev mode if AgentWeave is not configured)
3. Build the tool surface from configured OpenAPI specs, manual tools, and workflows
4. Create the PydanticAI agent
5. Mount the MCP server at `/mcp`
6. Build the A2A agent card
7. Start the config file watcher for hot-reload
8. Mark readiness as `true`

### Start the Frontend Dev Server

In a separate terminal:

```bash
cd packages/forge-ui
npm run dev
```

Vite starts on port **5173** and proxies API requests to the gateway on port **8000**:

| Proxy Path | Target |
|-----------|--------|
| `/v1/*` | `http://localhost:8000` |
| `/health/*` | `http://localhost:8000` |
| `/metrics` | `http://localhost:8000` |
| `/a2a/*` | `http://localhost:8000` |

Open [http://localhost:5173](http://localhost:5173) to access the admin dashboard.

### Docker Compose

For a containerized local environment with Redis:

```bash
docker compose up
```

This starts the Forge gateway (port 8000) and a Redis instance (port 6379). The gateway image is built using the multi-stage `Dockerfile`.

**Note**: The Dockerfile expects a specific build context. When building manually:

```bash
# From the parent directory (containing both forge-ai/ and agentweave/)
docker build -f forge-ai/Dockerfile .
```

## Verifying the Setup

### Health Checks

```bash
# Liveness (always responds, even during startup)
curl http://localhost:8000/health/live
# → {"status": "ok"}

# Readiness (responds after full initialization)
curl http://localhost:8000/health/ready
# → {"status": "ready", "version": "", "components": {}}

# Startup
curl http://localhost:8000/health/startup
# → {"status": "started"}
```

### Admin API (requires API key)

```bash
curl -H "Authorization: Bearer $FORGE_ADMIN_KEY" \
  http://localhost:8000/v1/admin/config
```

### Prometheus Metrics

```bash
curl http://localhost:8000/metrics
```

### Interactive API Docs

FastAPI auto-generates OpenAPI docs at:
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Common Issues

### "No config loaded" on startup

The gateway could not find `forge.yaml`. Ensure the file exists at the path specified by `FORGE_CONFIG_PATH`, or in the current working directory.

```bash
export FORGE_CONFIG_PATH=/path/to/forge.yaml
```

### "forge-agent not available, running in gateway-only mode"

The `forge-agent` package is not installed. Run `uv sync` from the workspace root to install all packages.

### "SecurityGate not configured -- running in DEVELOPMENT mode"

This is expected during local development. The gateway operates without authentication when AgentWeave security is disabled in config (`security.agentweave.enabled: false`). Agent-facing routes allow unauthenticated access with a logged warning.

### "Admin API key authentication is not configured" (HTTP 403)

Admin endpoints require API key auth to be enabled in config:

```yaml
security:
  api_keys:
    enabled: true
    keys:
      - source: env
        name: FORGE_ADMIN_KEY
```

Set the `FORGE_ADMIN_KEY` environment variable to your desired key value.

### npm ci fails with peer dependency conflicts

If `npm ci` fails in `packages/forge-ui/`, try:

```bash
npm ci --legacy-peer-deps
```

### Port 8000 already in use

Another process is using port 8000. Either stop that process or use a different port:

```bash
uv run uvicorn forge_gateway.app:create_app --factory --port 8001
```

Update the Vite proxy config in `packages/forge-ui/vite.config.ts` to match.

### watchdog import errors

The `watchdog` package is a dependency of `forge-config` and is required for hot-reload. If import errors occur, verify the installation:

```bash
uv run python -c "import watchdog; print(watchdog.__version__)"
```
