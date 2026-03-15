---
layout: page
title: API Reference
description: Complete endpoint documentation for the Forge AI Gateway REST API, including request/response schemas, authentication, and error codes.
parent: Developer Guide
nav_order: 3
---

# API Reference

The Forge AI Gateway exposes a REST API through FastAPI. All endpoints are served from a single uvicorn process, typically on port 8000.

Interactive API documentation is auto-generated at:
- **Swagger UI**: `GET /docs`
- **ReDoc**: `GET /redoc`
- **OpenAPI JSON**: `GET /openapi.json`

## Authentication

The API uses two authentication mechanisms depending on the route category:

### Admin Routes (`/v1/admin/*`)

Require an admin API key via either:

| Method | Header |
|--------|--------|
| Bearer token | `Authorization: Bearer <key>` |
| API key header | `X-API-Key: <key>` |

Keys are configured in `forge.yaml` under `security.api_keys` and resolved from environment variables or Kubernetes secrets. Constant-time comparison is used for validation.

### Agent Routes (`/v1/agent/*`, `/v1/chat/*`, `/a2a/tasks`)

Protected by the `SecurityGate` dependency, which runs the full AgentWeave pipeline:

1. **Identity extraction** -- Bearer token, `X-Caller-ID` header, or `caller_id` query parameter
2. **Authentication** -- JWT verification (when configured)
3. **Trust policy** -- Origin-based trust evaluation
4. **Rate limiting** -- Per-caller sliding window (configurable RPM)
5. **Audit logging** -- Structured event recording

In **development mode** (AgentWeave disabled), all agent routes allow unauthenticated access with a synthetic `dev-anonymous` identity.

## Error Response Format

All error responses follow a consistent structure:

```json
{
  "error": "Short error description",
  "detail": "Detailed error message (optional)",
  "code": "ERROR_CODE (optional)"
}
```

### Standard HTTP Status Codes

| Code | Meaning |
|------|---------|
| 400 | Bad request -- invalid input or validation failure |
| 401 | Unauthorized -- missing or invalid credentials |
| 403 | Forbidden -- authentication succeeded but access denied |
| 404 | Not found |
| 429 | Too many requests -- rate limit exceeded |
| 500 | Internal server error |
| 503 | Service unavailable -- agent not initialized or system not ready |

---

## Health Routes

Health check endpoints used by Kubernetes probes and load balancers. No authentication required.

### GET /health/live

Liveness probe. Returns 200 as long as the process is running.

**Response** `200 OK`

```json
{
  "status": "ok",
  "version": "",
  "components": {}
}
```

---

### GET /health/ready

Readiness probe. Returns 200 only after full initialization (config loaded, agent built, tools registered).

**Response** `200 OK`

```json
{
  "status": "ready",
  "version": "",
  "components": {}
}
```

**Response** `503 Service Unavailable` (during startup)

```json
{
  "detail": "Not ready"
}
```

---

### GET /health/startup

Startup probe. Returns 200 once the application lifespan has begun.

**Response** `200 OK`

```json
{
  "status": "started",
  "version": "",
  "components": {}
}
```

**Response** `503 Service Unavailable` (before lifespan starts)

```json
{
  "detail": "Starting up"
}
```

---

## Admin Routes

All admin routes require admin API key authentication. Prefix: `/v1/admin`.

### GET /v1/admin/config

Return the current resolved configuration with secrets redacted.

**Authentication**: Admin API key

**Response** `200 OK`

```json
{
  "config": {
    "metadata": {
      "name": "my-forge-agent",
      "version": "0.1.0",
      "description": "Example Forge AI deployment",
      "environment": "development"
    },
    "llm": { "..." },
    "tools": { "..." },
    "security": {
      "api_keys": {
        "enabled": true,
        "keys": [{ "source": "env", "name": "***REDACTED***" }]
      }
    },
    "agents": { "..." }
  },
  "path": "forge.yaml"
}
```

**Response** `500 Internal Server Error`

```json
{
  "error": "No config loaded"
}
```

---

### PUT /v1/admin/config

Validate and write a new configuration, triggering hot-reload.

**Authentication**: Admin API key

**Request Body**

```json
{
  "config": {
    "metadata": { "name": "updated-agent", "version": "0.2.0" },
    "llm": { "default_model": "gpt-4o", "temperature": 0.5 },
    "tools": {},
    "security": {},
    "agents": {}
  }
}
```

**Response** `200 OK`

```json
{
  "success": true,
  "reloaded": true,
  "message": "Config updated and tools reloaded"
}
```

**Response** `400 Bad Request` (validation failure)

```json
{
  "detail": "1 validation error for ForgeConfig\nmetadata -> name\n  field required..."
}
```

---

### GET /v1/admin/config/schema

Return the JSON Schema for the `ForgeConfig` Pydantic model.

**Authentication**: Admin API key

**Response** `200 OK`

Returns a standard JSON Schema object describing all configuration fields, types, defaults, and constraints. This is generated directly from the Pydantic v2 model.

---

### GET /v1/admin/tools

List all registered tools with metadata.

**Authentication**: Admin API key

**Response** `200 OK`

```json
[
  {
    "name": "find_pets",
    "description": "Find pets by status",
    "source": "configured"
  },
  {
    "name": "get_weather",
    "description": "Get current weather for a location",
    "source": "configured"
  },
  {
    "name": "peer_data_query",
    "description": "Query data from peer agent",
    "source": "peer"
  }
]
```

Returns an empty array if no agent is initialized.

---

### POST /v1/admin/tools/preview

Dry-run: parse an OpenAPI spec and return the tool list without registering.

**Authentication**: Admin API key

**Request Body**

```json
{
  "source": {
    "name": "petstore",
    "url": "https://petstore3.swagger.io/api/v3/openapi.json",
    "namespace": "petstore",
    "include_operations": ["findPetsByStatus", "getPetById"]
  }
}
```

**Response** `200 OK`

```json
{
  "tools": [
    {
      "name": "petstore_findPetsByStatus",
      "description": "Finds Pets by status",
      "source": "openapi"
    },
    {
      "name": "petstore_getPetById",
      "description": "Find pet by ID",
      "source": "openapi"
    }
  ],
  "count": 2
}
```

**Response** `400 Bad Request`

```json
{
  "detail": "Failed to fetch OpenAPI spec: ..."
}
```

---

### GET /v1/admin/sessions

List active agent sessions.

**Authentication**: Admin API key

**Response** `200 OK`

```json
[
  {
    "session_id": "abc-123",
    "message_count": 5,
    "agent": "assistant"
  },
  {
    "session_id": "def-456",
    "message_count": 12,
    "agent": "analyst"
  }
]
```

Returns an empty array if no agent is initialized.

---

### DELETE /v1/admin/sessions/{session_id}

Terminate a specific session.

**Authentication**: Admin API key

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | The session ID to terminate |

**Response** `200 OK`

```json
{
  "status": "deleted",
  "session_id": "abc-123"
}
```

**Response** `404 Not Found`

```json
{
  "error": "Session 'abc-123' not found"
}
```

---

### GET /v1/admin/peers

List configured peer agents with connection status.

**Authentication**: Admin API key

**Response** `200 OK`

```json
[
  {
    "name": "data-forge",
    "endpoint": "https://data-forge.hvs.internal",
    "trust_level": "high",
    "capabilities": ["data_query", "reporting"],
    "status": "unknown"
  },
  {
    "name": "security-forge",
    "endpoint": "https://security-forge.hvs.internal",
    "trust_level": "medium",
    "capabilities": ["threat_analysis", "audit"],
    "status": "unknown"
  }
]
```

---

### POST /v1/admin/peers/{name}/ping

Health-check a specific peer. Includes SSRF protection that blocks private/internal IP addresses and known internal hostnames.

**Authentication**: Admin API key

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | The peer name from config |

**Response** `200 OK` (reachable)

```json
{
  "name": "data-forge",
  "status": "reachable",
  "http_status": 200
}
```

**Response** `200 OK` (unreachable)

```json
{
  "name": "data-forge",
  "status": "unreachable",
  "error": "ConnectTimeout: ..."
}
```

**Response** `404 Not Found`

```json
{
  "detail": "Peer 'unknown-peer' not found"
}
```

**Response** `400 Bad Request` (SSRF protection)

```json
{
  "detail": "Peer endpoint targets a private or internal network"
}
```

---

## Programmatic Routes

Structured agent invocation for automation and integrations.

### POST /v1/agent/invoke

Invoke the agent programmatically with structured input and optional typed output.

**Authentication**: SecurityGate

**Request Body**

```json
{
  "intent": "Find all available pets with status 'available'",
  "params": {
    "status": "available"
  },
  "tool_hints": ["find_pets"],
  "output_schema": {
    "type": "object",
    "properties": {
      "pets": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": { "type": "string" },
            "status": { "type": "string" }
          }
        }
      }
    }
  },
  "session_id": "optional-session-id",
  "agent": "analyst"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `intent` | string | Yes | Description of what the agent should produce |
| `params` | object | No | Parameters to include in the prompt |
| `tool_hints` | string[] | No | Tool names to prioritize (intersected with persona filter) |
| `output_schema` | object | No | JSON Schema for typed output |
| `session_id` | string | No | Session ID for context continuity |
| `agent` | string | No | Named agent persona to use (from config) |

**Response** `200 OK`

```json
{
  "result": {
    "pets": [
      { "name": "Buddy", "status": "available" },
      { "name": "Max", "status": "available" }
    ]
  },
  "session_id": "optional-session-id",
  "tools_used": ["find_pets"],
  "model": "openai/gpt-4o"
}
```

**Response** `503 Service Unavailable`

```json
{
  "detail": "Agent not initialized"
}
```

**Response** `500 Internal Server Error`

```json
{
  "detail": "Internal server error"
}
```

---

## Conversational Routes

Chat-style interactions with optional streaming.

### POST /v1/chat/completions

Send a conversational message to the agent.

**Authentication**: SecurityGate

**Request Body**

```json
{
  "message": "What is the weather in San Francisco?",
  "session_id": "chat-session-123",
  "stream": false,
  "agent": "assistant"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | The user's message |
| `session_id` | string | No | Session ID for conversation continuity (auto-generated if omitted) |
| `stream` | boolean | No | Enable SSE streaming (default: false) |
| `agent` | string | No | Named agent persona to use |

**Response** `200 OK` (non-streaming)

```json
{
  "message": "The current weather in San Francisco is 62F with partly cloudy skies.",
  "session_id": "chat-session-123",
  "tools_used": ["get_weather"],
  "model": "openai/gpt-4o"
}
```

**Response** `200 OK` (streaming, `stream: true`)

Returns a Server-Sent Events (SSE) stream with `Content-Type: text/event-stream`:

```
data: {"chunk": "The current ", "session_id": "chat-session-123"}

data: {"chunk": "weather in San Francisco ", "session_id": "chat-session-123"}

data: {"chunk": "is 62F with partly cloudy skies.", "session_id": "chat-session-123"}

data: [DONE]
```

SSE headers include `Cache-Control: no-cache`, `Connection: keep-alive`, and `X-Accel-Buffering: no`.

**Response** `503 Service Unavailable`

```json
{
  "detail": "Agent not initialized"
}
```

---

## A2A Routes

Agent-to-Agent protocol endpoints for peer discovery and task delegation.

### GET /a2a/agent-card

Return the agent card for A2A discovery. No authentication required.

**Response** `200 OK`

```json
{
  "name": "my-forge-agent",
  "description": "Example Forge AI deployment",
  "capabilities": ["find_pets", "get_weather", "enrich_contact"],
  "version": "0.1.0",
  "endpoint": "http://localhost:8000",
  "protocols": ["a2a", "rest", "mcp"]
}
```

---

### POST /a2a/tasks

Submit a task for the agent to execute. Used by peer Forge instances.

**Authentication**: SecurityGate

**Request Body**

```json
{
  "task_type": "Look up weather data for the given location",
  "payload": {
    "location": "San Francisco"
  },
  "caller_id": "data-forge"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_type` | string | Yes | Description of the task (maps to agent intent) |
| `payload` | object | No | Task parameters |
| `caller_id` | string | No | Identity of the calling agent |

**Response** `200 OK` (success)

```json
{
  "status": "completed",
  "result": {
    "result": "The weather in San Francisco is 62F with partly cloudy skies."
  },
  "error": null
}
```

**Response** `200 OK` (failure)

```json
{
  "status": "failed",
  "result": null,
  "error": "Internal server error"
}
```

**Response** `503 Service Unavailable`

```json
{
  "detail": "Agent not initialized"
}
```

---

## Metrics

### GET /metrics

Expose Prometheus metrics in text exposition format.

**Authentication**: None

**Response** `200 OK` (`text/plain`)

```
# HELP python_gc_objects_collected_total ...
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 1234.0
...
```

Returns a comment placeholder when `prometheus_client` is not installed.

---

## MCP Endpoint

The MCP (Model Context Protocol) server is mounted at `/mcp` as an ASGI sub-application. It exposes the same tool surface as the REST API but uses the MCP wire protocol for LLM-to-LLM tool discovery and invocation.

The MCP server is built from the agent's `ToolSurfaceRegistry` using the FastMCP library and is rebuilt automatically on config hot-reload.

---

## Request/Response Models Reference

All models are defined in `packages/forge-gateway/src/forge_gateway/models.py`.

### InvokeRequest

```python
class InvokeRequest(BaseModel):
    intent: str
    params: dict[str, Any] = {}
    tool_hints: list[str] = []
    output_schema: dict[str, Any] | None = None
    session_id: str | None = None
    agent: str | None = None
```

### InvokeResponse

```python
class InvokeResponse(BaseModel):
    result: Any
    session_id: str | None = None
    tools_used: list[str] = []
    model: str | None = None
```

### ConversationRequest

```python
class ConversationRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = False
    agent: str | None = None
```

### ConversationResponse

```python
class ConversationResponse(BaseModel):
    message: str
    session_id: str
    tools_used: list[str] = []
    model: str | None = None
```

### HealthResponse

```python
class HealthResponse(BaseModel):
    status: str
    version: str = ""
    components: dict[str, str] = {}
```

### AdminConfigResponse

```python
class AdminConfigResponse(BaseModel):
    config: dict[str, Any]
    path: str = ""
```

### AdminToolInfo

```python
class AdminToolInfo(BaseModel):
    name: str
    description: str = ""
    source: str = "configured"  # "configured" | "peer" | "openapi"
```

### AdminSessionResponse

```python
class AdminSessionResponse(BaseModel):
    session_id: str
    message_count: int = 0
    agent: str | None = None
```

### AdminPeerResponse

```python
class AdminPeerResponse(BaseModel):
    name: str
    endpoint: str
    trust_level: str = "low"  # "high" | "medium" | "low"
    capabilities: list[str] = []
    status: str = "unknown"  # "reachable" | "unreachable" | "unknown"
```
