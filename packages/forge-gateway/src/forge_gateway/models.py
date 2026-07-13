"""Request/response models for the Forge Gateway."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# --- Agent API models ---


class InvokeRequest(BaseModel):
    """Request to invoke the agent programmatically."""

    intent: str
    params: dict[str, Any] = Field(default_factory=dict)
    tool_hints: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    session_id: str | None = None
    agent: str | None = None  # Agent persona to use


class InvokeResponse(BaseModel):
    """Response from programmatic agent invocation."""

    result: Any
    session_id: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    model: str | None = None


class ConversationRequest(BaseModel):
    """Request for conversational interaction."""

    message: str
    session_id: str | None = None
    stream: bool = False
    agent: str | None = None


class ConversationResponse(BaseModel):
    """Response from conversational interaction."""

    message: str
    session_id: str
    tools_used: list[str] = Field(default_factory=list)
    model: str | None = None


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    code: str | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str = ""
    components: dict[str, str] = Field(default_factory=dict)
    auth_mode: str = "enforce"
    auth_healthy: bool = True


# --- Admin API models ---


class AdminConfigResponse(BaseModel):
    """Response containing the current config (secrets redacted)."""

    config: dict[str, Any]
    path: str = ""


class AdminConfigUpdateRequest(BaseModel):
    """Request to update the config."""

    config: dict[str, Any]


class AdminConfigUpdateResponse(BaseModel):
    """Response from a config update operation."""

    success: bool
    reloaded: bool = False
    message: str = ""


class AdminToolInfo(BaseModel):
    """Metadata about a registered tool."""

    name: str
    description: str = ""
    source: str = "configured"


class AdminToolPreviewRequest(BaseModel):
    """Request to preview tools from an OpenAPI source."""

    source: dict[str, Any]


class AdminToolPreviewResponse(BaseModel):
    """Response from a tool preview (dry-run) operation."""

    tools: list[AdminToolInfo] = Field(default_factory=list)
    count: int = 0


class AdminSessionResponse(BaseModel):
    """Metadata about an active session."""

    session_id: str
    message_count: int = 0
    agent: str | None = None


class AdminPeerStatus(str, Enum):
    """Peer connection status."""

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class AdminPeerResponse(BaseModel):
    """Metadata about a configured peer."""

    name: str
    endpoint: str
    trust_level: str = "low"
    capabilities: list[str] = Field(default_factory=list)
    status: AdminPeerStatus = AdminPeerStatus.UNKNOWN


# --- User-issued API key models (ADR-0002) ---


class MintTokenRequest(BaseModel):
    """POST /v1/auth/tokens request body.

    ``roles`` omitted defaults to the caller's own current roles;
    ``ttl_seconds`` omitted defaults to
    ``security.service_tokens.user_tokens.default_ttl_seconds``.
    """

    label: str
    roles: list[str] | None = None
    ttl_seconds: int | None = None


class MintTokenResponse(BaseModel):
    """POST /v1/auth/tokens response -- the ONLY time the raw token is
    ever returned. It is not persisted anywhere and cannot be retrieved
    again after this response."""

    id: str
    token: str
    label: str
    roles: list[str]
    created_at: str
    expires_at: str


class TokenListItem(BaseModel):
    """One entry in GET /v1/auth/tokens -- metadata only, never the
    secret or its digest. ``owner_email`` is populated only in the
    admin ``?all=true`` view."""

    id: str
    label: str
    roles: list[str]
    created_at: str
    expires_at: str
    revoked_at: str | None = None
    owner_email: str | None = None


class TokenListResponse(BaseModel):
    """GET /v1/auth/tokens response."""

    tokens: list[TokenListItem] = Field(default_factory=list)
