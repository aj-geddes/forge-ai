"""Structured audit logging for Forge tool-call events.

Uses *structlog* to emit machine-readable JSON audit records that can be
consumed by centralised logging pipelines.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog


class AuditAction(str, Enum):
    """Well-known audit event actions."""

    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    RATE_LIMIT = "rate_limit"
    POLICY_DENY = "policy_deny"


@dataclass
class ToolCallEvent:
    """Structured representation of a tool-call audit event."""

    caller_id: str
    tool_name: str
    action: AuditAction = AuditAction.TOOL_CALL
    allowed: bool = True
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


class AuditLogger:
    """Emit structured audit log entries via *structlog*.

    Parameters
    ----------
    logger_name:
        Name passed to ``structlog.get_logger``.
    """

    def __init__(self, logger_name: str = "forge.security.audit") -> None:
        self._log: structlog.stdlib.BoundLogger = structlog.get_logger(logger_name)

    async def log_event(self, event: ToolCallEvent) -> None:
        """Log a ``ToolCallEvent``."""
        self._log.info(
            event.action.value,
            event_id=event.event_id,
            caller_id=event.caller_id,
            tool_name=event.tool_name,
            allowed=event.allowed,
            reason=event.reason,
            timestamp=event.timestamp,
            **event.metadata,
        )

    async def log_tool_call(
        self,
        caller_id: str,
        tool_name: str,
        *,
        allowed: bool = True,
        reason: str = "",
        **extra: Any,
    ) -> ToolCallEvent:
        """Convenience wrapper: build a ``ToolCallEvent`` and log it."""
        evt = ToolCallEvent(
            caller_id=caller_id,
            tool_name=tool_name,
            action=AuditAction.TOOL_CALL,
            allowed=allowed,
            reason=reason,
            metadata=extra,
        )
        await self.log_event(evt)
        return evt
