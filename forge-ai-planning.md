# FORGE — Dynamic MCP Agent System
## Architecture Specification v1.0

---

## What We're Building

**Forge** is a configuration-driven AI agent system that dynamically constructs MCP tool surfaces from a human-editable config file, routes LLM calls through LiteLLM for multi-provider flexibility, secures all agent communication through AgentWeave, and exposes two first-class interfaces: a conversational interface for direct human use and a structured API interface for application consumption. The whole thing runs in a container and scales horizontally on Kubernetes.

The core thesis: **config file in → working AI agent with full API tool access out.** No code required to add a new tool.

---

## System Name and Repository

```
forge/
├── forge-agent/          # Core agent process
├── forge-config/         # Config schema, validation, hot-reload
├── forge-gateway/        # Dual interface (REST + conversational)
├── forge-security/       # AgentWeave integration layer
├── deploy/               # Kubernetes manifests, Helm chart
└── docs/                 # Architecture, runbook, API reference
```

---

## Architecture Pillars

### 1. The Config Engine

The human-facing surface. A single YAML file that describes everything about what tools exist and where they live. No Python required to add a new capability.

**Config categories:**

**A) OpenAPI-sourced tools** — Point at any OpenAPI 3.x spec (remote URL or local path) and Forge generates a complete MCP tool surface via FastMCP 3.0's native OpenAPI provider. This is the high-leverage path. One spec file = tens of tools, automatically documented, typed, and callable.

**B) Manual REST tool definitions** — For APIs that don't have OpenAPI specs. Human defines endpoint, method, parameters, auth, and response mapping. More work per tool, but complete coverage.

**C) Chained tool workflows** — Named multi-step sequences that the agent can call as a single tool. Compose lower-level tools into business-logic-aware operations. Defined entirely in config.

**D) AgentWeave peer definitions** — Define other Forge agents this instance can communicate with, their identities, and the trust policy governing those interactions.

**Config hot-reload:** A file watcher (watchdog) detects changes to the config and triggers a graceful tool surface rebuild without restarting the process. Running calls are drained. New calls pick up the updated tool set. This means you can add a new API integration to a live running system with zero downtime.

---

### 2. Dynamic Tool Builder

Built on FastMCP 3.0. The tool builder reads the config engine output and constructs the live MCP server.

**For OpenAPI sources:** FastMCP 3.0's built-in OpenAPI provider handles this natively. You give it a spec URL or dict and it generates properly typed tools automatically. Forge wraps this with auth injection (pulling credentials from K8s secrets or environment), namespace assignment, and visibility filtering (which operations to expose vs. hide).

**For manual REST definitions:** Forge generates FastMCP tool functions dynamically at startup using Python's type system and `functools`. Each tool gets a proper docstring (the LLM reads this), typed parameters from the config schema, and consistent error handling. The key design constraint: tool generation must be deterministic. Same config → identical tool surface, always.

**Tool registration lifecycle:**
```
Config Load → Validate Schema → Resolve Secrets →
Build Tool Functions → Register with FastMCP →
Health Check → Expose to Clients
```

**Tool surface versioning:** Every config change increments a surface version. Clients can request a specific version or always-latest. This enables blue/green tool deployments.

---

### 3. LiteLLM Integration Layer

Forge does not talk to LLM providers directly. All inference goes through LiteLLM, either as an embedded router or a sidecar proxy.

**Why this matters:** Forge is provider-agnostic from day one. You put `provider: anthropic/claude-sonnet-4-20250514` in the config and it works. Swap to `openai/gpt-4o` tomorrow without touching anything else. LiteLLM handles the translation.

**LiteLLM responsibilities in Forge:**
- Provider routing (Anthropic, OpenAI, Gemini, Mistral, local Ollama, Azure OpenAI, all of it)
- Fallback chains — if primary provider fails, try secondary automatically
- Cost tracking per conversation, per API key, per tenant
- Rate limit management across providers
- Prompt caching awareness (Anthropic's cache_control, etc.)
- Usage logging for chargeback and observability

**Deployment choice:** In dev/small deployments, embed LiteLLM as a library. In production K8s, run it as a dedicated sidecar or shared service. The config toggles this:
```yaml
litellm:
  mode: sidecar          # or embedded, or external
  endpoint: http://litellm:4000
  fallback_chain:
    - anthropic/claude-sonnet-4-20250514
    - openai/gpt-4o
    - ollama/qwen3-coder
```

**Model routing by task:** Forge can route different tool categories to different models. High-stakes financial tool calls → Opus. Simple data retrieval → Haiku. This is configurable per tool in the config file.

---

### 4. AgentWeave Security Layer

Every message in and out of Forge passes through the AgentWeave layer. This is not optional and cannot be bypassed. The architectural principle: **security is structural, not advisory.**

**What AgentWeave provides for Forge:**

**Identity:** Every Forge instance has a cryptographic identity (keypair) generated at startup and registered with the AgentWeave trust fabric. This identity is used to sign all outbound messages and verify all inbound ones.

**Agent-to-Agent (A2A) communication:** When Forge talks to another agent — another Forge instance, a specialized sub-agent, or a third-party A2A-compliant agent — AgentWeave handles the mutual authentication, message signing, and replay-attack prevention. No agent can impersonate another.

**Tool call auditing:** Every tool invocation is logged with: caller identity, tool name, parameters (with PII scrubbing), timestamp, result hash, latency. This creates an immutable audit trail per conversation.

**Secret resolution:** API keys and credentials defined in the config never appear in the config in plaintext. They reference K8s Secrets or environment variables. AgentWeave's secret resolver pulls them at tool invocation time, injects them into requests, and ensures they never appear in logs or tool descriptions.

**Rate limiting and abuse prevention:** Per-identity rate limits enforced at the AgentWeave layer, before any tool call or LLM call happens. Brute force protection for the API gateway.

**Trust policy engine:** Config defines what external agents are allowed to do. An agent from the internet can call read tools but not write tools. An internal HVS agent has full access. This is enforced at the boundary.

---

### 5. Dual Interface Gateway

Forge is accessible two ways. The same underlying agent and tool surface serves both.

**Interface A: Conversational**

A streaming HTTP endpoint that accepts natural language and returns responses with tool call reasoning inline. This is the human-facing path — Claude Desktop, custom UIs, Open WebUI. Standard MCP protocol over HTTP (streamable HTTP transport per FastMCP 3.0).

Users can say "use the Stripe tool and the GitHub tool to show me a combined view of payments and recent deployments." The agent figures out which tools to call, in what order, and synthesizes the result.

**Interface B: Programmatic API**

A structured REST/JSON API for applications to consume. Applications don't need to know about MCP or agent reasoning — they just call a clean API.

```
POST /v1/agent/invoke
{
  "intent": "get_payment_summary",
  "params": { "account_id": "acct_123", "period": "last_30_days" },
  "tools": ["stripe_list_charges", "stripe_get_balance"],  // optional hints
  "stream": false
}
```

The agent receives this, selects appropriate tools, executes them, synthesizes the output, and returns a structured JSON response. Applications get AI-synthesized answers without managing agent logic themselves.

**Interface C: Agent-to-Agent**

A2A-compatible endpoint for other agents to call this Forge instance as a sub-agent. Secured by AgentWeave. Other Forge instances, ADK agents, LangGraph agents — anyone implementing A2A can call this. The "Agent Card" (A2A capability descriptor) is auto-generated from the config.

**Gateway routing:**
```
Incoming Request
    → AgentWeave Authentication
    → Rate Limit Check
    → Interface Classifier (MCP / REST / A2A)
    → Forge Agent Core
    → Tool Execution
    → Response Synthesis
    → AgentWeave Audit Log
    → Response
```

---

### 6. The Agent Core

The brain of the system. A PydanticAI-based agent (chosen for type safety, Python-native feel, and excellent structured output support).

**Why PydanticAI over LangGraph or CrewAI here:** Forge is a single-agent system with a tool surface that can be very wide. It doesn't need graph-based orchestration or crew metaphors. It needs a tight, fast, typed agent loop with reliable tool calling. PydanticAI is that.

**Agent loop:**
```
Receive Request (from any interface)
    → Parse intent
    → Select tools from available surface
    → Execute tool calls (parallelized where possible)
    → Handle tool errors with retry/fallback
    → Synthesize response in requested format
    → Return to interface layer
```

**Parallel tool execution:** When the agent identifies multiple independent tool calls (no data dependency between them), it executes them concurrently with asyncio. This is critical for performance — don't serialize what can parallelize.

**Structured output modes:** When the programmatic API is used, the agent returns typed Pydantic models, not raw text. The response schema is optionally defined in the config or inferred from the tool outputs.

**Context window management:** Long conversations use a sliding window with periodic summarization. The agent maintains a working memory dict for within-session state.

---

## Data Flow Diagrams

### Human Conversational Flow
```
User Message
    → Forge Gateway (HTTP/MCP)
    → AgentWeave: Authenticate + Rate Check
    → LiteLLM: Route to LLM Provider
    → LLM: Intent Parse + Tool Selection
    → Tool Builder: Execute Selected Tools (parallel)
        → External APIs (via config-defined auth)
    → LLM: Synthesize Response
    → Stream to User
    → AgentWeave: Audit Log
```

### Application Programmatic Flow
```
App POST /v1/agent/invoke
    → AgentWeave: API Key Auth + JWT Verify
    → Request Schema Validation (Pydantic)
    → Agent Core: Plan tool execution
    → Tool execution (parallel where safe)
    → Pydantic model response assembly
    → JSON response to App
    → AgentWeave: Billing event + Audit
```

### Config Hot-Reload Flow
```
Config file modified
    → Watchdog detects change
    → Config Engine: Validate new config
        → If invalid: reject, keep current, alert
        → If valid: continue
    → Tool Builder: Build new tool surface
    → Drain in-flight requests (graceful, max 30s)
    → Swap tool surface atomically
    → Log surface version increment
```

---

## Kubernetes Architecture

### Pod Layout

### Cluster-Aware Sizing

Forge resource sizing is driven by a `clusterProfile` Helm value, not hardcoded. At install time, declare what kind of cluster you're deploying into and the chart applies the appropriate resource envelope. No manual tuning required for standard deployments.

```yaml
# values.yaml
clusterProfile: small   # small | medium | large | custom
```

The three built-in profiles:

| Parameter | `small` | `medium` | `large` |
|---|---|---|---|
| **Target cluster** | 3-node homelabs, dev, single-client | 5-10 node shared infra | 10+ node production |
| **forge-agent replicas (min/max)** | 1 / 2 | 2 / 8 | 3 / 20 |
| **forge-agent CPU request/limit** | 100m / 500m | 250m / 1 | 500m / 2 |
| **forge-agent RAM request/limit** | 128Mi / 512Mi | 256Mi / 1Gi | 512Mi / 2Gi |
| **forge-gateway** | In-process (no split) | In-process | Separate pod set |
| **litellm** | Embedded library | Shared sidecar | Dedicated deployment |
| **redis** | Single pod, no persistence | Single pod + PVC | HA via redis-ha chart |
| **HPA trigger** | CPU 70% | CPU 60% + queue depth | Queue depth primary |
| **PodDisruptionBudget** | minAvailable: 1 | minAvailable: 1 | minAvailable: 2 |

**`custom` profile:** Disables all profile defaults and expects explicit resource values in `values.yaml`. For operators who know exactly what they have and want full control.

```yaml
# Custom profile example
clusterProfile: custom
forgeAgent:
  replicas:
    min: 1
    max: 4
  resources:
    requests:
      cpu: "150m"
      memory: "192Mi"
    limits:
      cpu: "750m"
      memory: "768Mi"
```

The Helm chart uses a `_helpers.tpl` lookup to merge the active profile defaults with any explicit overrides — explicit values always win, profile fills in the rest. This means you can start with `small`, override just the memory limit, and everything else stays profile-correct.

**forge-agent** (primary workload)
- The agent core + tool builder + config engine
- Resources, replicas, and HPA thresholds driven by cluster profile (see table above)
- Startup probe: 30s grace, Liveness: /health/live, Readiness: /health/ready

**forge-gateway** (optional split)
- `small` and `medium`: runs in-process with forge-agent, no extra pods
- `large`: splits into its own Deployment, allowing independent scaling of inbound handling vs. agent processing

**litellm-proxy** (embedded or sidecar depending on profile)
- `small`: embedded as a library — zero extra pods, lower overhead
- `medium`/`large`: dedicated sidecar or shared service with its own resource envelope and HPA on token throughput

**agentweave-sidecar** (per-pod sidecar, all profiles)
- Runs as a sidecar container in every forge-agent pod
- Intercepts all inbound/outbound via local Unix socket
- Zero network hop for security checks
- Resource footprint is small and consistent across profiles (50m CPU / 64Mi RAM request)

**redis** (shared cache + message queue)
- Tool call result caching (configurable TTL per tool)
- Rate limit counters
- Config version coordination across pod replicas
- Session state for conversational interface
- `small`: single pod, no persistence, acceptable for dev/single-client
- `medium`/`large`: PVC-backed or redis-ha chart depending on durability requirements

### Secrets Architecture
```
K8s Secrets
    → Mounted as files (not env vars where possible)
    → AgentWeave sidecar reads at injection time
    → Never logged, never in tool descriptions
    → Rotation: K8s secret update → config hot-reload picks up new values
```

### Helm Chart Structure
```
forge/
  Chart.yaml
  values.yaml          # All config exposed as Helm values
  values.prod.yaml     # Production overrides
  templates/
    deployment.yaml    # forge-agent + agentweave sidecar
    service.yaml
    hpa.yaml
    pdb.yaml           # PodDisruptionBudget for rolling deploys
    configmap.yaml     # Forge config (non-secret portions)
    secret.yaml        # Sealed Secrets / External Secrets reference
    servicemonitor.yaml # Prometheus scrape config
    ingress.yaml
```

### Scaling Strategy
- **Horizontal:** HPA on request queue depth (Redis list length) — more predictive than CPU for LLM workloads
- **Vertical:** Tool execution is I/O-bound; node size matters for concurrent tool call fan-out
- **Topology:** PodAntiAffinity to spread across nodes; no single node failure takes down all capacity
- **Stateless design:** Every forge-agent pod is fully stateless (state in Redis). Any pod can handle any request.

---

## Required Installs

### Host System Prerequisites

**Runtime:**
- Python 3.12+ (pyenv recommended for version management)
- Docker 25+ with BuildKit enabled
- kubectl 1.30+
- Helm 3.14+
- k9s (optional but strongly recommended for operations)

**Development toolchain:**
- uv (Python package manager — replaces pip + venv for speed)
- pre-commit
- ruff (linting + formatting)
- mypy (type checking)
- pytest + pytest-asyncio + pytest-httpx

**Local Kubernetes (dev):**
- k3d or kind (lightweight local clusters)
- Skaffold (dev-loop: build → deploy → tail logs in one command)

**Secret management:**
- kubeseal (Sealed Secrets) or external-secrets-operator
- Either works; Sealed Secrets is simpler for small teams, ESO for multi-cloud

**Observability stack (can use existing if present):**
- Prometheus + Grafana (kube-prometheus-stack Helm chart)
- Loki for log aggregation
- Tempo or Jaeger for distributed traces

**Package inventory (Python):**
```
fastmcp>=3.0.0          # MCP server + OpenAPI provider
litellm>=1.40.0         # Multi-provider LLM routing
pydantic-ai>=0.0.50     # Agent framework
pydantic>=2.7           # Data validation
httpx>=0.27             # Async HTTP for tool calls
watchdog>=4.0           # Config file hot-reload
redis>=5.0              # Cache + rate limiting
structlog>=24.0         # Structured logging
opentelemetry-sdk       # Distributed tracing
prometheus-client       # Metrics exposure
pyyaml>=6.0             # Config parsing
jsonschema>=4.22        # Config validation
cryptography>=42.0      # AgentWeave signing operations
python-jose>=3.3        # JWT handling
tenacity>=8.3           # Retry with backoff
anyio>=4.4              # Async primitives
uvicorn>=0.30           # ASGI server
fastapi>=0.111          # Gateway REST API
```

**Container base:** `python:3.12-slim` with multi-stage build. Final image target: under 200MB.

---

## Config File Schema (Canonical Design)

```yaml
# forge.yaml — Human-editable configuration

forge:
  name: "my-forge"
  version: "1.0.0"
  description: "What this Forge instance does"

llm:
  primary: "anthropic/claude-sonnet-4-20250514"
  fallback:
    - "openai/gpt-4o"
    - "ollama/qwen3-coder"  # local fallback
  litellm:
    mode: sidecar  # embedded | sidecar | external
    endpoint: "http://localhost:4000"

security:
  agentweave:
    enabled: true
    identity_secret: "forge-identity-keypair"  # K8s secret name
    trust_policy: strict  # strict | permissive
    audit_log: true
  api_keys:
    - name: "default"
      secret_ref: "forge-api-keys"  # K8s secret

tools:
  # OpenAPI-sourced tool sets
  openapi_sources:
    - name: github
      spec: "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.yaml"
      auth:
        type: bearer
        secret_ref: GITHUB_TOKEN
      namespace: "github"
      include_tags: [repos, issues, pulls]  # filter to relevant ops

    - name: stripe
      spec: "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.yaml"
      auth:
        type: bearer
        secret_ref: STRIPE_SECRET_KEY
      namespace: "stripe"
      include_operations: [list_charges, create_payment_intent, get_balance]

  # Manual REST tool definitions
  manual_tools:
    - name: get_weather
      description: "Get current weather conditions for a city"
      api:
        base_url: "https://api.openweathermap.org/data/2.5"
        endpoint: "/weather"
        method: GET
        auth:
          type: query_param
          param: "appid"
          secret_ref: OPENWEATHER_API_KEY
        params:
          - name: city
            type: string
            required: true
            description: "City name, e.g. 'Kansas City'"
        response_map:
          temperature: "$.main.temp"
          description: "$.weather[0].description"

  # Composed workflows
  workflows:
    - name: deployment_health_check
      description: "Check GitHub deployment status and correlate with system metrics"
      steps:
        - tool: github.list_deployments
          params: { owner: "{owner}", repo: "{repo}" }
          bind_as: deployments
        - tool: get_weather  # illustrative
          params: { city: "Kansas City" }
      output: "Synthesize deployment status and context"

agents:
  # Peer agents this Forge can call
  peers:
    - name: data-forge
      endpoint: "https://data-forge.hvs.internal"
      trust_level: high  # AgentWeave policy
      capabilities: [data_query, reporting]
```

---

## Open Design Decisions to Resolve Next Session

**1. AgentWeave interface contract** — Before implementation, we need to nail down the exact API AgentWeave exposes to Forge. Specifically: how does the identity registration work, what does the message signing envelope look like, and what does the trust policy DSL look like. This is the one dependency that needs to be defined before Forge's security layer can be coded.

**2. Config file location strategy in K8s** — Two options: ConfigMap mount (simple, but 1MB limit and requires pod restart without hot-reload), or init container that pulls from a Git repo or S3 (more complex, enables GitOps workflow for config). Given HVS's K8s expertise, the Git-backed option is worth designing. Config changes become PRs.

**3. Tool surface size management** — OpenAPI specs from Stripe and GitHub can generate hundreds of tools. Too many tools degrades LLM tool selection accuracy. Need a strategy: lazy loading (only register tools the agent has used before), semantic clustering (group related tools), or explicit include/exclude lists in config. The include_tags/include_operations fields in the schema sketch above are a start, but the full strategy needs a decision.

**4. Persistent session state** — The conversational interface needs memory. Options: Redis with TTL (simple, stateless pods), PostgreSQL with pgvector (persistent, searchable), or pure in-context (no external dependency, limited to context window). The choice affects the infra footprint.

**5. Forge registry** — If you're running multiple Forge instances for different purposes (data-forge, ops-forge, comms-forge), a central registry for discovery and capability advertisement would be useful. This is where A2A Agent Cards live. Worth designing as a lightweight service or just a K8s ConfigMap per instance.

---

## What Makes This Worth Building

The thing that separates Forge from other agent platforms is the **config-first philosophy at every layer.** An engineer adds a new API integration by writing 15 lines of YAML. No Python, no deployment, no PR for tool code. The hot-reload picks it up. That same config defines the security policy, the LLM routing, the peer agents, and the API surface exposed to applications.

The dual interface is the second differentiator. Application developers get a clean REST API with structured responses — they don't think about agents or MCP. Human users get a conversational interface with full tool access. The same Forge instance serves both, and the same security layer covers both.

The third is the Kubernetes-native design from the start. Forge isn't a local tool bolted onto K8s. It's designed to run as a properly scalable, observable, zero-downtime service. The HPA strategy, the sidecar security model, the stateless pod design — these aren't afterthoughts.

---

## Next Steps for Implementation Session

1. Define AgentWeave's interface contract (needs you to spec what agentweave exposes)
2. Build the config schema validator as a standalone Python library first — gets us fast iteration on the config design
3. Build the tool builder in isolation — prove it can generate a valid FastMCP tool from a manual config definition
4. Wire in LiteLLM as embedded first, then add sidecar option
5. Gateway last — interfaces are easy once the core is solid
6. Helm chart in parallel with gateway work
