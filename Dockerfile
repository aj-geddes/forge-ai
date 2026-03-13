# Stage 1: UI Builder
FROM node:22-slim AS ui-builder

WORKDIR /ui
COPY forge-ai/packages/forge-ui/package.json forge-ai/packages/forge-ui/package-lock.json ./
RUN npm ci
COPY forge-ai/packages/forge-ui/ .
RUN npm run build

# Stage 2: Python Builder
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /build

# Copy agentweave dependency first (referenced as ../agentweave in pyproject.toml)
# Build context must include agentweave as a sibling directory.
# Use: docker build -f forge-ai/Dockerfile . (from parent directory)
# Or the CI workflow handles this via context path.
COPY agentweave/ /build/agentweave/

# Set the app workdir
WORKDIR /build/forge-ai

# Copy workspace root and lockfile first for layer caching
COPY forge-ai/pyproject.toml forge-ai/uv.lock ./

# Copy all package pyproject.toml files for dependency resolution
COPY forge-ai/packages/forge-config/pyproject.toml packages/forge-config/pyproject.toml
COPY forge-ai/packages/forge-security/pyproject.toml packages/forge-security/pyproject.toml
COPY forge-ai/packages/forge-agent/pyproject.toml packages/forge-agent/pyproject.toml
COPY forge-ai/packages/forge-gateway/pyproject.toml packages/forge-gateway/pyproject.toml

# Install dependencies (cached layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Copy source code
COPY forge-ai/packages/ packages/

# Install workspace packages (non-editable so paths work in runtime image)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Stage 3: Runtime
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --gid 999 forge && \
    useradd --uid 999 --gid forge --shell /bin/false --create-home forge

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /build/forge-ai/.venv /app/.venv

# Copy UI build output
COPY --from=ui-builder /ui/dist /app/static

# Copy config example as reference
COPY forge-ai/forge.yaml.example /app/forge.yaml.example

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000 9090

USER forge

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx; httpx.get('http://localhost:8000/health/live').raise_for_status()"]

ENTRYPOINT ["python", "-m", "uvicorn", "forge_gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
