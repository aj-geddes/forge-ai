# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Copy workspace root and lockfile first for layer caching
COPY pyproject.toml uv.lock ./

# Copy all package pyproject.toml files for dependency resolution
COPY packages/forge-config/pyproject.toml packages/forge-config/pyproject.toml
COPY packages/forge-security/pyproject.toml packages/forge-security/pyproject.toml
COPY packages/forge-agent/pyproject.toml packages/forge-agent/pyproject.toml
COPY packages/forge-gateway/pyproject.toml packages/forge-gateway/pyproject.toml

# Copy agentweave dependency (local path reference)
COPY vendor/agentweave/ /home/aj-geddes/dev/claude-projects/agentweave/

# Install dependencies (cached layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Copy source code
COPY packages/ packages/

# Install workspace packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --gid 999 forge && \
    useradd --uid 999 --gid forge --shell /bin/false --create-home forge

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy config example as reference
COPY forge.yaml.example /app/forge.yaml.example

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000 9090

USER forge

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx; httpx.get('http://localhost:8000/health/live').raise_for_status()"]

ENTRYPOINT ["python", "-m", "uvicorn", "forge_gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
