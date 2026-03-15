---
layout: page
title: System Design
description: Architecture overview, component diagrams, request flow, technology stack, and design patterns for Forge AI.
parent: Technical
nav_order: 2
---

# System Design

## System Context

Forge AI sits between end users (via a React SPA control plane) and LLM providers, orchestrating tool-augmented AI agent interactions through a config-driven pipeline.

<div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem; padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">
  <div style="padding: 0.75rem 1.5rem; background: #1e1b4b; color: white; border-radius: 6px; font-weight: 600; font-size: 0.875rem;">Users / Operators</div>
  <div style="color: var(--color-text-muted, #64748b);">↓</div>
  <div style="padding: 0.75rem 1.5rem; background: #312e81; color: white; border-radius: 6px; font-weight: 600; font-size: 0.875rem;">Control Plane UI <span style="font-weight: 400; opacity: 0.8;">(React SPA)</span></div>
  <div style="color: var(--color-text-muted, #64748b);">↓</div>
  <div style="padding: 0.75rem 1.5rem; background: #3730a3; color: white; border-radius: 6px; font-weight: 600; font-size: 0.875rem;">Forge Gateway <span style="font-weight: 400; opacity: 0.8;">(FastAPI)</span></div>
  <div style="display: flex; gap: 2rem; align-items: flex-start; margin-top: 0.25rem;">
    <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
      <div style="color: var(--color-text-muted, #64748b);">↓</div>
      <div style="padding: 0.75rem 1.5rem; background: #4338ca; color: white; border-radius: 6px; font-weight: 600; font-size: 0.875rem;">Forge Agent <span style="font-weight: 400; opacity: 0.8;">(PydanticAI)</span></div>
      <div style="display: flex; gap: 1rem; margin-top: 0.25rem;">
        <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
          <div style="color: var(--color-text-muted, #64748b);">↓</div>
          <div style="padding: 0.5rem 1rem; background: #f59e0b; color: #1e1b4b; border-radius: 6px; font-weight: 600; font-size: 0.8rem;">LLM Provider <span style="font-weight: 400;">(LiteLLM)</span></div>
        </div>
        <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
          <div style="color: var(--color-text-muted, #64748b);">↓</div>
          <div style="padding: 0.5rem 1rem; background: #f59e0b; color: #1e1b4b; border-radius: 6px; font-weight: 600; font-size: 0.8rem;">External APIs <span style="font-weight: 400;">(OpenAPI / Manual)</span></div>
        </div>
        <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
          <div style="color: var(--color-text-muted, #64748b);">↓</div>
          <div style="padding: 0.5rem 1rem; background: #f59e0b; color: #1e1b4b; border-radius: 6px; font-weight: 600; font-size: 0.8rem;">Peer Agents <span style="font-weight: 400;">(A2A)</span></div>
        </div>
      </div>
    </div>
    <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
      <div style="color: var(--color-text-muted, #64748b);">↓</div>
      <div style="padding: 0.5rem 1rem; background: #f59e0b; color: #1e1b4b; border-radius: 6px; font-weight: 600; font-size: 0.8rem;">Redis <span style="font-weight: 400;">(Sessions / Cache)</span></div>
    </div>
  </div>
</div>

## Component Architecture

The system is structured as a **uv monorepo workspace** with four Python packages. Dependencies flow in a single direction, enforcing separation of concerns:

<div style="display: flex; align-items: stretch; gap: 0; flex-wrap: wrap; padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #1e1b4b; border-radius: 8px 0 0 8px; display: flex; flex-direction: column;">
    <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 0.5rem; font-size: 0.95rem;">forge-config</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>Pydantic schema</li>
      <li>YAML loader</li>
      <li>Hot-reload watcher</li>
      <li>Secret resolution</li>
    </ul>
  </div>
  <div style="display: flex; align-items: center; padding: 0 0.25rem; color: #4338ca; font-size: 1.25rem; font-weight: 700;">→</div>
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #312e81; display: flex; flex-direction: column;">
    <div style="font-weight: 700; color: #312e81; margin-bottom: 0.5rem; font-size: 0.95rem;">forge-security</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>Identity management</li>
      <li>Message signing</li>
      <li>Audit logging</li>
      <li>Rate limiting</li>
      <li>Trust policy</li>
      <li>SecurityGate</li>
    </ul>
  </div>
  <div style="display: flex; align-items: center; padding: 0 0.25rem; color: #4338ca; font-size: 1.25rem; font-weight: 700;">→</div>
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #3730a3; display: flex; flex-direction: column;">
    <div style="font-weight: 700; color: #3730a3; margin-bottom: 0.5rem; font-size: 0.95rem;">forge-agent</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>PydanticAI core</li>
      <li>Tool builders</li>
      <li>Tool registry</li>
      <li>LLM routing</li>
      <li>Peer calling</li>
    </ul>
  </div>
  <div style="display: flex; align-items: center; padding: 0 0.25rem; color: #4338ca; font-size: 1.25rem; font-weight: 700;">→</div>
  <div style="flex: 1; min-width: 180px; padding: 1rem; background: white; border: 2px solid #4338ca; border-radius: 0 8px 8px 0; display: flex; flex-direction: column;">
    <div style="font-weight: 700; color: #4338ca; margin-bottom: 0.5rem; font-size: 0.95rem;">forge-gateway</div>
    <ul style="margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: #64748b; line-height: 1.6;">
      <li>FastAPI app</li>
      <li>REST / MCP / A2A</li>
      <li>Admin API</li>
      <li>SPA serving</li>
      <li>Auth middleware</li>
    </ul>
  </div>
</div>

### Package Responsibilities

| Package | Role | Key Classes |
|---------|------|-------------|
| `forge-config` | Configuration schema, loading, and management | `ForgeConfig`, `ConfigWatcher`, `CompositeSecretResolver` |
| `forge-security` | Authentication, authorization, and audit | `SecurityGate`, `TrustPolicyEnforcer`, `SlidingWindowRateLimiter`, `AuditLogger` |
| `forge-agent` | AI agent orchestration and tool management | `ForgeAgent`, `ToolSurfaceRegistry`, `OpenAPIToolBuilder`, `ManualToolBuilder`, `WorkflowBuilder` |
| `forge-gateway` | HTTP gateway, routing, and UI serving | `create_app()`, `require_admin_key`, `security_dependency`, route modules |

## Request Flow

### Conversational Request (UI to Agent)

<div style="padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0); overflow-x: auto;">
  <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 1rem; font-size: 0.95rem;">Conversational Request Flow</div>
  <table style="width: 100%; border-collapse: collapse; font-size: 0.875rem;">
    <thead>
      <tr>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">Step</th>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">From</th>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">To</th>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">Action</th>
      </tr>
    </thead>
    <tbody>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">1</td>
        <td style="padding: 0.5rem;">React SPA</td>
        <td style="padding: 0.5rem;">Gateway</td>
        <td style="padding: 0.5rem;"><code>POST /v1/chat {message, session_id}</code></td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">2</td>
        <td style="padding: 0.5rem;">Gateway</td>
        <td style="padding: 0.5rem;">Auth Middleware</td>
        <td style="padding: 0.5rem;">require_admin_key (Bearer / X-API-Key) &rarr; validated key</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #fefce8;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">3</td>
        <td style="padding: 0.5rem;">Gateway</td>
        <td style="padding: 0.5rem;">SecurityGate</td>
        <td style="padding: 0.5rem;">security_dependency: JWT verification &rarr; Trust policy (origin + rate limit) &rarr; Audit log &rarr; returns CallerIdentity</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">4</td>
        <td style="padding: 0.5rem;">Gateway</td>
        <td style="padding: 0.5rem;">ForgeAgent</td>
        <td style="padding: 0.5rem;">run_conversational(message, session_id)</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">5</td>
        <td style="padding: 0.5rem;">ForgeAgent</td>
        <td style="padding: 0.5rem;">LLM Provider</td>
        <td style="padding: 0.5rem;">PydanticAI agent.run() &rarr; response with tool calls</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #fefce8;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">6</td>
        <td style="padding: 0.5rem;">ForgeAgent</td>
        <td style="padding: 0.5rem;">External API</td>
        <td style="padding: 0.5rem;">Execute tool via httpx &rarr; tool result</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">7</td>
        <td style="padding: 0.5rem;">ForgeAgent</td>
        <td style="padding: 0.5rem;">LLM Provider</td>
        <td style="padding: 0.5rem;">Continue with tool results &rarr; final response</td>
      </tr>
      <tr>
        <td style="padding: 0.5rem; font-weight: 600; color: #16a34a;">8</td>
        <td style="padding: 0.5rem;">Gateway</td>
        <td style="padding: 0.5rem;">React SPA</td>
        <td style="padding: 0.5rem;">JSON response (ForgeRunResult)</td>
      </tr>
    </tbody>
  </table>
</div>

### Admin Config Update (Hot-Reload)

<div style="padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0); overflow-x: auto;">
  <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 1rem; font-size: 0.95rem;">Admin Config Update (Hot-Reload) Flow</div>
  <table style="width: 100%; border-collapse: collapse; font-size: 0.875rem;">
    <thead>
      <tr>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">Step</th>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">From</th>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">To</th>
        <th style="text-align: left; padding: 0.5rem; border-bottom: 2px solid #e2e8f0; color: #1e1b4b;">Action</th>
      </tr>
    </thead>
    <tbody>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">1</td>
        <td style="padding: 0.5rem;">React SPA</td>
        <td style="padding: 0.5rem;">Admin API</td>
        <td style="padding: 0.5rem;"><code>PUT /v1/admin/config</code></td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">2</td>
        <td style="padding: 0.5rem;">Admin API</td>
        <td style="padding: 0.5rem;">Admin API</td>
        <td style="padding: 0.5rem;">Validate with <code>ForgeConfig.model_validate()</code></td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">3</td>
        <td style="padding: 0.5rem;">Admin API</td>
        <td style="padding: 0.5rem;">Filesystem</td>
        <td style="padding: 0.5rem;">Write forge.yaml to disk</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #fefce8;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">4</td>
        <td style="padding: 0.5rem;">Admin API</td>
        <td style="padding: 0.5rem;">ToolSurfaceRegistry</td>
        <td style="padding: 0.5rem;">build_and_swap(new_config): compute version hash &rarr; build OpenAPI + Manual + Workflow tools &rarr; atomic swap under asyncio.Lock</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #16a34a;">5</td>
        <td style="padding: 0.5rem;">Admin API</td>
        <td style="padding: 0.5rem;">React SPA</td>
        <td style="padding: 0.5rem;"><code>{success: true, reloaded: true}</code></td>
      </tr>
    </tbody>
  </table>
  <div style="margin-top: 1rem; padding: 0.75rem 1rem; background: #fffbeb; border: 1px solid #f59e0b; border-radius: 6px; font-size: 0.8rem; color: #92400e;">
    <strong>Async follow-up (watchdog):</strong> File change detected &rarr; Debounce (1s) &rarr; Reload config state &rarr; rebuild_mcp_server(registry)
  </div>
</div>

## Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Language** | Python 3.12+ | Async-first ecosystem, PydanticAI native support |
| **Package Manager** | uv | Fast dependency resolution, workspace support for monorepo |
| **Agent Framework** | PydanticAI | Type-safe agent interactions, TestModel for testing without API calls |
| **LLM Routing** | LiteLLM | Multi-provider support (OpenAI, Anthropic, etc.), fallback chains |
| **HTTP Framework** | FastAPI | Async, OpenAPI generation, dependency injection |
| **Config Validation** | Pydantic v2 | Strict validation, JSON Schema export, model serialization |
| **Frontend** | React + Vite | SPA with content-hash asset fingerprinting |
| **Security** | AgentWeave | Identity, signing, audit, trust policy framework |
| **MCP** | FastMCP | Model Context Protocol tool surface builder |
| **HTTP Client** | httpx | Async HTTP client with connection pooling |
| **File Watching** | watchdog | Cross-platform filesystem event monitoring |
| **Container** | Docker (multi-stage) | Three-stage build targeting less than 200MB |
| **Orchestration** | Kubernetes + Helm | Deployment profiles (small/medium/large) with HPA |
| **Caching** | Redis | Session storage, caching (optional) |
| **Metrics** | Prometheus | /metrics endpoint via prometheus_client |
| **Testing** | pytest + pytest-asyncio | Async test support, PydanticAI TestModel |
| **Linting** | ruff | Fast linting and formatting, 100-char line length |
| **Type Checking** | mypy (strict) | Static type analysis across all packages |

## Design Patterns

### Config-Driven Architecture

The entire system is driven by a single `forge.yaml` configuration file. The `ForgeConfig` Pydantic model validates all settings at load time, and every subsystem reads its configuration from this central schema. This means:

- Tool surfaces are declared, not coded
- Agent personas are defined in YAML
- Security policies are configured, not hard-coded
- LLM routing is declarative

**Source:** `packages/forge-config/src/forge_config/schema.py` (ForgeConfig root model)

### Async Pipeline

All I/O operations use `asyncio`. The gateway, agent, tool builders, security gate, and rate limiter are all async-first. The `ForgeAgent.run_conversational()` and `run_structured()` methods are async, and tool execution uses `httpx.AsyncClient` for non-blocking HTTP calls.

**Source:** `packages/forge-agent/src/forge_agent/agent/core.py` (ForgeAgent)

### Tool Registry Hot-Swap

The `ToolSurfaceRegistry` maintains the current tool set and supports atomic replacement. When configuration changes:

1. A new version hash is computed from the config content
2. If the hash differs, new tools are built from all sources (OpenAPI, manual, workflow, peer)
3. The tool list is swapped atomically under an `asyncio.Lock`
4. The MCP server is rebuilt with updated tools

This allows zero-downtime tool surface updates without restarting the gateway.

**Source:** `packages/forge-agent/src/forge_agent/builder/registry.py` (ToolSurfaceRegistry.build_and_swap)

### SPA with API Gateway

The FastAPI gateway serves both the API and the React SPA:

- `/assets/*` -- Static files served by `StaticFiles` middleware
- Known SPA routes (`/`, `/config`, `/tools`, `/chat`, `/peers`, `/security`, `/guide`) -- Serve `index.html` with `Cache-Control: no-cache`
- API routes (`/v1/*`, `/health/*`, `/metrics`, `/mcp`) -- JSON API endpoints
- All other paths -- Return 404

**Source:** `packages/forge-gateway/src/forge_gateway/app.py` (create_app, spa_fallback)

### Separation of Auth Concerns

Authentication is split into two layers for different route groups:

1. **Admin auth** (`require_admin_key`) -- API key validation for the `/v1/admin/*` control plane endpoints. Uses constant-time comparison via `hmac.compare_digest`.

2. **Agent security** (`security_dependency`) -- Full SecurityGate pipeline for agent-facing routes. Supports JWT verification, trust policy enforcement, rate limiting, and audit logging.

**Source:** `packages/forge-gateway/src/forge_gateway/auth.py` and `security.py`

## Scalability

### Development (Small Profile)

- Single replica agent deployment
- In-process gateway (no separate pod)
- Embedded LiteLLM (in-process)
- Redis without persistence
- No autoscaling

### Production (Large Profile)

- 3 agent replicas (baseline)
- Separate gateway deployment (2 replicas)
- Dedicated LiteLLM service
- Redis with PVC persistence (5Gi)
- HPA autoscaling: 3-20 replicas at 60% CPU target
- Pod Disruption Budget: minimum 2 available
- ServiceMonitor for Prometheus scraping

The production profile enables the gateway as a separate deployment, allowing the API/SPA serving layer to scale independently from the agent compute layer.

**Source:** `deploy/helm/forge/values.yaml` (defaults), `values.prod.yaml` (production overrides)
