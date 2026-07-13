---
layout: page
title: Data Model
description: Pydantic configuration schema, model relationships, YAML format, and secret resolution for Forge AI.
parent: Technical
nav_order: 3
---

# Data Model

All Forge AI configuration is defined through a hierarchy of Pydantic v2 models rooted at `ForgeConfig`. The schema is defined in `packages/forge-config/src/forge_config/schema.py`.

## ForgeConfig Schema

### Root Model

```python
class ForgeConfig(BaseModel):
    metadata: ForgeMetadata
    llm: LLMConfig
    tools: ToolsConfig
    security: SecurityConfig
    agents: AgentsConfig
```

All fields have defaults, so an empty `forge.yaml` file is valid and produces a working (minimal) configuration.

### Model Hierarchy

<div style="padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0); overflow-x: auto;">
  <div style="font-weight: 700; color: #1e1b4b; margin-bottom: 1rem; font-size: 0.95rem;">Model Hierarchy</div>

  <!-- Root: ForgeConfig -->
  <div style="padding: 0.75rem 1rem; background: #1e1b4b; color: white; border-radius: 6px; font-weight: 700; font-size: 0.9rem; margin-bottom: 0.75rem;">ForgeConfig</div>

  <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem; margin-left: 1rem; border-left: 3px solid #c7d2fe; padding-left: 1rem;">

    <!-- ForgeMetadata -->
    <div style="padding: 0.75rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px;">
      <div style="font-weight: 600; color: #312e81; font-size: 0.85rem; margin-bottom: 0.25rem;">ForgeMetadata</div>
      <div style="font-size: 0.75rem; color: #64748b;">field: <code>metadata</code> (1:1)</div>
    </div>

    <!-- LLMConfig -->
    <div style="padding: 0.75rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px;">
      <div style="font-weight: 600; color: #312e81; font-size: 0.85rem; margin-bottom: 0.25rem;">LLMConfig</div>
      <div style="font-size: 0.75rem; color: #64748b;">field: <code>llm</code> (1:1)</div>
      <div style="margin-top: 0.375rem; padding-left: 0.5rem; border-left: 2px solid #c7d2fe;">
        <div style="font-size: 0.75rem; color: #4338ca;">LiteLLMConfig <span style="color: #94a3b8;">(1:1)</span></div>
      </div>
    </div>

    <!-- ToolsConfig -->
    <div style="padding: 0.75rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px;">
      <div style="font-weight: 600; color: #312e81; font-size: 0.85rem; margin-bottom: 0.25rem;">ToolsConfig</div>
      <div style="font-size: 0.75rem; color: #64748b;">field: <code>tools</code> (1:1)</div>
      <div style="margin-top: 0.375rem; padding-left: 0.5rem; border-left: 2px solid #c7d2fe; display: flex; flex-direction: column; gap: 0.25rem;">
        <div style="font-size: 0.75rem; color: #4338ca;">OpenAPISource[] <span style="color: #94a3b8;">(0:N)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; AuthConfig (1:1)</span>
        </div>
        <div style="font-size: 0.75rem; color: #4338ca;">ManualTool[] <span style="color: #94a3b8;">(0:N)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; ManualToolAPI (1:1) &rarr; AuthConfig, ResponseMapping</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; ParameterDef[] (0:N)</span>
        </div>
        <div style="font-size: 0.75rem; color: #4338ca;">Workflow[] <span style="color: #94a3b8;">(0:N)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; ParameterDef[] (0:N)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; WorkflowStep[] (1:N)</span>
        </div>
      </div>
    </div>

    <!-- SecurityConfig -->
    <div style="padding: 0.75rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px;">
      <div style="font-weight: 600; color: #312e81; font-size: 0.85rem; margin-bottom: 0.25rem;">SecurityConfig</div>
      <div style="font-size: 0.75rem; color: #64748b;">field: <code>security</code> (1:1)</div>
      <div style="margin-top: 0.375rem; padding-left: 0.5rem; border-left: 2px solid #c7d2fe; display: flex; flex-direction: column; gap: 0.25rem;">
        <div style="font-size: 0.75rem; color: #4338ca;">SecurityAuthConfig <span style="color: #94a3b8;">(1:1, auth)</span></div>
        <div style="font-size: 0.75rem; color: #4338ca;">OIDCConfig <span style="color: #94a3b8;">(1:1, oidc)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; SessionConfig (1:1)</span>
        </div>
        <div style="font-size: 0.75rem; color: #4338ca;">ServiceTokenConfig <span style="color: #94a3b8;">(1:1, service_tokens)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; ServiceToken[] (0:N)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; UserTokenConfig (1:1)</span>
        </div>
        <div style="font-size: 0.75rem; color: #4338ca;">AuthorizationConfig <span style="color: #94a3b8;">(1:1, authorization)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; RoleBinding[] (0:N)</span>
        </div>
        <div style="font-size: 0.75rem; color: #4338ca;">AgentWeaveConfig <span style="color: #94a3b8;">(1:1, agentweave -- workload plane, deprecated-inert for human auth)</span></div>
        <div style="font-size: 0.75rem; color: #4338ca;">APIKeyConfig <span style="color: #94a3b8;">(1:1, api_keys -- deprecated, inert)</span>
          <span style="color: #64748b; display: block; padding-left: 0.5rem;">&#8627; SecretRef[] (0:N)</span>
        </div>
      </div>
    </div>

    <!-- AgentsConfig -->
    <div style="padding: 0.75rem; background: white; border: 1px solid #e2e8f0; border-radius: 6px;">
      <div style="font-weight: 600; color: #312e81; font-size: 0.85rem; margin-bottom: 0.25rem;">AgentsConfig</div>
      <div style="font-size: 0.75rem; color: #64748b;">field: <code>agents</code> (1:1)</div>
      <div style="margin-top: 0.375rem; padding-left: 0.5rem; border-left: 2px solid #c7d2fe; display: flex; flex-direction: column; gap: 0.25rem;">
        <div style="font-size: 0.75rem; color: #4338ca;">AgentDef[] <span style="color: #94a3b8;">(0:N)</span></div>
        <div style="font-size: 0.75rem; color: #4338ca;">PeerAgent[] <span style="color: #94a3b8;">(0:N)</span></div>
      </div>
    </div>

  </div>

  <!-- AuthConfig shared reference -->
  <div style="margin-top: 1rem; padding: 0.75rem 1rem; background: #fffbeb; border: 1px solid #f59e0b; border-radius: 6px; font-size: 0.8rem; color: #92400e;">
    <strong>AuthConfig</strong> (shared by OpenAPISource and ManualToolAPI) references optional <strong>SecretRef</strong> fields: <code>token</code> (0:1), <code>username</code> (0:1), <code>password</code> (0:1)
  </div>
</div>

## Model Definitions

### Metadata

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | `"forge"` | Deployment name |
| `version` | `str` | `"0.1.0"` | Config version |
| `description` | `str` | `""` | Human-readable description |
| `environment` | `str` | `"development"` | Environment identifier (development, staging, production) |

### LLM Configuration

**`LLMConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_model` | `str` | `"gpt-4o"` | Default LLM model identifier |
| `temperature` | `float` | `0.7` | Sampling temperature |
| `max_tokens` | `int` | `4096` | Maximum response tokens |
| `system_prompt` | `str \| None` | `None` | Default system prompt for all agents |
| `litellm` | `LiteLLMConfig` | (defaults) | LiteLLM router configuration |

**`LiteLLMConfig`**

| Field | Type | Default | Validation |
|-------|------|---------|------------|
| `mode` | `LiteLLMMode` | `embedded` | One of: `embedded`, `sidecar`, `external` |
| `endpoint` | `str \| None` | `None` | Required when mode is `sidecar` or `external` |
| `model_list` | `list[dict]` | `[]` | LiteLLM model routing table |
| `fallback_models` | `list[str]` | `[]` | Fallback model chain |
| `timeout` | `float` | `30.0` | Request timeout in seconds |
| `max_retries` | `int` | `3` | Maximum retry attempts |

### Tools Configuration

**`ToolsConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `openapi_sources` | `list[OpenAPISource]` | `[]` | OpenAPI specs to auto-generate tools from |
| `manual_tools` | `list[ManualTool]` | `[]` | Manually defined API tools |
| `workflows` | `list[Workflow]` | `[]` | Composite multi-step tools |

Backward-compatible aliases: `openapi` maps to `openapi_sources`, `manual` maps to `manual_tools`.

**`OpenAPISource`**

| Field | Type | Default | Validation |
|-------|------|---------|------------|
| `name` | `str` | (required) | Source identifier |
| `url` | `str \| None` | `None` | Remote spec URL |
| `path` | `str \| None` | `None` | Local file path |
| `spec` | `str \| None` | `None` | Auto-resolves to `url` or `path` |
| `route_map` | `dict[str, str]` | `{}` | Operation ID to tool name mapping |
| `auth` | `AuthConfig` | (defaults) | Auth for API calls |
| `prefix` | `str \| None` | `None` | Tool name prefix |
| `namespace` | `str \| None` | `None` | Synced with `prefix` |
| `include_tags` | `list[str]` | `[]` | Filter by OpenAPI tags |
| `include_operations` | `list[str]` | `[]` | Filter by operation IDs |

At least one of `url`, `path`, or `spec` must be provided.

**`ManualTool`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Tool name |
| `description` | `str` | (required) | Tool description for LLM |
| `parameters` | `list[ParameterDef]` | `[]` | Input parameters |
| `api` | `ManualToolAPI` | (required) | API call configuration |

**`ManualToolAPI`**

| Field | Type | Default | Validation |
|-------|------|---------|------------|
| `url` | `str \| None` | `None` | Full URL |
| `base_url` | `str \| None` | `None` | Base URL (combined with `endpoint`) |
| `endpoint` | `str \| None` | `None` | Path appended to `base_url` |
| `method` | `HTTPMethod` | `GET` | One of: `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `headers` | `dict[str, str]` | `{}` | Additional HTTP headers |
| `body_template` | `dict \| None` | `None` | Request body template |
| `auth` | `AuthConfig` | (defaults) | Authentication config |
| `response_mapping` | `ResponseMapping` | (defaults) | Response field mapping |
| `timeout` | `float` | `30.0` | Request timeout |

Either `url` or both `base_url` and `endpoint` must be provided.

**`ParameterDef`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Parameter name |
| `type` | `ParamType` | `string` | One of: `string`, `integer`, `number`, `boolean`, `array`, `object` |
| `description` | `str` | `""` | Parameter description |
| `required` | `bool` | `True` | Whether the parameter is required |
| `default` | `Any` | `None` | Default value |

**`Workflow` and `WorkflowStep`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Workflow tool name |
| `description` | `str` | (required) | Workflow description |
| `parameters` | `list[ParameterDef]` | `[]` | Input parameters |
| `steps` | `list[WorkflowStep]` | (required, min 1) | Ordered execution steps |

| WorkflowStep Field | Type | Default | Description |
|---------------------|------|---------|-------------|
| `tool` | `str` | (required) | Name of tool to invoke |
| `params` | `dict[str, Any]` | `{}` | Parameters (supports `{{ var }}` templates) |
| `output_as` | `str \| None` | `None` | Variable name for step output |
| `condition` | `str \| None` | `None` | Condition expression for conditional execution |

### Authentication Configuration

**`AuthConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `AuthType` | `none` | One of: `bearer`, `api_key`, `basic`, `none` |
| `token` | `SecretRef \| None` | `None` | Required for `bearer` and `api_key` |
| `header_name` | `str` | `"Authorization"` | Header name for API key auth |
| `username` | `SecretRef \| None` | `None` | Required for `basic` auth |
| `password` | `SecretRef \| None` | `None` | Required for `basic` auth |

### Security Configuration

Rooted at `SecurityConfig` (ADR-0001: Dex OIDC for the human plane; ADR-0004: AgentWeave for the separate workload plane). `security.jwt_secret` is **not** a field on this model -- a `model_validator(mode="before")` raises a hard `ValueError` if a loaded config still sets it (it was an HS256 shared secret, removed rather than deprecated).

**`SecurityConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auth` | `SecurityAuthConfig` | (defaults) | Declared enforcement mode (`enforce` / `dev_insecure`) |
| `oidc` | `OIDCConfig` | (defaults, points at the live Dex instance) | Dex OIDC settings for browser login |
| `service_tokens` | `ServiceTokenConfig` | (defaults) | Static + self-service machine-client tokens |
| `authorization` | `AuthorizationConfig` | (defaults) | Claims -> roles -> permissions bindings |
| `rate_limit_rpm` | `int` | `60` | Requests per minute per authenticated identity |
| `allowed_origins` | `list[str]` | `["https://forgeai.hvslocal"]` | CORS allowed origins. A `"*"` entry is rejected at validation time when `oidc.enabled` is `True`. |
| `agentweave` | `AgentWeaveConfig` | (defaults) | **Deprecated / inert for human auth** -- workload-plane (SPIFFE + OPA) settings only, kept so old configs still parse |
| `api_keys` | `APIKeyConfig` | (defaults) | **Deprecated / inert** -- kept so old configs still parse; translated into a synthetic service token for one minor release |

**`SecurityAuthConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `AuthMode` | `enforce` | One of: `enforce`, `dev_insecure` (also requires the `FORGE_DEV_INSECURE=1` environment variable to actually engage) |

**`OIDCConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Enable the Dex OIDC login flow |
| `issuer` | `str` | `"https://dex.hvslocal/dex"` | Dex issuer URL (must be `https://` outside `dev_insecure` mode) |
| `client_id` | `str` | `"forge-ai"` | Registered Dex client id (public client, PKCE) |
| `client_secret` | `SecretRef \| None` | `None` | Unused by the public client; present for future confidential-client support |
| `redirect_uri` | `str` | `"https://forgeai.hvslocal/auth/callback"` | OIDC callback URL |
| `scopes` | `list[str]` | `["openid", "email", "profile", "groups"]` | Requested OIDC scopes |
| `allowed_algorithms` | `list[str]` | `["RS256"]` | Accepted JWS signing algorithms |
| `session` | `SessionConfig` | (defaults) | BFF session-cookie codec settings |

**`ServiceTokenConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Enable static service tokens |
| `tokens` | `list[ServiceToken]` | `[]` | Statically configured tokens (`id`, `secret_sha256` digest, `roles`, optional `expires_at`) |
| `user_tokens` | `UserTokenConfig` | (defaults) | Self-service user-minted token settings (ADR-0002) |

**`AuthorizationConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_role` | `str \| None` | `None` | Role granted to a principal matching no binding (deny by default) |
| `metrics_public` | `bool` | `True` | Whether `GET /metrics` is unauthenticated |
| `roles` | `dict[str, list[str]]` | `{viewer, user, admin}` | Role name -> permissions (`agent:invoke`, `tools:invoke`, `agent:peer`, `config:read`, `config:write`, `metrics:read`, or `*`) |
| `bindings` | `list[RoleBinding]` | `[]` | Claim (`groups`/`emails`/`subs`) -> role mappings |

**`AgentWeaveConfig`** (workload plane -- ADR-0004)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Start the `:8443` SPIFFE mTLS + OPA workload listener. Has no effect on human/`:8000` authentication. |
| `trust_domain` | `str` | `"forge.local"` | SPIFFE trust domain |
| `spiffe_endpoint` | `str` | `"unix:///run/spire/sockets/agent.sock"` | SPIRE agent socket |
| `authz_provider` | `str` | `"opa"` | Authorization provider |
| `opa_endpoint` | `str` | `"http://localhost:8181"` | OPA endpoint |
| `identity_secret` | `str \| None` | `None` | Identity secret override |
| `trust_policy` | `TrustPolicy` | `strict` | One of: `strict`, `permissive` |
| `workload_listener_port` | `int` | `8443` | Port for the mTLS listener |

**`APIKeyConfig`** (deprecated, ADR-0001)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Deprecated -- admin routes no longer check this directly |
| `keys` | `list[SecretRef]` | `[]` | Deprecated -- list of API key secret references |

### Agents Configuration

**`AgentsConfig`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default` | `str` | `"assistant"` | Name of the default agent persona |
| `agents` | `list[AgentDef]` | `[]` | Named agent persona definitions |
| `peers` | `list[PeerAgent]` | `[]` | Peer agent connections |

**`AgentDef`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Agent persona name |
| `description` | `str` | `""` | Human-readable description |
| `system_prompt` | `str \| None` | `None` | System prompt override |
| `model` | `str \| None` | `None` | Model override (uses default if None) |
| `tools` | `list[str]` | `[]` | Tool name allow-list (empty = all tools) |
| `max_turns` | `int` | `10` | Maximum LLM request turns |

**`PeerAgent`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Peer identifier |
| `endpoint` | `str` | (required) | Peer base URL |
| `trust_level` | `TrustLevel` | `low` | One of: `high`, `medium`, `low` |
| `capabilities` | `list[str]` | `[]` | Peer's declared capabilities |

### Secret References

**`SecretRef`**

| Field | Type | Default | Validation |
|-------|------|---------|------------|
| `source` | `SecretSource` | (required) | One of: `env`, `k8s_secret` |
| `name` | `str` | (required) | Environment variable name or K8s secret name |
| `key` | `str \| None` | `None` | Required when `source` is `k8s_secret` |

## Configuration File Format

Configuration is defined in `forge.yaml` (path configurable via `FORGE_CONFIG_PATH` environment variable). Here is a minimal example:

```yaml
metadata:
  name: my-forge-agent
  environment: development

llm:
  default_model: gpt-4o
  temperature: 0.7
  max_tokens: 4096
  litellm:
    mode: embedded

tools:
  manual_tools:
    - name: get_weather
      description: "Get current weather for a location"
      parameters:
        - name: location
          type: string
          required: true
      api:
        base_url: "https://api.weatherapi.com"
        endpoint: "/v1/current.json"
        method: GET
        auth:
          type: api_key
          token:
            source: env
            name: WEATHER_API_KEY

security:
  service_tokens:
    enabled: true
    tokens:
      - id: ci-runner
        secret_sha256: "b1946ac92492d2347c6235b4d2611184..."
        roles: [user]
  rate_limit_rpm: 60
  allowed_origins:
    - "https://forge.example.com"

agents:
  default: assistant
  agents:
    - name: assistant
      description: "General-purpose assistant"
      max_turns: 10
```

The canonical reference with all options is in `forge.yaml.example` at the repository root.

## Secret Resolution Flow

Secrets are never stored as plaintext in the configuration file. Instead, they are referenced using `SecretRef` objects that are resolved at runtime:

<div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem; padding: 1.5rem; background: var(--color-bg-secondary, #f8fafc); border-radius: 8px; border: 1px solid var(--color-border, #e2e8f0);">
  <div style="padding: 0.75rem 1.5rem; background: #1e1b4b; color: white; border-radius: 6px; font-weight: 600; font-size: 0.875rem; text-align: center;">forge.yaml<br/><span style="font-weight: 400; opacity: 0.8; font-size: 0.8rem;">SecretRef: {source: env, name: API_KEY}</span></div>
  <div style="color: var(--color-text-muted, #64748b);">↓</div>
  <div style="padding: 0.75rem 1.5rem; background: #312e81; color: white; border-radius: 6px; font-weight: 600; font-size: 0.875rem;">CompositeSecretResolver</div>
  <div style="display: flex; gap: 2rem; margin-top: 0.25rem;">
    <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
      <div style="font-size: 0.75rem; color: #64748b; font-style: italic;">source = env</div>
      <div style="color: var(--color-text-muted, #64748b);">↓</div>
      <div style="padding: 0.625rem 1rem; background: #4338ca; color: white; border-radius: 6px; font-weight: 600; font-size: 0.8rem; text-align: center;">EnvSecretResolver<br/><span style="font-weight: 400; opacity: 0.8; font-size: 0.75rem;">os.environ.get()</span></div>
    </div>
    <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
      <div style="font-size: 0.75rem; color: #64748b; font-style: italic;">source = k8s_secret</div>
      <div style="color: var(--color-text-muted, #64748b);">↓</div>
      <div style="padding: 0.625rem 1rem; background: #4338ca; color: white; border-radius: 6px; font-weight: 600; font-size: 0.8rem; text-align: center;">K8sSecretResolver<br/><span style="font-weight: 400; opacity: 0.8; font-size: 0.75rem;">(when registered)</span></div>
    </div>
  </div>
  <div style="color: var(--color-text-muted, #64748b);">↓</div>
  <div style="padding: 0.625rem 1.5rem; background: #dcfce7; color: #166534; border-radius: 6px; font-weight: 600; font-size: 0.875rem;">Resolved plaintext value</div>
</div>

The `CompositeSecretResolver` delegates to source-specific resolvers:

- **`EnvSecretResolver`** -- Reads from environment variables (`os.environ.get(ref.name)`)
- **`K8sSecretResolver`** -- Reads from Kubernetes secret volumes (registered when running in-cluster)

If resolution fails, a `SecretResolutionError` is raised with a descriptive message. The admin API recursively redacts all `SecretRef` values before returning configuration data to prevent secret leakage.

**Source:** `packages/forge-config/src/forge_config/secret_resolver.py`
