---
layout: page
title: Infrastructure
description: Docker multi-stage build, Helm chart structure, Kubernetes resources, deployment profiles, and CI/CD for Forge AI.
parent: Technical
nav_order: 4
---

# Infrastructure

## Docker Multi-Stage Build

The `Dockerfile` uses a three-stage build to produce a minimal runtime image targeting less than 200MB.

<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">

  <!-- Stage 1: UI Builder -->
  <div style="padding: 1rem; background: white; border: 2px solid #1e1b4b; border-radius: 8px;">
    <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 0.75rem; padding-bottom: 0.5rem; border-bottom: 2px solid #e2e8f0;">Stage 1: UI Builder</div>
    <div style="font-size: 0.8rem; color: #64748b;">
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">1</span>
        <code style="font-size: 0.75rem;">FROM node:22-slim</code>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">2</span>
        npm ci (install deps)
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">3</span>
        npm run build (Vite)
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #dcfce7; color: #166534; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">&#10003;</span>
        <strong>/ui/dist</strong> (static assets)
      </div>
    </div>
  </div>

  <!-- Stage 2: Python Builder -->
  <div style="padding: 1rem; background: white; border: 2px solid #312e81; border-radius: 8px;">
    <div style="font-weight: 700; color: #312e81; margin-bottom: 0.75rem; padding-bottom: 0.5rem; border-bottom: 2px solid #e2e8f0;">Stage 2: Python Builder</div>
    <div style="font-size: 0.8rem; color: #64748b;">
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">1</span>
        <code style="font-size: 0.75rem;">FROM uv:python3.12-bookworm-slim</code>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">2</span>
        Copy agentweave dependency
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">3</span>
        Copy pyproject.toml + uv.lock
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">4</span>
        <code style="font-size: 0.75rem;">uv sync --frozen --no-dev</code>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">5</span>
        Copy source code
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">6</span>
        <code style="font-size: 0.75rem;">uv sync --frozen --no-editable</code>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #dcfce7; color: #166534; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">&#10003;</span>
        <strong>/build/forge-ai/.venv</strong>
      </div>
    </div>
  </div>

  <!-- Stage 3: Runtime -->
  <div style="padding: 1rem; background: white; border: 2px solid #4338ca; border-radius: 8px;">
    <div style="font-weight: 700; color: #4338ca; margin-bottom: 0.75rem; padding-bottom: 0.5rem; border-bottom: 2px solid #e2e8f0;">Stage 3: Runtime</div>
    <div style="font-size: 0.8rem; color: #64748b;">
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">1</span>
        <code style="font-size: 0.75rem;">FROM python:3.12-slim-bookworm</code>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">2</span>
        Create forge user (uid 999)
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #fef3c7; color: #92400e; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">&#8592;</span>
        Copy .venv from <strong>Stage 2</strong>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #fef3c7; color: #92400e; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">&#8592;</span>
        Copy static assets from <strong>Stage 1</strong>
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">5</span>
        EXPOSE 8000 9090
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.375rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #eef2ff; color: #4338ca; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">6</span>
        HEALTHCHECK /health/live
      </div>
      <div style="display: flex; align-items: center; gap: 0.5rem;">
        <span style="display: inline-block; width: 1.25rem; height: 1.25rem; background: #dcfce7; color: #166534; border-radius: 4px; text-align: center; font-size: 0.7rem; line-height: 1.25rem; font-weight: 700; flex-shrink: 0;">&#10003;</span>
        <strong>ENTRYPOINT uvicorn</strong>
      </div>
    </div>
  </div>

</div>

### Build Details

| Stage | Base Image | Purpose | Output |
|-------|-----------|---------|--------|
| UI Builder | `node:22-slim` | Build React SPA with Vite | `/ui/dist` (static HTML/JS/CSS) |
| Python Builder | `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` | Install Python dependencies and workspace packages | `/build/forge-ai/.venv` |
| Runtime | `python:3.12-slim-bookworm` | Minimal production image | Port 8000 (HTTP) + 9090 (metrics) |

### Build Context

The Dockerfile expects to be built from the **parent directory** of the forge-ai repository because AgentWeave is referenced as a sibling:

```bash
# Build from parent directory
docker build -f forge-ai/Dockerfile .
```

### Security Hardening

- Runs as non-root user `forge` (UID 999, GID 999)
- `PYTHONUNBUFFERED=1` for reliable logging
- `PYTHONDONTWRITEBYTECODE=1` to prevent `.pyc` file creation
- uv cache mount (`--mount=type=cache`) avoids embedding cache in layers

### HEALTHCHECK

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx; httpx.get('http://localhost:8000/health/live').raise_for_status()"]
```

**Source:** `Dockerfile`

## Helm Chart Structure

The Helm chart at `deploy/helm/forge/` provides a complete Kubernetes deployment with configurable profiles.

```
deploy/helm/forge/
  Chart.yaml              # Chart metadata (v0.1.0)
  values.yaml             # Default values (small profile)
  values.dev.yaml         # Development overrides
  values.prod.yaml        # Production overrides (large profile)
  templates/
    _helpers.tpl           # Template helper functions
    deployment.yaml        # Agent deployment
    gateway-deployment.yaml # Separate gateway (large profile)
    litellm-deployment.yaml # LiteLLM sidecar/dedicated
    redis-deployment.yaml  # Redis + Service + PVC
    service.yaml           # ClusterIP service
    ingress.yaml           # Ingress resource
    configmap.yaml         # forge.yaml ConfigMap
    secret.yaml            # API key secrets
    hpa.yaml               # Horizontal Pod Autoscaler
    pdb.yaml               # Pod Disruption Budget
    servicemonitor.yaml    # Prometheus ServiceMonitor
    NOTES.txt              # Post-install notes
```

## Deployment Profiles

### Profile Comparison

| Setting | Small (dev) | Medium (default) | Large (prod) |
|---------|-------------|------------------|--------------|
| **Agent replicas** | 1 | 1 | 3 |
| **Agent CPU request** | 50m | 100m | 500m |
| **Agent memory request** | 64Mi | 128Mi | 512Mi |
| **Agent CPU limit** | 500m | 500m | 2000m |
| **Agent memory limit** | 512Mi | 512Mi | 2Gi |
| **Gateway** | disabled (in-process) | disabled (in-process) | enabled (2 replicas) |
| **Gateway CPU request** | -- | -- | 250m |
| **Gateway memory request** | -- | -- | 256Mi |
| **LiteLLM mode** | embedded | embedded | dedicated |
| **Redis persistence** | disabled | disabled | enabled (5Gi PVC) |
| **Autoscaling** | disabled | disabled | enabled (3-20 replicas) |
| **HPA CPU target** | -- | -- | 60% |
| **Pod Disruption Budget** | disabled | disabled | enabled (min 2) |
| **ServiceMonitor** | disabled | disabled | enabled |

### Profile Usage

```bash
# Development
helm install forge ./deploy/helm/forge -f deploy/helm/forge/values.dev.yaml

# Production
helm install forge ./deploy/helm/forge -f deploy/helm/forge/values.prod.yaml

# Custom config
helm install forge ./deploy/helm/forge \
  --set-file forgeConfig=my-forge.yaml \
  --set secrets.OPENAI_API_KEY="sk-..."
```

## Kubernetes Resources

### Deployment (Agent)

The primary deployment runs the Forge agent container with:

- **Config volume mount:** ConfigMap mounted at `/app/config/forge.yaml`
- **Environment:** `FORGE_CONFIG_PATH=/app/config/forge.yaml`
- **Security context:** `runAsNonRoot: true`, UID/GID 999
- **Config checksum annotation:** Forces pod restart on ConfigMap changes
- **Optional LiteLLM sidecar:** When `litellm.mode=sidecar`, a LiteLLM container runs alongside the agent on port 4000

**Source:** `deploy/helm/forge/templates/deployment.yaml`

### Service

```yaml
type: ClusterIP
ports:
  - port: 8000    # HTTP (gateway/agent)
    name: http
  - port: 9090    # Prometheus metrics
    name: metrics
```

The service selector dynamically targets either the `gateway` component (when gateway is enabled) or the `agent` component (when gateway is in-process).

**Source:** `deploy/helm/forge/templates/service.yaml`

### Ingress

Optional Ingress resource with configurable:
- `ingressClassName` (e.g., `nginx`)
- Host-based routing
- TLS termination
- Custom annotations

**Source:** `deploy/helm/forge/templates/ingress.yaml`

### ConfigMap

Embeds the full `forge.yaml` content from `values.forgeConfig`. When no custom config is provided, generates a minimal default config using the chart name and profile.

**Source:** `deploy/helm/forge/templates/configmap.yaml`

### HPA (Horizontal Pod Autoscaler)

When `autoscaling.enabled=true`:

```yaml
apiVersion: autoscaling/v2
scaleTargetRef: Deployment/{name}-agent
minReplicas: 3      # (prod default)
maxReplicas: 20     # (prod default)
metrics:
  - type: Resource
    resource: cpu
    target: 60%     # (prod default)
```

**Source:** `deploy/helm/forge/templates/hpa.yaml`

### Redis

The Redis deployment supports four modes:

| Mode | Description |
|------|-------------|
| `single` | Single Redis pod without persistence |
| `single-pvc` | Single Redis pod with PersistentVolumeClaim |
| `ha` | High-availability (reserved for future implementation) |
| `external` | Skip deployment, use external Redis (configure `redis.external.host`) |

When persistence is enabled, a `PersistentVolumeClaim` is created with configurable `size` and `storageClass`.

**Source:** `deploy/helm/forge/templates/redis-deployment.yaml`

## Environment Promotion

<div style="display: flex; align-items: stretch; gap: 0; flex-wrap: wrap; padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #1e1b4b; border-radius: 8px 0 0 8px;">
    <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 0.5rem;">Development</div>
    <div style="font-size: 0.75rem; color: #64748b; font-style: italic; margin-bottom: 0.5rem;">values.dev.yaml</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>1 replica</li>
      <li>Debug logging</li>
      <li>Embedded LiteLLM</li>
      <li>No persistence</li>
    </ul>
  </div>
  <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 0.5rem;">
    <div style="color: #4338ca; font-weight: 700; font-size: 1rem;">→</div>
    <div style="font-size: 0.65rem; color: #64748b;">promote</div>
  </div>
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #3730a3;">
    <div style="font-weight: 700; color: #3730a3; margin-bottom: 0.5rem;">Staging</div>
    <div style="font-size: 0.75rem; color: #64748b; font-style: italic; margin-bottom: 0.5rem;">values.yaml</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>1 replica</li>
      <li>Info logging</li>
      <li>Embedded LiteLLM</li>
      <li>Optional persistence</li>
    </ul>
  </div>
  <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 0.5rem;">
    <div style="color: #4338ca; font-weight: 700; font-size: 1rem;">→</div>
    <div style="font-size: 0.65rem; color: #64748b;">promote</div>
  </div>
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #4338ca; border-radius: 0 8px 8px 0;">
    <div style="font-weight: 700; color: #4338ca; margin-bottom: 0.5rem;">Production</div>
    <div style="font-size: 0.75rem; color: #64748b; font-style: italic; margin-bottom: 0.5rem;">values.prod.yaml</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>3+ replicas, autoscaling</li>
      <li>Dedicated LiteLLM</li>
      <li>Redis PVC</li>
      <li>Monitoring enabled</li>
    </ul>
  </div>
</div>

### Development Environment

- `image.pullPolicy: Never` (local images)
- `FORGE_ENV=development`, `LOG_LEVEL=DEBUG`
- Minimal resource requests (50m CPU, 64Mi memory)

### Production Environment

- 3 agent replicas with autoscaling (3-20)
- Separate gateway deployment (2 replicas)
- Dedicated LiteLLM service
- Redis with 5Gi persistent storage
- ServiceMonitor for Prometheus
- Pod Disruption Budget (min 2 available)

**Source:** `deploy/helm/forge/values.dev.yaml`, `deploy/helm/forge/values.prod.yaml`
