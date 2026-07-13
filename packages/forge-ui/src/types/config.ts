// These types mirror the backend contract exactly:
//   - packages/forge-config/src/forge_config/schema.py (ForgeConfig and friends)
//   - packages/forge-gateway/src/forge_gateway/models.py (Admin*/Health/Conversation models)
// Keep field names and shapes in lockstep with those Pydantic models -- the
// backend is the source of truth. When the backend adds a field, mirror it
// here; do not invent fields the backend does not return or accept.

export type LiteLLMMode = "embedded" | "sidecar" | "external";
export type SecretSource = "env" | "k8s_secret";
export type ParamType = "string" | "integer" | "number" | "boolean" | "array" | "object";
export type AuthType = "bearer" | "api_key" | "basic" | "none";
export type HTTPMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type TrustLevel = "high" | "medium" | "low";
export type TrustPolicy = "strict" | "permissive";
export type PeerStatus = "reachable" | "unreachable" | "unknown";

// --- Secret references (forge_config.schema.SecretRef) ---

export interface SecretRef {
  source: SecretSource;
  name: string;
  key?: string | null; // required when source === "k8s_secret"
}

// --- Metadata (forge_config.schema.ForgeMetadata) ---

export interface ForgeMetadata {
  name: string;
  version: string;
  description?: string;
  environment?: string;
}

// --- LLM configuration (forge_config.schema.LLMConfig / LiteLLMConfig) ---

export interface LiteLLMConfig {
  mode: LiteLLMMode;
  endpoint?: string | null; // required for sidecar/external
  model_list?: Record<string, unknown>[];
  fallback_models?: string[];
  timeout?: number;
  max_retries?: number;
}

export interface LLMConfig {
  default_model: string;
  litellm?: LiteLLMConfig;
  system_prompt?: string | null;
  temperature?: number;
  max_tokens?: number;
}

// --- Tools configuration (forge_config.schema.ToolsConfig and friends) ---

export interface ParameterDef {
  name: string;
  type: ParamType;
  description?: string;
  required?: boolean;
  default?: unknown;
}

export interface AuthConfig {
  type: AuthType;
  token?: SecretRef | null;
  header_name?: string;
  username?: SecretRef | null;
  password?: SecretRef | null;
}

export interface ResponseMapping {
  result_path?: string;
  error_path?: string | null;
  status_field?: string | null;
  field_map?: Record<string, string>;
}

export interface ManualToolAPI {
  url?: string | null;
  base_url?: string | null;
  endpoint?: string | null;
  method: HTTPMethod;
  headers?: Record<string, string>;
  body_template?: Record<string, unknown> | null;
  auth?: AuthConfig;
  response_mapping?: ResponseMapping;
  timeout?: number;
}

export interface ManualTool {
  name: string;
  description: string;
  parameters?: ParameterDef[];
  api: ManualToolAPI;
}

export interface OpenAPISource {
  name: string;
  url?: string | null;
  path?: string | null;
  spec?: string | null;
  route_map?: Record<string, string>;
  auth?: AuthConfig;
  prefix?: string | null;
  namespace?: string | null;
  include_tags?: string[];
  include_operations?: string[];
}

export interface WorkflowStep {
  tool: string;
  params?: Record<string, unknown>;
  output_as?: string | null;
  condition?: string | null;
}

export interface Workflow {
  name: string;
  description: string;
  parameters?: ParameterDef[];
  steps: WorkflowStep[];
}

export interface ToolsConfig {
  openapi_sources?: OpenAPISource[];
  manual_tools?: ManualTool[];
  workflows?: Workflow[];
}

// --- Security configuration (forge_config.schema.SecurityConfig and friends) ---

export interface AgentWeaveConfig {
  enabled: boolean;
  trust_domain?: string;
  spiffe_endpoint?: string;
  authz_provider?: string;
  opa_endpoint?: string;
  identity_secret?: string | null;
  trust_policy?: TrustPolicy;
}

export interface APIKeyConfig {
  enabled: boolean;
  keys: SecretRef[];
}

export interface SecurityConfig {
  agentweave?: AgentWeaveConfig;
  api_keys?: APIKeyConfig;
  jwt_secret?: SecretRef | null;
  rate_limit_rpm?: number;
  allowed_origins?: string[];
}

// --- Agents configuration (forge_config.schema.AgentsConfig and friends) ---

export interface PeerAgent {
  name: string;
  endpoint: string;
  trust_level: TrustLevel;
  capabilities?: string[];
  spiffe_id?: string | null;
  status?: PeerStatus;
}

// --- POST /v1/admin/peers (forge_gateway.models.AdminPeerCreateRequest) ---

export interface CreatePeerRequest {
  name: string;
  endpoint: string;
  trust_level?: TrustLevel;
  capabilities?: string[];
  spiffe_id?: string | null;
}

export interface AgentDef {
  name: string;
  description?: string;
  system_prompt?: string | null;
  model?: string | null;
  tools?: string[];
  max_turns?: number;
}

export interface AgentsConfig {
  default?: string;
  agents?: AgentDef[];
  peers?: PeerAgent[];
}

// --- Root config (forge_config.schema.ForgeConfig) ---

export interface ForgeConfig {
  metadata: ForgeMetadata;
  llm: LLMConfig;
  tools: ToolsConfig;
  security?: SecurityConfig;
  agents?: AgentsConfig;
}

// --- Gateway response models (forge_gateway.models) ---

export interface HealthResponse {
  status: string;
  version?: string;
  components?: Record<string, string>;
}

export interface ToolInfo {
  name: string;
  description: string;
  source?: string;
}

export interface Session {
  session_id: string;
  message_count?: number;
  agent?: string | null;
}

export interface PingPeerResponse {
  name: string;
  status: "reachable" | "unreachable";
  http_status?: number;
  error?: string;
  latency_ms?: number | null;
}
