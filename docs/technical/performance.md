---
layout: page
title: Performance
description: Async-first design, config hot-reload, tool registry hot-swap, LiteLLM modes, caching, and connection pooling for Forge AI.
parent: Technical
nav_order: 6
---

# Performance

## Async-First Architecture

All I/O operations in Forge AI use Python's `asyncio` framework. This design choice ensures the gateway can handle concurrent requests efficiently without thread-per-request overhead.

### Async Boundaries

| Component | Async Methods | Purpose |
|-----------|--------------|---------|
| `ForgeAgent` | `initialize()`, `run_conversational()`, `run_structured()` | Agent orchestration |
| `ToolSurfaceRegistry` | `build_and_swap()`, `force_swap()`, `clear()` | Atomic tool surface updates |
| `SecurityGate` | `__call__()`, `authenticate()`, `authorize_tool_call()`, `check_rate_limit()` | Security pipeline |
| `TrustPolicyEnforcer` | `evaluate()`, `check_origin()`, `check_rate_limit()` | Trust policy checks |
| `SlidingWindowRateLimiter` | `check()`, `peek()` | Rate limit checks |
| `AuditLogger` | `log_tool_call()` | Audit event recording |
| `OpenAPIToolBuilder` | `build()` | Remote spec fetching |
| All FastAPI routes | `async def` handlers | Request handling |

The gateway uses `uvicorn` as the ASGI server, configured via the Docker entrypoint:

```
python -m uvicorn forge_gateway.app:create_app --factory --host 0.0.0.0 --port 8000
```

**Source:** `packages/forge-agent/src/forge_agent/agent/core.py`, `Dockerfile`

## Config Hot-Reload

The `ConfigWatcher` monitors the `forge.yaml` file for changes using the `watchdog` library and triggers a reload cascade without restarting the application.

### Reload Flow

<div style="padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0); overflow-x: auto;">
  <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 1rem; font-size: 0.95rem;">Config Reload Flow</div>
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
        <td style="padding: 0.5rem;">Filesystem</td>
        <td style="padding: 0.5rem;">watchdog Observer</td>
        <td style="padding: 0.5rem;">File modified event detected</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">2</td>
        <td style="padding: 0.5rem;">watchdog</td>
        <td style="padding: 0.5rem;">DebouncedHandler</td>
        <td style="padding: 0.5rem;">on_modified() &rarr; check path matches &rarr; cancel previous timer &rarr; schedule callback</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #fffbeb;">
        <td style="padding: 0.5rem; font-weight: 600; color: #92400e;">--</td>
        <td style="padding: 0.5rem; font-style: italic; color: #92400e;" colspan="3">Debounce: wait 1 second for additional changes to settle</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">3</td>
        <td style="padding: 0.5rem;">DebouncedHandler</td>
        <td style="padding: 0.5rem;">Reload Callback</td>
        <td style="padding: 0.5rem;">_on_config_change(path) &rarr; load_config(path)</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #eef2ff;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;" rowspan="5">4</td>
        <td style="padding: 0.5rem;" colspan="3"><strong>Parallel subsystem updates:</strong></td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #eef2ff;">
        <td style="padding: 0.5rem;">Callback</td>
        <td style="padding: 0.5rem;">admin</td>
        <td style="padding: 0.5rem;">admin.set_state(new_config)</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #eef2ff;">
        <td style="padding: 0.5rem;">Callback</td>
        <td style="padding: 0.5rem;">programmatic / conversational</td>
        <td style="padding: 0.5rem;">set_config(new_config)</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #eef2ff;">
        <td style="padding: 0.5rem;">Callback</td>
        <td style="padding: 0.5rem;">auth</td>
        <td style="padding: 0.5rem;">set_api_key_config(new_config)</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0; background: #eef2ff;">
        <td style="padding: 0.5rem;">Callback</td>
        <td style="padding: 0.5rem;">security</td>
        <td style="padding: 0.5rem;">_init_security_gate(new_config)</td>
      </tr>
      <tr style="border-bottom: 1px solid #e2e8f0;">
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">5</td>
        <td style="padding: 0.5rem;">Callback</td>
        <td style="padding: 0.5rem;">Subsystems</td>
        <td style="padding: 0.5rem;">Schedule async tool rebuild</td>
      </tr>
      <tr>
        <td style="padding: 0.5rem; font-weight: 600; color: #4338ca;">6</td>
        <td style="padding: 0.5rem;">Callback</td>
        <td style="padding: 0.5rem;">Subsystems</td>
        <td style="padding: 0.5rem;">Refresh A2A agent card</td>
      </tr>
    </tbody>
  </table>
</div>

### Debounce Mechanism

The `_DebouncedHandler` prevents rapid file-change events (common with editors that perform multiple writes) from triggering excessive reloads. It uses `asyncio.AbstractEventLoop.call_later()` to schedule the callback after a 1-second quiet period. Each new event cancels the previous timer.

**Source:** `packages/forge-config/src/forge_config/watcher.py`

## Tool Registry Hot-Swap

The `ToolSurfaceRegistry` provides zero-downtime tool surface updates through atomic replacement.

### Hot-Swap Process

<div style="padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">
  <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 1rem; font-size: 0.95rem;">Tool Registry Hot-Swap Process</div>

  <div style="display: flex; flex-direction: column; gap: 0.5rem;">
    <!-- Step 1: Detect -->
    <div style="display: flex; align-items: center; gap: 0.75rem;">
      <span style="display: inline-block; width: 1.5rem; height: 1.5rem; background: #4338ca; color: white; border-radius: 50%; text-align: center; font-size: 0.75rem; line-height: 1.5rem; font-weight: 700; flex-shrink: 0;">1</span>
      <div style="padding: 0.5rem 1rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.85rem; flex-grow: 1;">
        <strong>Config change detected</strong> &rarr; compute version hash (<code>compute_surface_version(config)</code>)
      </div>
    </div>

    <!-- Decision -->
    <div style="display: flex; align-items: stretch; gap: 0.75rem; margin-left: 2.25rem;">
      <div style="display: flex; flex-direction: column; gap: 0.5rem; flex: 1;">
        <div style="padding: 0.5rem 1rem; background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; font-size: 0.85rem;">
          <strong>Hash matches current?</strong>
        </div>
        <div style="display: flex; gap: 1rem;">
          <div style="flex: 1; padding: 0.5rem; background: #dcfce7; border: 1px solid #86efac; border-radius: 6px; font-size: 0.8rem; text-align: center;">
            <strong style="color: #166534;">Yes</strong> &rarr; Skip rebuild (no-op)
          </div>
          <div style="flex: 2; padding: 0.5rem; background: #dbeafe; border: 1px solid #93c5fd; border-radius: 6px; font-size: 0.8rem; text-align: center;">
            <strong style="color: #1e40af;">No</strong> &rarr; Continue to build
          </div>
        </div>
      </div>
    </div>

    <!-- Step 2: Build -->
    <div style="display: flex; align-items: center; gap: 0.75rem;">
      <span style="display: inline-block; width: 1.5rem; height: 1.5rem; background: #4338ca; color: white; border-radius: 50%; text-align: center; font-size: 0.75rem; line-height: 1.5rem; font-weight: 700; flex-shrink: 0;">2</span>
      <div style="padding: 0.5rem 1rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.85rem; flex-grow: 1;">
        <strong>Build new tool set:</strong>
        <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.375rem;">
          <span style="padding: 0.25rem 0.5rem; background: #eef2ff; color: #3730a3; border-radius: 4px; font-size: 0.75rem;">OpenAPI tools (async)</span>
          <span style="color: #64748b;">→</span>
          <span style="padding: 0.25rem 0.5rem; background: #eef2ff; color: #3730a3; border-radius: 4px; font-size: 0.75rem;">Manual tools</span>
          <span style="color: #64748b;">→</span>
          <span style="padding: 0.25rem 0.5rem; background: #eef2ff; color: #3730a3; border-radius: 4px; font-size: 0.75rem;">Workflow tools</span>
          <span style="color: #64748b;">→</span>
          <span style="padding: 0.25rem 0.5rem; background: #eef2ff; color: #3730a3; border-radius: 4px; font-size: 0.75rem;">Peer agent tools</span>
        </div>
      </div>
    </div>

    <!-- Step 3: Swap -->
    <div style="display: flex; align-items: center; gap: 0.75rem;">
      <span style="display: inline-block; width: 1.5rem; height: 1.5rem; background: #4338ca; color: white; border-radius: 50%; text-align: center; font-size: 0.75rem; line-height: 1.5rem; font-weight: 700; flex-shrink: 0;">3</span>
      <div style="padding: 0.5rem 1rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.85rem; flex-grow: 1;">
        <strong>Atomic swap:</strong> Acquire <code>asyncio.Lock</code> &rarr; <code>self._tools = new_tools</code> &rarr; <code>self._version = new_version</code> &rarr; Release lock
      </div>
    </div>

    <!-- Step 4: MCP -->
    <div style="display: flex; align-items: center; gap: 0.75rem;">
      <span style="display: inline-block; width: 1.5rem; height: 1.5rem; background: #16a34a; color: white; border-radius: 50%; text-align: center; font-size: 0.75rem; line-height: 1.5rem; font-weight: 700; flex-shrink: 0;">4</span>
      <div style="padding: 0.5rem 1rem; background: #dcfce7; border: 1px solid #86efac; border-radius: 6px; font-size: 0.85rem; flex-grow: 1;">
        <strong>Rebuild MCP server</strong> with updated tool surface
      </div>
    </div>
  </div>
</div>

### Version-Based Change Detection

The registry uses a content hash (`compute_surface_version`) to detect whether the tool-related configuration has actually changed. If the hash matches the current version, the rebuild is skipped entirely. This prevents unnecessary work when non-tool config fields are modified.

### Concurrency Safety

All tool surface mutations are protected by an `asyncio.Lock`. This prevents concurrent config reloads from interleaving their build and swap operations. The lock is held only during the final swap (not during the build phase), minimizing contention.

**Source:** `packages/forge-agent/src/forge_agent/builder/registry.py`

## LiteLLM Modes

Forge AI supports three deployment modes for LiteLLM, each optimizing for different scale and isolation needs:

| Mode | Description | Use Case | Configuration |
|------|-------------|----------|---------------|
| **Embedded** | LiteLLM runs in-process within the Forge agent | Development, single-instance deployments | `litellm.mode: embedded` |
| **Sidecar** | LiteLLM runs as a separate container in the same pod | Medium scale, shared pod resources | `litellm.mode: sidecar`, requires `litellm.endpoint` |
| **External** | LiteLLM runs as a dedicated Kubernetes service | Production, independent scaling and management | `litellm.mode: dedicated`, requires `litellm.endpoint` |

### Mode Selection Impact

<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">

  <!-- Embedded -->
  <div style="padding: 1rem; background: white; border: 2px solid #1e1b4b; border-radius: 8px;">
    <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 0.5rem; font-size: 0.85rem;">Embedded</div>
    <div style="padding: 0.75rem; background: #eef2ff; border-radius: 6px; text-align: center;">
      <div style="font-weight: 600; color: #3730a3; font-size: 0.8rem;">Forge Agent</div>
      <div style="font-size: 0.7rem; color: #64748b;">+ LiteLLM in-process</div>
    </div>
    <div style="font-size: 0.7rem; color: #94a3b8; margin-top: 0.5rem; text-align: center; font-style: italic;">Single process</div>
  </div>

  <!-- Sidecar -->
  <div style="padding: 1rem; background: white; border: 2px solid #3730a3; border-radius: 8px;">
    <div style="font-weight: 700; color: #3730a3; margin-bottom: 0.5rem; font-size: 0.85rem;">Sidecar</div>
    <div style="display: flex; align-items: center; gap: 0.5rem;">
      <div style="flex: 1; padding: 0.5rem; background: #eef2ff; border-radius: 6px; text-align: center;">
        <div style="font-weight: 600; color: #3730a3; font-size: 0.8rem;">Forge Agent</div>
      </div>
      <span style="color: #64748b; font-size: 0.75rem;">&#8596;</span>
      <div style="flex: 1; padding: 0.5rem; background: #fef3c7; border-radius: 6px; text-align: center;">
        <div style="font-weight: 600; color: #92400e; font-size: 0.8rem;">LiteLLM</div>
        <div style="font-size: 0.7rem; color: #92400e;">:4000</div>
      </div>
    </div>
    <div style="font-size: 0.7rem; color: #94a3b8; margin-top: 0.5rem; text-align: center; font-style: italic;">Same pod</div>
  </div>

  <!-- External -->
  <div style="padding: 1rem; background: white; border: 2px solid #4338ca; border-radius: 8px;">
    <div style="font-weight: 700; color: #4338ca; margin-bottom: 0.5rem; font-size: 0.85rem;">External / Dedicated</div>
    <div style="display: flex; flex-direction: column; align-items: center; gap: 0.375rem;">
      <div style="padding: 0.5rem 0.75rem; background: #eef2ff; border-radius: 6px; text-align: center; width: 100%;">
        <div style="font-weight: 600; color: #3730a3; font-size: 0.8rem;">Forge Agent Pod</div>
      </div>
      <span style="color: #64748b; font-size: 0.75rem;">&#8597; HTTP</span>
      <div style="padding: 0.5rem 0.75rem; background: #fef3c7; border-radius: 6px; text-align: center; width: 100%;">
        <div style="font-weight: 600; color: #92400e; font-size: 0.8rem;">LiteLLM Service</div>
        <div style="font-size: 0.7rem; color: #92400e;">(separate Deployment)</div>
      </div>
    </div>
    <div style="font-size: 0.7rem; color: #94a3b8; margin-top: 0.5rem; text-align: center; font-style: italic;">Separate pods</div>
  </div>

</div>

When using `sidecar` or `external` modes, the `LLMRouter` passes the `api_base` setting to PydanticAI's `ModelSettings`, which routes LLM requests through the LiteLLM proxy endpoint.

**Source:** `packages/forge-config/src/forge_config/schema.py` (LiteLLMConfig), `packages/forge-agent/src/forge_agent/agent/llm.py`

## Redis for Session Storage

Redis provides persistent session storage and caching. In the Kubernetes deployment, Redis supports four modes:

| Mode | Description | Persistence |
|------|-------------|-------------|
| `single` | Single Redis pod, ephemeral | No |
| `single-pvc` | Single Redis pod with PersistentVolumeClaim | Yes |
| `ha` | High-availability (reserved) | Yes |
| `external` | External Redis service | Depends on provider |

In development, the `ConversationContext` uses in-memory storage. Redis integration provides durability across agent restarts in production.

**Source:** `deploy/helm/forge/values.yaml` (Redis configuration)

## Connection Pooling

Tool execution uses `httpx.AsyncClient` for HTTP calls to external APIs. The `httpx` library provides built-in connection pooling with:

- HTTP/2 support
- Connection reuse across requests to the same host
- Configurable timeout per tool (`ManualToolAPI.timeout`, default 30s)

Peer agent health checks (`POST /v1/admin/peers/{name}/ping`) create short-lived clients with a 5-second timeout.

**Source:** `packages/forge-gateway/src/forge_gateway/routes/admin.py` (peer ping)

## SPA Asset Caching

The React SPA built by Vite uses content-hash filenames for optimal caching:

| Resource | Caching Strategy |
|----------|-----------------|
| `/assets/*.js`, `/assets/*.css` | Vite content-hash filenames (e.g., `index-abc123.js`). Immutable, long-lived cache. |
| `/index.html` | Served with `Cache-Control: no-cache, no-store, must-revalidate`. Always fetches latest to pick up new asset hashes. |
| Other static files | Served directly by `StaticFiles` middleware with default caching. |

This approach ensures that:
- Asset files are aggressively cached by browsers and CDNs
- New deployments take effect immediately (index.html is never cached)
- No cache-busting query strings are needed

**Source:** `packages/forge-gateway/src/forge_gateway/app.py` (spa_fallback, Cache-Control headers)

## Performance Characteristics Summary

| Aspect | Approach | Benefit |
|--------|----------|---------|
| I/O model | Async-first (asyncio + uvicorn) | High concurrency without thread overhead |
| Config reload | File watcher with 1s debounce | Zero-downtime config updates |
| Tool swap | Atomic replace under asyncio.Lock | Consistent tool surface during updates |
| Version detection | Content hash comparison | Skip unnecessary rebuilds |
| LLM routing | LiteLLM with fallback chains | Automatic failover between providers |
| HTTP clients | httpx connection pooling | Efficient reuse of TCP connections |
| Static assets | Content-hash filenames | Optimal browser caching |
