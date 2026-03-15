export type LiteLLMMode = "embedded" | "sidecar" | "external";
export type SecretSource = "env" | "k8s_secret";
export type ParamType = "string" | "integer" | "number" | "boolean" | "array" | "object";
export type AuthType = "bearer" | "api_key" | "basic" | "none";
export type HTTPMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type TrustLevel = "high" | "medium" | "low";
export type TrustPolicy = "strict" | "permissive";

export interface SecretRef {
  source: SecretSource;
  key: string;
  name?: string;
}

export interface ForgeMetadata {
  name: string;
  version: string;
  description?: string;
  environment?: string;
  labels?: Record<string, string>;
}

export interface LiteLLMConfig {
  mode: LiteLLMMode;
  endpoint?: string;
  config_path?: string;
  port?: number;
}

export interface LLMConfig {
  default_model: string;
  api_key?: string | SecretRef;
  api_base?: string;
  temperature?: number;
  max_tokens?: number;
  system_prompt?: string | null;
  litellm?: LiteLLMConfig;
}

export interface ParameterDef {
  name: string;
  type: ParamType;
  description: string;
  required?: boolean;
  default?: unknown;
  enum?: string[];
}

export interface AuthConfig {
  type: AuthType;
  token?: string | SecretRef;
  header_name?: string;
  username?: string;
  password?: string | SecretRef;
}

export interface ResponseMapping {
  result_path?: string;
  error_path?: string;
  status_field?: string;
}

export interface ManualToolAPI {
  url: string;
  method: HTTPMethod;
  headers?: Record<string, string>;
  body_template?: string;
  auth?: AuthConfig;
  response_mapping?: ResponseMapping;
}

export interface ManualTool {
  name: string;
  description: string;
  parameters: ParameterDef[];
  api: ManualToolAPI;
}

export interface OpenAPISource {
  url: string;
  spec_url?: string;
  auth?: AuthConfig;
  include_operations?: string[];
  exclude_operations?: string[];
}

export interface WorkflowStep {
  tool: string;
  description?: string;
  inputs?: Record<string, unknown>;
  output_var?: string;
  condition?: string;
}

export interface Workflow {
  name: string;
  description: string;
  steps: WorkflowStep[];
}

export interface ToolsConfig {
  openapi?: OpenAPISource[];
  manual?: ManualTool[];
  workflows?: Workflow[];
}

export interface AgentWeaveConfig {
  enabled: boolean;
  agent_id?: string;
  key_path?: string;
  trust_store?: string;
}

export interface APIKeyConfig {
  key_hash: string;
  description?: string;
  scopes?: string[];
}

export interface SecurityConfig {
  agentweave?: AgentWeaveConfig;
  api_keys?: APIKeyConfig[];
  cors_origins?: string[];
  rate_limit?: {
    requests_per_minute?: number;
    burst?: number;
  };
  trust_policy?: TrustPolicy;
}

export interface PeerAgent {
  name: string;
  endpoint: string;
  trust_level: TrustLevel;
  description?: string;
  capabilities?: string[];
  status?: string;
  auth?: AuthConfig;
}

export interface AgentDef {
  name: string;
  description?: string;
  system_prompt?: string;
  tools?: string[];
  model?: string;
}

export interface AgentsConfig {
  default_agent?: AgentDef;
  agents?: AgentDef[];
}

export interface ForgeConfig {
  metadata: ForgeMetadata;
  llm: LLMConfig;
  tools: ToolsConfig;
  security?: SecurityConfig;
  peers?: PeerAgent[];
  agents?: AgentsConfig;
}

export interface HealthResponse {
  status: string;
  version?: string;
  uptime?: number;
  checks?: Record<string, { status: string; message?: string }>;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters?: ParameterDef[];
  source?: string;
}

export interface Session {
  session_id: string;
  agent?: string | null;
  message_count?: number;
}
