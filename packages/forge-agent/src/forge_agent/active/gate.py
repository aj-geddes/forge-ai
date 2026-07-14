"""The human-approval gate for irreversible/outward-facing tool calls
(ADR-0005 SS6.2).

A tool marked ``requires_approval`` (see ``forge_config.schema.ManualTool``)
is wrapped by :class:`ToolGate` at tool-build time. Invoking the wrapped
tool never runs the real side effect: it drafts an :class:`ApprovalRequest`
in the :class:`ApprovalStore` and returns a "pending approval" result to
the caller. The real side effect only fires once a human calls
:meth:`ToolGate.approve` (e.g. via ``POST /v1/admin/approvals/{id}/approve``,
guarded by the ``agent:approve`` permission) -- and it fires **exactly
once**: approving an already-resolved request raises
:class:`ApprovalAlreadyResolvedError`, so the underlying tool can never be
double-executed. Rejecting (:meth:`ToolGate.reject`) never executes the
real tool, and is idempotent-safe.

**"Exactly once" holds only within a single process/replica (security
review finding #5).** :class:`ApprovalStore` is in-memory and NOT shared
across replicas. If the same gate is running in more than one pod (e.g.
``agent.replicaCount > 1`` or ``autoscaling.enabled``), a gated tool
drafted on replica A is invisible to replica B's store -- a caller could
in principle approve independently-drafted copies of the same logical
action on two different pods, executing it twice. This is NOT a
theoretical edge case for outward-facing actions (e.g. social
publishing): it is why
``deploy/helm/forge/templates/deployment.yaml`` fails the Helm render (a
loud, build-time error, not a silent runtime gap) when a gated tool is
configured (any ``ManualTool.requires_approval``,
``OpenAPISource.requires_approval``, or non-empty
``OpenAPISource.approval_operations``) together with more than one
replica. hvs-k8s already runs a single agent replica, so this guard is
defence against a *future* silent regression (e.g. an operator bumping
``replicaCount`` without realizing a gate is configured), not a change to
today's deployment.

Restart-durability follow-up (ADR-0005 SS5.4/SS6.2): this MVP's
:class:`ApprovalStore` is in-memory only -- a pending approval does not
survive a process restart, and does not survive scaling to more than one
replica (see above). It is structured so a durable, cross-replica backend
(mirroring ``forge_agent.agent.store.ConversationStore``'s in-memory/Redis
split, ADR-0003) can be slotted in behind the same async interface later
without changing any caller -- that Redis-backed ``ApprovalStore`` is the
real fix for multi-replica exactly-once and is tracked as a follow-up,
not implemented here.

Approval expiry (``ApprovalPolicy.approval_timeout_seconds`` in the ADR) is
NOT implemented here -- that is a property of the active-runtime
supervisor (ADR-0005 Phase 1), which does not exist yet. This module only
guarantees that an *unknown* or *already-resolved* approval id fails
cleanly (404/409); a "parked too long" sweep is a follow-up once the
supervisor exists to own run/task lifecycle.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger("forge.agent.active.gate")

GatedToolFunc = Callable[..., Awaitable[Any]]
DraftSummaryBuilder = Callable[[dict[str, Any]], str]

# ADR-0005 SS6.2/SS11 (security review finding #4): a sane default cap on
# total stored ApprovalRequests (pending + resolved) per ApprovalStore,
# so an unconfigured store is bounded out of the box -- the DoS
# protection is not opt-in. Generous enough that no realistic pending
# workload hits it, small enough to keep worst-case memory bounded.
DEFAULT_MAX_APPROVAL_REQUESTS = 1000


class ApprovalStatus(str, Enum):
    """Lifecycle of a single :class:`ApprovalRequest`."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Deterministic SHA-256 hex digest of a tool-call argument dict.

    Keys are sorted (``json.dumps(..., sort_keys=True)``) so the hash is
    independent of dict insertion/iteration order -- used to bind an
    ``ApprovalRequest`` to the exact arguments it was drafted (and later
    approved) for.
    """
    canonical = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ApprovalRequest:
    """A parked, human-reviewable draft of a gated tool call.

    Attributes:
        id: Opaque unique identifier.
        tool_name: The name of the gated tool this request is for.
        arguments: The exact keyword arguments the tool was invoked with.
        argument_hash: ``hash_arguments(arguments)`` captured at draft
            time -- re-checked before execution (defense in depth).
        requested_by: The requesting agent/persona name, when known.
        run_id: The autonomous run or conversation/session id, when known.
        draft_summary: A human-readable description of what would happen.
        created_at: When the draft was created (UTC).
        status: Current lifecycle state.
        resolved_at: When ``status`` last transitioned away from PENDING.
        resolved_by: The principal (subject) who approved/rejected it.
        result: The real tool's return value, once executed.
        error: The real tool's exception message, if execution failed.
    """

    id: str
    tool_name: str
    arguments: dict[str, Any]
    argument_hash: str
    requested_by: str | None
    run_id: str | None
    draft_summary: str
    created_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    result: Any = None
    error: str | None = None


class ApprovalError(Exception):
    """Base class for approval-flow errors; carries an HTTP-shaped status
    code so the admin API can translate it without inspecting the type."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApprovalNotFoundError(ApprovalError):
    """Raised when an approval id is unknown to the store."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"Approval request {approval_id!r} not found", status_code=404)
        self.approval_id = approval_id


class ApprovalAlreadyResolvedError(ApprovalError):
    """Raised when approve/reject is attempted on a non-pending request."""

    def __init__(self, approval_id: str, status: ApprovalStatus) -> None:
        super().__init__(
            f"Approval request {approval_id!r} is already resolved (status={status.value})",
            status_code=409,
        )
        self.approval_id = approval_id
        self.status = status


class ApprovalStoreFullError(ApprovalError):
    """Raised when the :class:`ApprovalStore` is at capacity and cannot
    accept a new draft (ADR-0005 SS6.2/SS11, security review finding #4:
    unbounded ``ApprovalStore`` growth is a DoS vector -- a chatty or
    adversarial model could otherwise retain unbounded drafts in memory).

    Fail-closed: when the cap is hit and no RESOLVED entries remain to
    evict, a new gated tool call is refused outright -- it is never
    silently executed ungated, and never silently dropped as "drafted"
    without actually being retained."""

    def __init__(self, max_size: int) -> None:
        super().__init__(
            f"Approval store is at capacity ({max_size} requests) and every stored "
            "request is still pending; refusing to draft a new approval request. "
            "This tool call has NOT been executed.",
            status_code=503,
        )
        self.max_size = max_size


class ApprovalStore:
    """Persists :class:`ApprovalRequest` objects, guarding concurrent
    access with an ``asyncio.Lock`` so two concurrent approve/reject calls
    on the same id can never both "win" the state transition.

    In-memory ``dict`` for MVP (ADR-0005 SS6.2). Every access goes through
    this async interface (never a bare dict lookup from callers), so a
    durable/cross-replica backend can replace the storage without
    touching :class:`ToolGate` or any route.
    """

    def __init__(self, max_size: int = DEFAULT_MAX_APPROVAL_REQUESTS) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = asyncio.Lock()
        self._max_size = max_size

    @property
    def max_size(self) -> int:
        """The maximum number of requests (pending + resolved) this store
        will hold at once (ADR-0005 SS6.2/SS11, finding #4)."""
        return self._max_size

    def _evict_oldest_resolved_locked(self) -> None:
        """Evict RESOLVED requests, oldest-resolved-first, until the store
        is back under `max_size` or no RESOLVED entries remain.

        Must be called while holding `self._lock`. PENDING requests are
        never evicted here -- a human still needs to see and decide on
        them; only already-decided history is expendable.
        """
        if len(self._requests) < self._max_size:
            return
        resolved = sorted(
            (r for r in self._requests.values() if r.status != ApprovalStatus.PENDING),
            key=lambda r: r.resolved_at or r.created_at,
        )
        for request in resolved:
            if len(self._requests) < self._max_size:
                break
            del self._requests[request.id]

    async def create(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        requested_by: str | None,
        run_id: str | None,
        draft_summary: str,
    ) -> ApprovalRequest:
        """Create and persist a new PENDING :class:`ApprovalRequest`.

        Bounded (finding #4): if the store is at `max_size`, the oldest
        RESOLVED entries are evicted first to make room. If the store is
        still at capacity after that (i.e. every stored request is still
        PENDING), this raises :class:`ApprovalStoreFullError` instead of
        drafting -- fail-closed, never silently ungated.
        """
        async with self._lock:
            self._evict_oldest_resolved_locked()
            if len(self._requests) >= self._max_size:
                raise ApprovalStoreFullError(self._max_size)

            request = ApprovalRequest(
                id=uuid.uuid4().hex,
                tool_name=tool_name,
                arguments=dict(arguments),
                argument_hash=hash_arguments(arguments),
                requested_by=requested_by,
                run_id=run_id,
                draft_summary=draft_summary,
                created_at=datetime.now(UTC),
            )
            self._requests[request.id] = request
            return request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return the request with *approval_id*, or ``None`` if unknown."""
        async with self._lock:
            return self._requests.get(approval_id)

    async def list_all(self) -> list[ApprovalRequest]:
        """Return every request, newest first (pending + resolved)."""
        async with self._lock:
            return sorted(self._requests.values(), key=lambda r: r.created_at, reverse=True)

    def _get_or_raise(self, approval_id: str) -> ApprovalRequest:
        request = self._requests.get(approval_id)
        if request is None:
            raise ApprovalNotFoundError(approval_id)
        return request

    async def transition_to_approved(
        self, approval_id: str, *, resolved_by: str | None
    ) -> ApprovalRequest:
        """Atomically move a PENDING request to APPROVED.

        Raises:
            ApprovalNotFoundError: *approval_id* is unknown.
            ApprovalAlreadyResolvedError: the request is not PENDING (this
                is what makes an approval single-use: a second concurrent
                or sequential call loses the race/finds the terminal state
                and raises here, before the real tool would ever run again).
        """
        async with self._lock:
            request = self._get_or_raise(approval_id)
            if request.status != ApprovalStatus.PENDING:
                raise ApprovalAlreadyResolvedError(approval_id, request.status)
            request.status = ApprovalStatus.APPROVED
            request.resolved_at = datetime.now(UTC)
            request.resolved_by = resolved_by
            return request

    async def transition_to_rejected(
        self, approval_id: str, *, resolved_by: str | None
    ) -> ApprovalRequest:
        """Atomically move a PENDING request to REJECTED.

        Idempotent-safe: rejecting an already-REJECTED request is a no-op
        that returns the existing record rather than raising. Rejecting a
        request in any other terminal state (APPROVED) raises
        :class:`ApprovalAlreadyResolvedError` -- an executed action cannot
        be retroactively rejected.
        """
        async with self._lock:
            request = self._get_or_raise(approval_id)
            if request.status == ApprovalStatus.REJECTED:
                return request
            if request.status != ApprovalStatus.PENDING:
                raise ApprovalAlreadyResolvedError(approval_id, request.status)
            request.status = ApprovalStatus.REJECTED
            request.resolved_at = datetime.now(UTC)
            request.resolved_by = resolved_by
            return request

    async def record_result(
        self, approval_id: str, *, result: Any = None, error: str | None = None
    ) -> None:
        """Attach the real tool's outcome to an already-approved request."""
        async with self._lock:
            request = self._requests.get(approval_id)
            if request is not None:
                request.result = result
                request.error = error


def _default_draft_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    return f"Call '{tool_name}' with arguments {arguments!r}"


class ToolGate:
    """Wraps a side-effecting tool function so it drafts instead of
    executes, and exposes the approve/reject decision surface.

    Usage (see ``forge_agent.builder.manual.ManualToolBuilder`` for the
    real integration point):

        gate = ToolGate(ApprovalStore())
        gated_publish = gate.wrap("publish_post", real_publish_func)
        # gated_publish(**kwargs) now drafts instead of executing.
        # ... a human approves via the admin API ...
        result = await gate.approve(approval_id)  # runs real_publish_func once
    """

    def __init__(self, store: ApprovalStore) -> None:
        self._store = store
        self._registered: dict[str, GatedToolFunc] = {}

    def wrap(
        self,
        tool_name: str,
        func: GatedToolFunc,
        *,
        requested_by: str | None = None,
        run_id: str | None = None,
        draft_summary_builder: DraftSummaryBuilder | None = None,
    ) -> GatedToolFunc:
        """Return an async function that drafts an approval instead of
        calling *func*. Registers *func* so a later :meth:`approve` can
        invoke the real call by tool name.

        Args:
            tool_name: Stable name identifying the gated tool.
            func: The real (side-effecting) tool coroutine function.
            requested_by: The requesting agent/persona, recorded on the
                draft for audit (ADR-0005 SS6.3).
            run_id: The autonomous run or session id, when known.
            draft_summary_builder: Optional callable that renders a
                human-readable draft summary from the call arguments.
                Defaults to a generic ``"Call '<tool>' with <args>"``.
        """
        self._registered[tool_name] = func

        async def gated(**kwargs: Any) -> dict[str, Any]:
            summary = (
                draft_summary_builder(kwargs)
                if draft_summary_builder is not None
                else _default_draft_summary(tool_name, kwargs)
            )
            request = await self._store.create(
                tool_name=tool_name,
                arguments=kwargs,
                requested_by=requested_by,
                run_id=run_id,
                draft_summary=summary,
            )
            logger.info(
                "Gated tool '%s' drafted as approval request %s (requested_by=%s, run_id=%s)",
                tool_name,
                request.id,
                requested_by,
                run_id,
            )
            return {
                "status": "drafted",
                "approval_id": request.id,
                "tool": tool_name,
                "draft_summary": summary,
                "message": (
                    f"'{tool_name}' is a gated action and requires human approval "
                    f"before it runs. Drafted as approval request {request.id!r} -- "
                    "it has NOT been executed yet."
                ),
            }

        return gated

    async def approve(self, approval_id: str, *, resolved_by: str | None = None) -> Any:
        """Approve *approval_id* and execute the real tool exactly once.

        Raises:
            ApprovalNotFoundError: unknown id (404).
            ApprovalAlreadyResolvedError: not currently PENDING (409) --
                this is what makes approval single-use.
            ApprovalError: the stored arguments no longer match the
                argument hash captured at draft time (409), or no real
                tool is registered for this request's tool name (500).

        Returns:
            The real tool's return value.
        """
        request = await self._store.transition_to_approved(approval_id, resolved_by=resolved_by)

        # Defense in depth: re-verify the argument-hash binding immediately
        # before executing. A mismatch means the persisted arguments were
        # altered after the draft was hashed -- refuse rather than run
        # content nobody actually approved.
        if hash_arguments(request.arguments) != request.argument_hash:
            raise ApprovalError(
                f"Approval request {approval_id!r} argument hash mismatch; refusing to execute",
                status_code=409,
            )

        func = self._registered.get(request.tool_name)
        if func is None:
            raise ApprovalError(
                f"No gated tool registered for {request.tool_name!r}; cannot execute",
                status_code=500,
            )

        try:
            result = await func(**request.arguments)
        except Exception as exc:
            await self._store.record_result(approval_id, error=str(exc))
            raise
        await self._store.record_result(approval_id, result=result)
        return result

    async def reject(self, approval_id: str, *, resolved_by: str | None = None) -> ApprovalRequest:
        """Reject *approval_id*. Never executes the real tool.

        Raises:
            ApprovalNotFoundError: unknown id (404).
            ApprovalAlreadyResolvedError: the request was already APPROVED
                (409) -- an executed action cannot be retroactively
                rejected. Rejecting an already-REJECTED request is a
                no-op (idempotent-safe).
        """
        return await self._store.transition_to_rejected(approval_id, resolved_by=resolved_by)

    async def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        """Look up a single approval request by id."""
        return await self._store.get(approval_id)

    async def list_approvals(self) -> list[ApprovalRequest]:
        """List every approval request (pending + resolved), newest first."""
        return await self._store.list_all()
