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
  /** ADR-0005 SS6.2 human-approval gate. Base-only (security control) --
   * runtime-editable UI must never let a caller flip this. */
  requires_approval?: boolean;
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

export type AgentMode = "passive" | "active";

export interface AgentDef {
  name: string;
  description?: string;
  system_prompt?: string | null;
  model?: string | null;
  /** This agent's least-privilege tool scope. Undefined or empty means full
   * (unscoped) access to every configured tool -- enforced server-side. */
  tools?: string[];
  max_turns?: number;
  /** Governance mode; the backend field may not exist on every deployment
   * yet, so callers must tolerate its absence and treat it as "passive". */
  mode?: AgentMode;
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

// --- Layered base+overlay config, monotonic git-promotion ---
//
// These mirror the honesty-envelope contract on the admin config API:
// `persisted` reflects the real filesystem write outcome (never assumed
// from a 2xx status alone), `durable`/`drift_from_git` distinguish "written
// to the overlay PVC" from "reconciled into the git-owned BASE", and
// `rev`/`base_rev` drive optimistic concurrency (If-Match) plus the
// drift-banner/promotion-diff UI.

/**
 * Policy determining how configuration mutations are handled.
 */
export type MutationPolicy = "disabled" | "overlay";

/**
 * Response from GET /v1/admin/config. `rev` and `base_rev` drive optimistic
 * concurrency via If-Match headers. `base_rev` is `null` on a deployment
 * with no overlay written yet (pure BASE, nothing to diff against).
 */
export interface AdminConfigGetResponse {
  config: ForgeConfig;
  path: string;
  rev: number;
  base_rev: string | null;
  drift_from_git: boolean;
  source_layers: string[];
  mutation_policy: MutationPolicy;
}

/**
 * Envelope returned by every config-mutating admin endpoint. `persisted`
 * must be true before the UI shows success. NOTE: `POST /v1/admin/peers`
 * does NOT return this envelope -- it resolves the bare created peer
 * (`PeerAgent`); see the doc comment on `useCreatePeer` in `api/hooks.ts`.
 */
export interface ConfigMutationEnvelope {
  persisted: boolean;
  durable: boolean;
  drift_from_git: boolean;
  rev: number;
  base_rev: string | null;
  promotion_available: boolean;
  message: string;
}

/**
 * A single entry in the configuration audit trail. NOT currently returned
 * by `GET /v1/admin/config/promotion/diff` (see `PromotionDiffResponse`) --
 * the audit trail lives on the separate `GET /v1/admin/config/history`
 * endpoint. Kept here (optional, unused today) for the promotion view to
 * pick up if/when that endpoint is wired in.
 */
export interface AuditTrailEntry {
  rev: number;
  updated_by?: string;
  updated_at?: string;
  summary?: string;
}

/**
 * Response from GET /v1/admin/config/promotion/diff. Contains the unified
 * diff and a copy-paste PR body. `audit_trail` and `changed_count` are not
 * populated by the current backend response model -- both are optional and
 * the UI degrades gracefully (hides/falls back) when absent; see the note
 * on `AuditTrailEntry` above.
 */
export interface PromotionDiffResponse {
  diff: string;
  pr_body: string;
  audit_trail?: AuditTrailEntry[];
  rev: number;
  base_rev: string | null;
  drift_from_git: boolean;
  changed_count?: number;
}

/**
 * Body of a 409 (Conflict) response for optimistic-concurrency failures.
 *
 * FastAPI serializes `HTTPException(status_code=409, detail=...)` as
 * `{"detail": <detail>}`. The admin API's 409s pass a structured
 * `{message, current_rev}` object as `detail`, so the server's current
 * revision normally lives at `body.detail.current_rev`. `current_rev` /
 * `rev` are also accepted at the top level as a defensive fallback for any
 * endpoint that hasn't been migrated to the structured form yet.
 */
export interface ConflictErrorBody {
  detail?: string | { message?: string; current_rev?: number };
  current_rev?: number;
  rev?: number;
}
