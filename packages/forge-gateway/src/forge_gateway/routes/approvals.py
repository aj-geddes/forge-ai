"""Admin API routes for the human-approval gate (ADR-0005 SS6.2).

A tool marked ``requires_approval`` (``forge_config.schema.ManualTool``)
drafts an ``ApprovalRequest`` instead of executing (see
``forge_agent.active.gate.ToolGate``, wired into
``ForgeAgent.registry.tool_gate``). These routes are the human decision
surface for those drafts:

- ``GET /v1/admin/approvals`` -- list pending + resolved requests. Guarded
  by ``config:read`` (read-only, same posture as every other admin GET).
- ``POST /v1/admin/approvals/{id}/approve`` -- executes the real gated
  call exactly once. Guarded by the new ``agent:approve`` permission.
- ``POST /v1/admin/approvals/{id}/reject`` -- marks the request rejected;
  never executes. Also guarded by ``agent:approve``.

``agent:approve`` is deliberately a distinct, narrower permission than
``config:write``: approving an irreversible/outward-facing action is not
the same authority as editing configuration (ADR-0005 SS6.2/SS6.7).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException
from forge_agent.active.gate import (
    ApprovalAlreadyResolvedError,
    ApprovalError,
    ApprovalNotFoundError,
    ApprovalRequest,
    ToolGate,
)
from forge_config.schema import Permission
from forge_security.oidc import Principal
from pydantic import BaseModel

from forge_gateway import redaction, security
from forge_gateway.activity import recent_activity

logger = logging.getLogger("forge.gateway.approvals")

router = APIRouter(prefix="/v1/admin/approvals", tags=["approvals"])

_read = Depends(security.require_permission("config:read"))
_approve = Depends(security.require_permission("agent:approve"))


class AuditTrailLike(Protocol):
    """The one method this module relies on from an audit trail --
    structurally the same shape as
    ``forge_security.workload.authz.AuditTrailLike`` (ADR-0004), reused
    here (ADR-0005 SS6.3) to record approve/reject decisions to the
    AgentWeave ``AuditTrail`` when the workload plane is enabled."""

    async def record_auth_check(
        self,
        caller_id: str,
        action: str,
        resource: str,
        decision: str,
        duration: float,
        reason: str = "",
    ) -> None: ...


# Module-level state set from the application lifespan (mirrors
# routes/admin.py's set_state pattern): the live ToolGate shared by the
# agent's ToolSurfaceRegistry, or None when no agent/gate is available.
_tool_gate: ToolGate | None = None
# ADR-0005 SS6.3 (security review finding #3): the AgentWeave AuditTrail,
# when the workload plane (ADR-0004) is enabled; None otherwise. Every
# approve/reject decision is always recorded to the activity feed
# regardless -- this is an additional, best-effort sink.
_audit_trail: AuditTrailLike | None = None


def set_state(tool_gate: ToolGate | None) -> None:
    """Wire the live ``ToolGate`` from the application lifespan."""
    global _tool_gate
    _tool_gate = tool_gate


def set_audit_trail(audit_trail: AuditTrailLike | None) -> None:
    """Wire the live AgentWeave ``AuditTrail`` from the application
    lifespan (ADR-0005 SS6.3, security review finding #3) -- present only
    when the workload plane (ADR-0004) is enabled and healthy. ``None``
    (the default) degrades gracefully: approve/reject decisions are still
    always recorded to the activity feed."""
    global _audit_trail
    _audit_trail = audit_trail


class ApprovalRequestResponse(BaseModel):
    """Wire shape for a single parked/resolved approval request."""

    id: str
    tool: str
    arguments: dict[str, Any]
    argument_hash: str
    status: str
    requested_by: str | None
    run_id: str | None
    draft_summary: str
    created_at: str
    resolved_at: str | None
    resolved_by: str | None


class ApprovalDecisionResponse(BaseModel):
    """Response for an approve/reject decision."""

    id: str
    status: str
    result: Any = None


def _to_response(request: ApprovalRequest, *, redact: bool) -> ApprovalRequestResponse:
    """Build the wire response for *request*.

    Security review finding #2 (ADR-0005 SS11): a gated tool's drafted
    ``arguments`` can carry secrets (an auth token forwarded as a declared
    parameter, an API key in a request body, ...). ``GET
    /v1/admin/approvals`` is reachable by ``config:read`` alone (a viewer),
    while approve/reject need ``agent:approve``. When *redact* is true
    (the caller lacks ``agent:approve``), the arguments are redacted with
    the same logic as ``GET /v1/admin/config`` before serializing -- a
    caller who cannot approve/reject never needs the raw values, only
    enough to recognize *what* is pending.
    """
    arguments = dict(request.arguments)
    if redact:
        redaction.redact_secrets(arguments)
    return ApprovalRequestResponse(
        id=request.id,
        tool=request.tool_name,
        arguments=arguments,
        argument_hash=request.argument_hash,
        status=request.status.value,
        requested_by=request.requested_by,
        run_id=request.run_id,
        draft_summary=request.draft_summary,
        created_at=request.created_at.isoformat(),
        resolved_at=request.resolved_at.isoformat() if request.resolved_at else None,
        resolved_by=request.resolved_by,
    )


def _require_gate() -> ToolGate:
    if _tool_gate is None:
        raise HTTPException(status_code=404, detail="No approval request found")
    return _tool_gate


async def _audit_decision(
    *,
    approval_id: str,
    tool_name: str,
    decision: str,
    action: str,
    principal: Principal,
) -> None:
    """Record an approve/reject decision (ADR-0005 SS6.3, security review
    finding #3).

    Always recorded to the recent-activity feed (``GET
    /v1/admin/activity``), attributing the decision to the calling
    principal. Additionally, best-effort, routed to the AgentWeave
    ``AuditTrail`` when the workload plane is enabled (``set_state(...,
    audit_trail=...)``) -- degrades gracefully (logs, never raises) so a
    broken/unreachable audit backend can never block an approve/reject
    decision that has already taken effect.
    """
    recent_activity.record(
        tool=tool_name,
        arguments={"approval_id": approval_id, "decision": decision, "actor": principal.sub},
        ok=True,
        error=None,
        interface="approval",
        session_id=approval_id,
    )
    if _audit_trail is None:
        return
    start = time.monotonic()
    try:
        await _audit_trail.record_auth_check(
            caller_id=principal.sub,
            action=action,
            resource=f"approval:{tool_name}",
            decision="allow",
            duration=time.monotonic() - start,
            reason=f"approval_id={approval_id}",
        )
    except Exception:
        logger.warning(
            "Failed to record %s decision for approval %s to the AgentWeave audit "
            "trail -- the decision itself already succeeded and is recorded in the "
            "activity feed.",
            decision,
            approval_id,
            exc_info=True,
        )


@router.get(
    "",
    response_model=list[ApprovalRequestResponse],
)
async def list_approvals(principal: Principal = _read) -> list[ApprovalRequestResponse]:
    """List every approval request (pending + resolved), newest first.

    Returns an empty list -- rather than erroring -- when no agent/gate is
    wired, matching the rest of the admin API's "nothing configured yet"
    posture (e.g. ``routes/admin.py::list_tools``). Arguments are redacted
    (security review finding #2) unless the caller holds ``agent:approve``.
    """
    if _tool_gate is None:
        return []
    requests = await _tool_gate.list_approvals()
    can_see_arguments = Permission.AGENT_APPROVE.value in principal.permissions
    return [_to_response(r, redact=not can_see_arguments) for r in requests]


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalDecisionResponse,
    responses={404: {"description": "Not found"}, 409: {"description": "Already resolved"}},
)
async def approve_approval(
    approval_id: str,
    principal: Principal = _approve,
) -> ApprovalDecisionResponse:
    """Approve *approval_id* and execute the real gated call exactly once.

    Single-use: a second approve on an already-resolved id raises 409
    (``ApprovalAlreadyResolvedError``), so the underlying side effect can
    never fire twice. An unknown id is a clean 404.
    """
    gate = _require_gate()
    try:
        result = await gate.approve(approval_id, resolved_by=principal.sub)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    resolved = await gate.get_approval(approval_id)
    tool_name = resolved.tool_name if resolved is not None else "unknown"
    await _audit_decision(
        approval_id=approval_id,
        tool_name=tool_name,
        decision="approved",
        action=Permission.AGENT_APPROVE.value,
        principal=principal,
    )
    return ApprovalDecisionResponse(id=approval_id, status="approved", result=result)


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalDecisionResponse,
    responses={404: {"description": "Not found"}, 409: {"description": "Already resolved"}},
)
async def reject_approval(
    approval_id: str,
    principal: Principal = _approve,
) -> ApprovalDecisionResponse:
    """Reject *approval_id*. Never executes the real gated call.

    Idempotent-safe: rejecting an already-rejected id succeeds again
    (200). Rejecting an already-approved (and therefore already executed)
    id fails cleanly (409) -- an executed action cannot be retroactively
    rejected. An unknown id is a clean 404.
    """
    gate = _require_gate()
    try:
        request = await gate.reject(approval_id, resolved_by=principal.sub)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _audit_decision(
        approval_id=approval_id,
        tool_name=request.tool_name,
        decision="rejected",
        action="agent:reject",
        principal=principal,
    )
    return ApprovalDecisionResponse(id=approval_id, status=request.status.value)
