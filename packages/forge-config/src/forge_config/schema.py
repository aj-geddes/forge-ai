"""Pydantic models for Forge YAML configuration."""

from __future__ import annotations

import re
import warnings
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# SHA-256 hex digest: exactly 64 lowercase hex characters.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

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


class TrustLevel(str, Enum):
    """Trust level for peer agents."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TrustPolicy(str, Enum):
    """Trust policy for AgentWeave."""

    STRICT = "strict"
    PERMISSIVE = "permissive"


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
    field_map: dict[str, str] = Field(default_factory=dict)


class ManualToolAPI(BaseModel):
    """API call configuration for a manual tool."""

    url: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    method: HTTPMethod = HTTPMethod.GET
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    response_mapping: ResponseMapping = Field(default_factory=ResponseMapping)
    timeout: float = 30.0

    @model_validator(mode="after")
    def validate_url_fields(self) -> ManualToolAPI:
        has_base_endpoint = self.base_url is not None and self.endpoint is not None
        if not has_base_endpoint and not self.url:
            msg = "Either url or both base_url and endpoint must be provided"
            raise ValueError(msg)
        return self

    @property
    def resolved_url(self) -> str:
        """Return the effective URL for this API call.

        If base_url and endpoint are both set, they take precedence over url.
        Otherwise falls back to url.
        """
        if self.base_url is not None and self.endpoint is not None:
            return self.base_url.rstrip("/") + self.endpoint
        # url is guaranteed non-None by validator when base_url/endpoint aren't set
        return self.url  # type: ignore[return-value]


class ManualTool(BaseModel):
    """A manually defined tool (not from OpenAPI spec)."""

    name: str
    description: str
    parameters: list[ParameterDef] = Field(default_factory=list)
    api: ManualToolAPI


class OpenAPISource(BaseModel):
    """An OpenAPI spec source for auto-generating tools."""

    name: str
    url: str | None = None
    path: str | None = None
    spec: str | None = None
    route_map: dict[str, str] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    prefix: str | None = None
    namespace: str | None = None
    include_tags: list[str] = Field(default_factory=list)
    include_operations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> OpenAPISource:
        if not self.url and not self.path and not self.spec:
            msg = "Either url, path, or spec must be provided for OpenAPI source"
            raise ValueError(msg)
        # If spec is provided but url/path aren't, resolve spec to url or path
        if self.spec and not self.url and not self.path:
            if self.spec.startswith(("http://", "https://")):
                self.url = self.spec
            else:
                self.path = self.spec
        # Sync namespace and prefix: namespace takes precedence
        if self.namespace and not self.prefix:
            self.prefix = self.namespace
        elif self.prefix and not self.namespace:
            self.namespace = self.prefix
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

    openapi_sources: list[OpenAPISource] = Field(default_factory=list)
    manual_tools: list[ManualTool] = Field(default_factory=list)
    workflows: list[Workflow] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_field_names(cls, data: Any) -> Any:
        """Accept old field names (openapi, manual) for backward compatibility."""
        if isinstance(data, dict):
            if "openapi" in data and "openapi_sources" not in data:
                data["openapi_sources"] = data.pop("openapi")
            if "manual" in data and "manual_tools" not in data:
                data["manual_tools"] = data.pop("manual")
        return data

    @property
    def openapi(self) -> list[OpenAPISource]:
        """Backward-compatible alias for openapi_sources."""
        return self.openapi_sources

    @property
    def manual(self) -> list[ManualTool]:
        """Backward-compatible alias for manual_tools."""
        return self.manual_tools


# --- Security Configuration (ADR-0001: Dex OIDC authentication) ---


class AuthMode(str, Enum):
    """Declared enforcement mode for the auth subsystem.

    ``enforce`` is the schema default: the absence of a ``security`` block
    in ``forge.yaml`` can never mean the absence of authentication.
    ``dev_insecure`` additionally requires the ``FORGE_DEV_INSECURE=1``
    environment variable at process start (checked by forge-gateway, not
    by this schema) before it is allowed to actually engage.
    """

    ENFORCE = "enforce"
    DEV_INSECURE = "dev_insecure"


class Permission(str, Enum):
    """The closed set of permissions guarding gateway routes."""

    AGENT_INVOKE = "agent:invoke"
    TOOLS_INVOKE = "tools:invoke"
    AGENT_PEER = "agent:peer"
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"
    METRICS_READ = "metrics:read"


# Sentinel permission granting every permission in the closed set (used by
# the built-in "admin" role). Not itself a member of ``Permission``.
PERMISSION_WILDCARD = "*"


class SecurityAuthConfig(BaseModel):
    """Declares (rather than infers) the auth enforcement posture."""

    mode: AuthMode = AuthMode.ENFORCE


class SessionConfig(BaseModel):
    """BFF session-cookie codec settings.

    The cookie is a stateless, authenticated-encrypted blob (Fernet /
    MultiFernet) containing only identity claims -- never an access or
    refresh token.
    """

    cookie_name: str = "forge_session"
    encryption_key: SecretRef | None = Field(
        default_factory=lambda: SecretRef(source=SecretSource.ENV, name="FORGE_SESSION_KEY")
    )
    previous_encryption_key: SecretRef | None = None
    lifetime_seconds: int = 28800  # 8h absolute
    idle_timeout_seconds: int = 3600  # 1h sliding
    same_site: str = "lax"
    secure: bool = True
    csrf_cookie_name: str = "forge_csrf"

    @field_validator("same_site")
    @classmethod
    def validate_same_site(cls, v: str) -> str:
        if v == "none":
            msg = (
                "session.same_site cannot be 'none' -- that would defeat the "
                "primary CSRF control (ADR-0001 SS4.2). Use 'lax' (default) or "
                "'strict'."
            )
            raise ValueError(msg)
        if v not in ("lax", "strict"):
            msg = f"session.same_site must be 'lax' or 'strict', got {v!r}"
            raise ValueError(msg)
        return v


class OIDCConfig(BaseModel):
    """Dex OIDC settings.

    Defaults are pre-filled with the verified, live values for this
    deployment's Dex instance so that an unconfigured ``forge.yaml`` still
    enforces against a real identity provider (see SecurityConfig's
    "enforce requires a mechanism" validator).

    ``client_secret`` is optional: the registered ``forge-ai`` Dex client
    is a PUBLIC client secured by PKCE, not a confidential client -- see
    the developer report for this deviation from the ADR's confidential-
    client example.
    """

    enabled: bool = True
    issuer: str = "https://dex.hvslocal/dex"
    client_id: str = "forge-ai"
    client_secret: SecretRef | None = None
    audience: str | None = None  # defaults to client_id, resolved below
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile", "groups"])
    redirect_uri: str = "https://forgeai.hvslocal/auth/callback"
    post_login_default_path: str = "/"

    discovery: bool = True
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks_uri: str = "https://dex.hvslocal/dex/keys"

    allowed_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    clock_skew_seconds: int = 60
    jwks_cache_ttl_seconds: int = 300
    jwks_min_refresh_seconds: int = 30
    jwks_stale_grace_seconds: int = 86400

    accept_bearer_tokens: bool = True

    session: SessionConfig = Field(default_factory=SessionConfig)

    @model_validator(mode="after")
    def resolve_audience(self) -> OIDCConfig:
        if self.audience is None:
            self.audience = self.client_id
        return self


class ServiceToken(BaseModel):
    """A single Forge-issued service-token principal.

    Only the SHA-256 hex digest of the token is stored -- never the
    token itself. A digest of a 256-bit random value is not brute-
    forceable and carries no exploitable material, so it may live in
    ``forge.yaml`` in git.
    """

    id: str
    description: str = ""
    secret_sha256: str
    roles: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None

    @field_validator("secret_sha256")
    @classmethod
    def validate_sha256_hex(cls, v: str) -> str:
        if not _SHA256_HEX_RE.fullmatch(v):
            msg = (
                "service token secret_sha256 must be exactly 64 lowercase hex "
                f"characters (a SHA-256 digest), got {v!r}"
            )
            raise ValueError(msg)
        return v


USER_TOKEN_ID_PREFIX = "u_"

# ADR-0002 SS7.2: the hard floor on a user-minted token's TTL. Not itself a
# configurable knob -- only default_ttl_seconds and max_ttl_seconds are.
_USER_TOKEN_MIN_TTL_SECONDS = 3600  # 1 hour


class UserTokenConfig(BaseModel):
    """Self-service, user-issued API keys (ADR-0002).

    ``enabled`` defaults to ``False`` so a PVC-less deployment (local dev,
    a pre-rollout pod) parses and runs unchanged -- this feature is
    strictly opt-in and additive to the ADR-0001 static service-token
    model. Persistence and lifecycle semantics live in
    ``forge_security.oidc.user_tokens``; this block only carries the
    switch and the TTL bounds enforced at mint time.
    """

    enabled: bool = False
    store_path: str = "/app/data/user_tokens.json"
    default_ttl_seconds: int = 2_592_000  # 30 days
    max_ttl_seconds: int = 7_776_000  # 90 days
    # DoS guard (security review finding): caps the number of ACTIVE
    # (non-revoked, non-expired) tokens a single owner may hold at once.
    # Enforced by forge-gateway at mint time -- this field only carries the
    # configured limit. 25 is a generous default for real per-device usage
    # while still bounding the shared PVC store and the cost of the
    # single-global-lock rewrite-the-whole-document write path.
    max_tokens_per_owner: int = Field(default=25, ge=1)

    @field_validator("store_path")
    @classmethod
    def validate_store_path_absolute(cls, v: str) -> str:
        if not v.startswith("/"):
            msg = (
                "security.service_tokens.user_tokens.store_path must be an "
                f"absolute path, got {v!r}"
            )
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_ttl_bounds(self) -> UserTokenConfig:
        if not (_USER_TOKEN_MIN_TTL_SECONDS <= self.default_ttl_seconds <= self.max_ttl_seconds):
            msg = (
                "security.service_tokens.user_tokens.default_ttl_seconds "
                f"({self.default_ttl_seconds}) must be between "
                f"{_USER_TOKEN_MIN_TTL_SECONDS} (the 1-hour floor) and "
                f"max_ttl_seconds ({self.max_ttl_seconds}), inclusive"
            )
            raise ValueError(msg)
        return self


class ServiceTokenConfig(BaseModel):
    """Machine-client credentials (MCP, A2A, CI)."""

    enabled: bool = False
    tokens: list[ServiceToken] = Field(default_factory=list)
    user_tokens: UserTokenConfig = Field(default_factory=UserTokenConfig)

    @model_validator(mode="after")
    def validate_reserved_id_namespace(self) -> ServiceTokenConfig:
        """The ``u_`` id prefix is reserved for dynamically-minted user
        tokens (ADR-0002 SS7.1) -- a statically configured token cannot
        claim it, so a future mint can never collide with a static entry."""
        for token in self.tokens:
            if token.id.startswith(USER_TOKEN_ID_PREFIX):
                msg = (
                    f"security.service_tokens.tokens id {token.id!r} uses the "
                    f"{USER_TOKEN_ID_PREFIX!r} prefix, which is reserved for "
                    "dynamically-minted user tokens (ADR-0002). Rename the "
                    "static token id."
                )
                raise ValueError(msg)
        return self


class RoleBinding(BaseModel):
    """Maps claims (groups / emails / subs) to a role.

    A principal receives the union of roles from every binding it
    matches. ``groups``/``subs`` match case-sensitively; ``emails`` match
    case-insensitively (see ADR-0001 SS6.3).
    """

    role: str
    groups: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    subs: list[str] = Field(default_factory=list)


_DEFAULT_ROLES: dict[str, list[str]] = {
    "viewer": ["config:read", "metrics:read"],
    "user": ["config:read", "metrics:read", "agent:invoke", "tools:invoke"],
    "admin": ["*"],
}


class AuthorizationConfig(BaseModel):
    """Claims -> roles -> permissions. Deny by default.

    A principal matching no binding receives zero permissions
    (``default_role`` is ``None`` by default -- deny). Setting
    ``default_role`` is a deliberate, greppable act of trusting everyone
    the IdP authenticates.
    """

    default_role: str | None = None
    metrics_public: bool = True
    roles: dict[str, list[str]] = Field(default_factory=lambda: dict(_DEFAULT_ROLES))
    bindings: list[RoleBinding] = Field(default_factory=list)


class AgentWeaveConfig(BaseModel):
    """AgentWeave workload-plane settings (ADR-0004).

    Inert for the *human* OIDC plane (ADR-0001): nothing here can ever
    disable authentication on ``:8000`` -- see ``SecurityConfig``, which
    has no field connecting ``agentweave`` to ``auth``/``oidc``. As of
    ADR-0004, ``enabled`` un-inerts the separate *workload* (SPIFFE + OPA
    + mTLS) plane on ``:8443``: when true, forge-gateway builds a
    ``WorkloadPlane`` and starts the mTLS listener; when false (the
    default-safe value for old configs), no workload listener is started
    at all and this block has zero runtime effect.
    """

    enabled: bool = False
    trust_domain: str = "forge.local"
    spiffe_endpoint: str = "unix:///run/spire/sockets/agent.sock"
    authz_provider: str = "opa"
    opa_endpoint: str = "http://localhost:8181"
    identity_secret: str | None = None
    trust_policy: TrustPolicy = TrustPolicy.STRICT

    # --- Workload plane (ADR-0004 SS7.3) ---
    workload_listener_port: int = 8443
    """Port for the pod-terminated mTLS ("a2a-mtls") listener."""

    audit_backend: Literal["stdout", "file"] = "stdout"
    """Workload-plane audit sink. ``stdout`` (default) needs no volume
    and is safe with any replica count; ``file`` requires ``audit_path``
    and a writable, single-writer volume."""

    audit_path: str | None = None
    """Path for ``FileAuditBackend`` when ``audit_backend == "file"``.
    Ignored (falls back to stdout) when unset."""


class APIKeyConfig(BaseModel):
    """API key authentication for the gateway.

    Deprecated (ADR-0001 SS11): accepted for one minor release and
    internally represents a synthetic ``legacy-api-key`` admin service
    token. Emits a ``DeprecationWarning`` at every construction where a
    key is actually configured.
    """

    enabled: bool = False
    keys: list[SecretRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def warn_if_configured(self) -> APIKeyConfig:
        if self.enabled or self.keys:
            warnings.warn(
                "security.api_keys is deprecated (ADR-0001) and will be removed "
                "in the next minor release. It is translated into a synthetic "
                "'legacy-api-key' admin service token. Migrate to "
                "security.service_tokens.",
                DeprecationWarning,
                stacklevel=2,
            )
        return self


class SecurityConfig(BaseModel):
    """Top-level security configuration (ADR-0001: Dex OIDC authentication).

    ``auth.mode`` defaults to ``enforce`` and the default ``oidc`` block
    points at this deployment's real Dex instance, so an entirely absent
    ``security:`` block in ``forge.yaml`` still enforces authentication
    against a working mechanism -- the absence of configuration can never
    mean the absence of authentication.
    """

    auth: SecurityAuthConfig = Field(default_factory=SecurityAuthConfig)
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)
    service_tokens: ServiceTokenConfig = Field(default_factory=ServiceTokenConfig)
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig)

    rate_limit_rpm: int = 60
    allowed_origins: list[str] = Field(default_factory=lambda: ["https://forgeai.hvslocal"])

    # --- Deprecated / inert (kept so old configs still parse) ---
    agentweave: AgentWeaveConfig = Field(default_factory=AgentWeaveConfig)
    api_keys: APIKeyConfig = Field(default_factory=APIKeyConfig)

    @model_validator(mode="before")
    @classmethod
    def reject_jwt_secret(cls, data: Any) -> Any:
        """security.jwt_secret is REMOVED (ADR-0001 SS11). It was a symmetric
        HS256 shared secret verified with ``verify_aud=False`` -- leaving it
        accepted would leave a second, weaker door. Its presence (from a
        loaded YAML dict, or a direct keyword argument) is a hard error."""
        if isinstance(data, dict) and data.get("jwt_secret") is not None:
            msg = (
                "security.jwt_secret has been removed (ADR-0001): it was an "
                "HS256 shared secret verified with verify_aud=False, which "
                "cannot validate Dex's RS256 tokens and accepted tokens from "
                "any Dex client. Migrate to security.oidc (Dex RS256 + JWKS) "
                "or security.service_tokens (machine clients). Remove "
                "jwt_secret from your config."
            )
            raise ValueError(msg)
        return data

    @model_validator(mode="after")
    def validate_enforce_has_mechanism(self) -> SecurityConfig:
        """ADR-0001 SS8.4 #1: enforce + oidc disabled + no service tokens is a
        declaration with nothing to enforce with."""
        if (
            self.auth.mode == AuthMode.ENFORCE
            and not self.oidc.enabled
            and not self.service_tokens.tokens
        ):
            msg = (
                "security.auth.mode is 'enforce' but there is nothing to "
                "enforce with: security.oidc.enabled is false and "
                "security.service_tokens.tokens is empty. Enable OIDC, "
                "configure at least one service token, or set "
                "auth.mode: dev_insecure (also requires FORGE_DEV_INSECURE=1)."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_session_secure(self) -> SecurityConfig:
        """ADR-0001 SS8.4 #2: a non-secure session cookie is only tolerable in
        dev_insecure mode."""
        if (
            self.oidc.enabled
            and not self.oidc.session.secure
            and self.auth.mode != AuthMode.DEV_INSECURE
        ):
            msg = (
                "security.oidc.session.secure is false but security.auth.mode "
                "is not 'dev_insecure'. The session cookie must be Secure "
                "outside of dev_insecure mode."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_wildcard_origin(self) -> SecurityConfig:
        """ADR-0001 SS8.4 #4: a wildcard origin with allow_credentials=True
        reflects any origin -- with a session cookie in play that is a total
        compromise."""
        if "*" in self.allowed_origins and self.oidc.enabled:
            msg = (
                "security.allowed_origins contains '*' while security.oidc is "
                "enabled. A wildcard origin combined with credentialed CORS "
                "(required for the session cookie) reflects any origin, which "
                "is a total compromise once a session cookie exists. List "
                "explicit origins."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_issuer_scheme(self) -> SecurityConfig:
        """ADR-0001 SS8.4 #8: oidc.issuer must be https:// unless dev_insecure."""
        if (
            self.oidc.enabled
            and self.auth.mode != AuthMode.DEV_INSECURE
            and not self.oidc.issuer.startswith("https://")
        ):
            msg = (
                f"security.oidc.issuer ({self.oidc.issuer!r}) must use https:// "
                "outside of dev_insecure mode."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_bindings_and_roles(self) -> SecurityConfig:
        """ADR-0001 SS8.4 #6: typo-proofing -- a misspelled permission must
        never fail open."""
        known_permissions = {p.value for p in Permission} | {PERMISSION_WILDCARD}
        for role_name, perms in self.authorization.roles.items():
            for perm in perms:
                if perm not in known_permissions:
                    msg = (
                        f"authorization.roles[{role_name!r}] references unknown "
                        f"permission {perm!r}. Known permissions: "
                        f"{sorted(known_permissions)}"
                    )
                    raise ValueError(msg)
        for binding in self.authorization.bindings:
            if binding.role not in self.authorization.roles:
                msg = (
                    f"authorization.bindings references unknown role "
                    f"{binding.role!r}. Known roles: "
                    f"{sorted(self.authorization.roles)}"
                )
                raise ValueError(msg)
        return self


# --- Agents Configuration ---


class PeerAgent(BaseModel):
    """A peer agent that this Forge instance can communicate with."""

    name: str
    endpoint: str
    trust_level: TrustLevel = TrustLevel.LOW
    capabilities: list[str] = Field(default_factory=list)
    spiffe_id: str | None = None
    """Expected SPIFFE ID of this peer's SVID (ADR-0004 SS7.3), used by
    the outbound ``PeerCaller`` to verify it reached the intended peer
    over mTLS. ``None`` when the peer is not on the workload plane (or
    the caller has no expectation to verify against)."""


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
    peers: list[PeerAgent] = Field(default_factory=list)


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

    @model_validator(mode="after")
    def validate_agentweave_peers_are_pinned(self) -> ForgeConfig:
        """ADR-0004 SS7.3 mandatory pinning: when the workload (SPIFFE +
        OPA mTLS) plane is enabled, every configured peer must carry a
        pinned ``spiffe_id``. Without one, the outbound ``PeerCaller``
        has nothing to verify the responding peer's SVID against, and a
        task payload could be sent to -- and trusted from -- whatever
        workload happens to answer on that endpoint, so long as it holds
        any valid SPIRE-issued certificate in the trust domain. Reject
        that configuration at load time rather than let it parse and
        fail (or worse, silently under-verify) at runtime."""
        if self.security.agentweave.enabled:
            unpinned = [peer.name for peer in self.agents.peers if not peer.spiffe_id]
            if unpinned:
                names = ", ".join(unpinned)
                msg = (
                    "security.agentweave.enabled is true, but the following "
                    f"agents.peers have no spiffe_id pinned: {names}. ADR-0004 "
                    "SS7.3 requires every peer called over the mTLS workload "
                    "plane to have a pinned spiffe_id so PeerCaller can verify "
                    "it reached the intended peer before sending a task "
                    "payload. Set spiffe_id for each peer, or remove it from "
                    "agents.peers, or disable security.agentweave."
                )
                raise ValueError(msg)
        return self
