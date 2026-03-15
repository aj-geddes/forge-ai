---
layout: page
title: "Configuration Reference"
description: "Complete forge.yaml configuration reference with every option, type, default, and description."
tier: user
nav_order: 9
---

# Configuration Reference

This page documents every option available in `forge.yaml`. The configuration is organized into five top-level sections: `metadata`, `llm`, `tools`, `security`, and `agents`.

Secret values (API keys, passwords) are never stored in plaintext in the config file. Instead, they use **secret references** that point to environment variables or Kubernetes secrets. See [Secret References](#secret-references) at the end of this page.

---

## metadata

Basic information about the Forge AI deployment.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | `"forge"` | The name of this agent instance. Used in the dashboard, logs, and A2A agent card. |
| `version` | string | `"0.1.0"` | Version string for your deployment. |
| `description` | string | `""` | Human-readable description shown on the dashboard and in the A2A agent card. |
| `environment` | string | `"development"` | Deployment environment identifier (e.g., `development`, `staging`, `production`). |

**Example:**

```yaml
metadata:
  name: my-forge-agent
  version: "0.1.0"
  description: "Example Forge AI deployment"
  environment: development
```

---

## llm

Controls how the agent connects to language model providers.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `default_model` | string | `"gpt-4o"` | The model identifier used for conversations. Must match a model name in the LiteLLM model list (if configured). |
| `temperature` | float | `0.7` | Controls response randomness. Range: 0.0 (deterministic) to 2.0 (highly creative). |
| `max_tokens` | integer | `4096` | Maximum number of tokens in the model's response. |
| `system_prompt` | string | `null` | Default system prompt applied to all agents unless overridden per-agent. |

### llm.litellm

LiteLLM router configuration for multi-provider LLM routing.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mode` | string | `"embedded"` | How LiteLLM is deployed. One of: `embedded` (in-process), `sidecar` (separate container), `external` (remote service). |
| `endpoint` | string | `null` | URL of the LiteLLM proxy. **Required** when mode is `sidecar` or `external`. Ignored for `embedded`. |
| `model_list` | list | `[]` | List of model definitions. Each entry has `model_name` (friendly name) and `litellm_params` (provider-specific config including `model` and `api_key`). |
| `fallback_models` | list | `[]` | Ordered list of model names to try if the primary model fails. Models must be defined in `model_list`. |
| `timeout` | float | `30.0` | Request timeout in seconds for LLM API calls. |
| `max_retries` | integer | `3` | Number of retry attempts for failed LLM requests. |

**Example:**

```yaml
llm:
  default_model: gpt-4o
  temperature: 0.7
  max_tokens: 4096
  system_prompt: "You are a helpful AI assistant with access to tools."
  litellm:
    mode: embedded
    model_list:
      - model_name: gpt-4o
        litellm_params:
          model: openai/gpt-4o
          api_key: ${OPENAI_API_KEY}
      - model_name: claude-sonnet
        litellm_params:
          model: anthropic/claude-sonnet-4-20250514
          api_key: ${ANTHROPIC_API_KEY}
    fallback_models:
      - claude-sonnet
    timeout: 30.0
    max_retries: 3
```

---

## tools

Defines the tools available to the agent. There are three tool types: OpenAPI sources, manual tools, and workflows.

### tools.openapi_sources

Each entry imports tools from an OpenAPI specification.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | *required* | Identifier for this OpenAPI source. |
| `url` | string | `null` | URL of the OpenAPI spec (JSON or YAML). Provide either `url`, `path`, or `spec`. |
| `path` | string | `null` | Local file path to the OpenAPI spec. |
| `spec` | string | `null` | Inline spec content, or a URL/path that is auto-detected. |
| `namespace` | string | `null` | Prefix added to all tool names from this source (e.g., `petstore_findPets`). |
| `include_tags` | list | `[]` | Only import operations tagged with these values. Empty means import all. |
| `include_operations` | list | `[]` | Only import operations with these operation IDs. Empty means import all. |
| `route_map` | object | `{}` | Rename operations: keys are original operation IDs, values are the desired tool names. |
| `auth.type` | string | `"none"` | Authentication type for API calls: `none`, `bearer`, `api_key`, or `basic`. |
| `auth.token` | secret ref | `null` | Secret reference for bearer or API key auth. |
| `auth.header_name` | string | `"Authorization"` | HTTP header name for the authentication credential. |

**Example:**

```yaml
tools:
  openapi_sources:
    - name: petstore
      url: https://petstore3.swagger.io/api/v3/openapi.json
      namespace: petstore
      include_tags:
        - pet
      include_operations:
        - findPetsByStatus
        - getPetById
      route_map:
        findPetsByStatus: find_pets
        getPetById: get_pet
      auth:
        type: none
```

### tools.manual_tools

Each entry defines a custom tool with explicit parameters and API configuration.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | *required* | The tool name as the agent sees it. |
| `description` | string | *required* | What the tool does (shown to the LLM). |
| `parameters` | list | `[]` | Input parameters (see below). |
| `api.base_url` | string | `null` | Base URL for the API call. Use with `endpoint`. |
| `api.endpoint` | string | `null` | Path appended to `base_url`. |
| `api.url` | string | `null` | Full URL (alternative to base_url + endpoint). |
| `api.method` | string | `"GET"` | HTTP method: `GET`, `POST`, `PUT`, `PATCH`, or `DELETE`. |
| `api.headers` | object | `{}` | Additional HTTP headers to include. |
| `api.body_template` | object | `null` | JSON body template for POST/PUT requests. |
| `api.auth` | object | `{}` | Authentication configuration (same structure as OpenAPI auth). |
| `api.response_mapping.result_path` | string | `"$"` | JSONPath expression to extract the result from the response. |
| `api.response_mapping.field_map` | object | `{}` | Rename fields in the extracted result. Keys are output names, values are source paths. |
| `api.timeout` | float | `30.0` | Request timeout in seconds. |

#### Parameter definitions

Each parameter in the `parameters` list has:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | *required* | Parameter name. |
| `type` | string | `"string"` | Data type: `string`, `integer`, `number`, `boolean`, `array`, or `object`. |
| `description` | string | `""` | Description shown to the LLM to explain the parameter's purpose. |
| `required` | boolean | `true` | Whether the parameter is required. |
| `default` | any | `null` | Default value when the parameter is not provided. |

**Example:**

```yaml
tools:
  manual_tools:
    - name: get_weather
      description: "Get current weather for a location"
      parameters:
        - name: location
          type: string
          description: "City name or coordinates"
          required: true
        - name: units
          type: string
          description: "Temperature units"
          required: false
          default: metric
      api:
        base_url: "https://api.weatherapi.com"
        endpoint: "/v1/current.json"
        method: GET
        auth:
          type: api_key
          token:
            source: env
            name: WEATHER_API_KEY
          header_name: "X-Api-Key"
        response_mapping:
          result_path: "$.current"
          field_map:
            temperature: "temp_c"
            condition: "condition.text"
            humidity: "humidity"
```

### tools.workflows

Each entry defines a multi-step tool composed of other tools.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | *required* | The workflow tool name. |
| `description` | string | *required* | What the workflow does. |
| `parameters` | list | `[]` | Input parameters (same structure as manual tool parameters). |
| `steps` | list | *required* | Ordered list of steps (at least one). |

#### Workflow steps

Each step in the `steps` list has:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tool` | string | *required* | Name of the tool to call. |
| `params` | object | `{}` | Parameters to pass. Use Jinja2-style templates to reference workflow inputs or previous step outputs (e.g., `{{ "{{ email }}" }}`). |
| `output_as` | string | `null` | Variable name to store this step's result for use in later steps. |
| `condition` | string | `null` | Python-style expression that must evaluate to true for the step to execute (e.g., `"contact.city is not None"`). |

**Example:**

```yaml
tools:
  workflows:
    - name: enrich_contact
      description: "Look up a contact and enrich with weather data"
      parameters:
        - name: email
          type: string
          description: "Contact email"
          required: true
{% raw %}      steps:
        - tool: lookup_contact
          params:
            email: "{{ email }}"
          output_as: contact
        - tool: get_weather
          params:
            location: "{{ contact.city }}"
          output_as: weather{% endraw %}
          condition: "contact.city is not None"
```

---

## security

Security, authentication, and access control settings.

### security.agentweave

AgentWeave integration for agent-to-agent security.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `true` | Whether AgentWeave is active. When `false`, the gateway runs in development mode without identity verification. |
| `trust_domain` | string | `"forge.local"` | The SPIFFE trust domain for this agent's identity. |
| `spiffe_endpoint` | string | `"unix:///run/spire/sockets/agent.sock"` | SPIRE agent socket path for SPIFFE identity. |
| `authz_provider` | string | `"opa"` | Authorization policy engine. Currently supports `opa`. |
| `opa_endpoint` | string | `"http://localhost:8181"` | URL of the OPA (Open Policy Agent) server. |
| `identity_secret` | string | `null` | Secret used for identity operations. Can use `${ENV_VAR}` syntax for environment variable substitution. |
| `trust_policy` | string | `"strict"` | Trust policy: `strict` (deny by default) or `permissive` (allow by default). |

### security.api_keys

API key authentication for the gateway admin API.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `false` | Whether API key authentication is enforced. When `false`, the admin API returns 403 for all requests. |
| `keys` | list | `[]` | List of secret references pointing to API key values. Each entry has `source` (`env` or `k8s_secret`) and `name`. |

### Other security options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `jwt_secret` | secret ref | `null` | Secret reference for JWT verification. When set, the SecurityGate verifies JWT tokens. When `null`, JWT verification is disabled. |
| `rate_limit_rpm` | integer | `60` | Maximum requests per minute. Applies to all API endpoints. |
| `allowed_origins` | list | `["*"]` | CORS allowed origins. Use `["*"]` for development or specific domains for production. |

**Example:**

```yaml
security:
  agentweave:
    enabled: true
    trust_domain: forge.local
    spiffe_endpoint: "unix:///run/spire/sockets/agent.sock"
    authz_provider: opa
    opa_endpoint: "http://localhost:8181"
    identity_secret: "${AGENTWEAVE_IDENTITY_SECRET}"
    trust_policy: strict
  api_keys:
    enabled: true
    keys:
      - source: env
        name: FORGE_API_KEY
  rate_limit_rpm: 60
  allowed_origins:
    - "https://forge.example.com"
```

---

## agents

Agent persona definitions and peer agent connections.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `default` | string | `"assistant"` | Name of the default agent persona used when no specific agent is requested. Must match one of the entries in `agents`. |

### agents.agents

A list of named agent personas. Each entry defines a distinct agent personality with its own configuration.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | *required* | Unique identifier for this agent. |
| `description` | string | `""` | Human-readable description of the agent's purpose. |
| `system_prompt` | string | `null` | System prompt for this agent. Overrides the global `llm.system_prompt`. |
| `model` | string | `null` | Model override for this agent. When `null`, uses `llm.default_model`. |
| `tools` | list | `[]` | Tool name filter. When non-empty, the agent can only use the listed tools. When empty, the agent has access to all registered tools. |
| `max_turns` | integer | `10` | Maximum conversation turns before the session is capped. |

### agents.peers

A list of peer agents for A2A communication.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | string | *required* | Unique identifier for the peer. |
| `endpoint` | string | *required* | Full URL of the peer's gateway (e.g., `https://data-forge.hvs.internal`). |
| `trust_level` | string | `"low"` | Trust classification: `high`, `medium`, or `low`. |
| `capabilities` | list | `[]` | Tags describing what the peer can do (e.g., `data_query`, `reporting`). |

**Example:**

```yaml
agents:
  default: assistant
  agents:
    - name: assistant
      description: "General-purpose assistant"
      system_prompt: "You are a helpful assistant."
      max_turns: 10
    - name: analyst
      description: "Data analysis specialist"
      system_prompt: "You are a data analyst. Focus on structured output."
      model: gpt-4o
      tools:
        - get_weather
        - enrich_contact
      max_turns: 5
  peers:
    - name: data-forge
      endpoint: "https://data-forge.hvs.internal"
      trust_level: high
      capabilities: [data_query, reporting]
    - name: security-forge
      endpoint: "https://security-forge.hvs.internal"
      trust_level: medium
      capabilities: [threat_analysis, audit]
```

---

## Secret References

Secret values are never written directly in `forge.yaml`. Instead, you use **secret references** that tell Forge AI where to find the actual value at runtime.

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Where to read the secret: `env` (environment variable) or `k8s_secret` (Kubernetes secret). |
| `name` | string | The environment variable name or Kubernetes secret name. |
| `key` | string | The key within a Kubernetes secret. **Required** when `source` is `k8s_secret`. |

**Environment variable example:**

```yaml
token:
  source: env
  name: MY_API_KEY
```

**Kubernetes secret example:**

```yaml
token:
  source: k8s_secret
  name: forge-secrets
  key: api-key
```

Environment variable substitution with `${VAR_NAME}` syntax is also supported in string values (e.g., `identity_secret: "${AGENTWEAVE_IDENTITY_SECRET}"`). This is resolved at config load time by the secret resolver.
