"""Request/response models for the Forge Gateway."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
