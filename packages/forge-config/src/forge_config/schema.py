"""Pydantic models for Forge YAML configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# --- Enums ---


class LiteLLMMode(str, Enum):
    """How LiteLLM is deployed relative to the Forge agent."""

    EMBEDDED = "embedded"
    SIDECAR = "sidecar"
    EXTERNAL = "external"


class SecretSource(str, Enum):
    """Where a secret value is resolved from."""

    ENV = "env"
    K8S_SECRET = "k8s_secret"


class ParamType(str, Enum):
    """Supported parameter types for manual tool definitions."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class AuthType(str, Enum):
    """Authentication methods for API tool calls."""

    BEARER = "bearer"
    API_KEY = "api_key"
    BASIC = "basic"
    NONE = "none"


class HTTPMethod(str, Enum):
    """Supported HTTP methods for manual tools."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


# --- Secret References ---


class SecretRef(BaseModel):
    """Polymorphic secret reference: either env var or Kubernetes secret."""

    source: SecretSource
    name: str
    key: str | None = None  # Required for k8s_secret

    @model_validator(mode="after")
    def validate_k8s_key(self) -> SecretRef:
        if self.source == SecretSource.K8S_SECRET and not self.key:
            msg = "key is required when source is k8s_secret"
            raise ValueError(msg)
        return self


# --- LLM Configuration ---


class LiteLLMConfig(BaseModel):
    """LiteLLM Router configuration."""

    mode: LiteLLMMode = LiteLLMMode.EMBEDDED
    endpoint: str | None = None  # Required for sidecar/external
    model_list: list[dict[str, Any]] = Field(default_factory=list)
    fallback_models: list[str] = Field(default_factory=list)
    timeout: float = 30.0
    max_retries: int = 3

    @model_validator(mode="after")
    def validate_endpoint(self) -> LiteLLMConfig:
        if self.mode in (LiteLLMMode.SIDECAR, LiteLLMMode.EXTERNAL) and not self.endpoint:
            msg = f"endpoint is required when mode is {self.mode.value}"
            raise ValueError(msg)
        return self


class LLMConfig(BaseModel):
    """Top-level LLM configuration."""

    default_model: str = "gpt-4o"
    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)
    system_prompt: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096


# --- Tools Configuration ---


class ParameterDef(BaseModel):
    """Parameter definition for manual tools."""

    name: str
    type: ParamType = ParamType.STRING
    description: str = ""
    required: bool = True
    default: Any = None


class AuthConfig(BaseModel):
    """Authentication configuration for API calls."""

    type: AuthType = AuthType.NONE
    token: SecretRef | None = None
    header_name: str = "Authorization"
    username: SecretRef | None = None
    password: SecretRef | None = None

    @model_validator(mode="after")
    def validate_auth_fields(self) -> AuthConfig:
        if self.type == AuthType.BEARER and not self.token:
            msg = "token is required for bearer auth"
            raise ValueError(msg)
        if self.type == AuthType.API_KEY and not self.token:
            msg = "token is required for api_key auth"
            raise ValueError(msg)
        if self.type == AuthType.BASIC and (not self.username or not self.password):
            msg = "username and password are required for basic auth"
            raise ValueError(msg)
        return self


class ResponseMapping(BaseModel):
    """Maps API response fields to tool output."""

    result_path: str = "$"  # JSONPath expression
    error_path: str | None = None
    status_field: str | None = None


class ManualToolAPI(BaseModel):
    """API call configuration for a manual tool."""

    url: str
    method: HTTPMethod = HTTPMethod.GET
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    response_mapping: ResponseMapping = Field(default_factory=ResponseMapping)
    timeout: float = 30.0


class ManualTool(BaseModel):
    """A manually defined tool (not from OpenAPI spec)."""

    name: str
    description: str
    parameters: list[ParameterDef] = Field(default_factory=list)
    api: ManualToolAPI


class OpenAPISource(BaseModel):
    """An OpenAPI spec source for auto-generating tools."""

    url: str | None = None
    path: str | None = None
    route_map: dict[str, str] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    prefix: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> OpenAPISource:
        if not self.url and not self.path:
            msg = "Either url or path must be provided for OpenAPI source"
            raise ValueError(msg)
        return self


class WorkflowStep(BaseModel):
    """A single step in a workflow tool."""

    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    output_as: str | None = None
    condition: str | None = None


class Workflow(BaseModel):
    """A composite tool built from multiple steps."""

    name: str
    description: str
    parameters: list[ParameterDef] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(min_length=1)


class ToolsConfig(BaseModel):
    """Top-level tools configuration."""

    openapi: list[OpenAPISource] = Field(default_factory=list)
    manual: list[ManualTool] = Field(default_factory=list)
    workflows: list[Workflow] = Field(default_factory=list)


# --- Security Configuration ---


class AgentWeaveConfig(BaseModel):
    """AgentWeave integration settings."""

    enabled: bool = True
    trust_domain: str = "forge.local"
    spiffe_endpoint: str = "unix:///run/spire/sockets/agent.sock"
    authz_provider: str = "opa"
    opa_endpoint: str = "http://localhost:8181"


class APIKeyConfig(BaseModel):
    """API key authentication for the gateway."""

    enabled: bool = False
    keys: list[SecretRef] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    """Top-level security configuration."""

    agentweave: AgentWeaveConfig = Field(default_factory=AgentWeaveConfig)
    api_keys: APIKeyConfig = Field(default_factory=APIKeyConfig)
    rate_limit_rpm: int = 60
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])


# --- Agents Configuration ---


class AgentDef(BaseModel):
    """Definition of a named agent persona/profile."""

    name: str
    description: str = ""
    system_prompt: str | None = None
    model: str | None = None  # Override default
    tools: list[str] = Field(default_factory=list)  # Tool name filter
    max_turns: int = 10


class AgentsConfig(BaseModel):
    """Configuration for agent personas."""

    default: str = "assistant"
    agents: list[AgentDef] = Field(default_factory=list)


# --- Metadata ---


class ForgeMetadata(BaseModel):
    """Metadata about the Forge deployment."""

    name: str = "forge"
    version: str = "0.1.0"
    description: str = ""
    environment: str = "development"


# --- Root Config ---


class ForgeConfig(BaseModel):
    """Root configuration model for forge.yaml."""

    metadata: ForgeMetadata = Field(default_factory=ForgeMetadata)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
