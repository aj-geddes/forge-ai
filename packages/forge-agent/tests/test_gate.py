"""Tests for the human-approval gate (ADR-0005 SS6.2).

Covers the core security properties of the draft -> approve -> execute
control that makes an autonomous "publish"-style tool safe:

1. A gated tool does NOT execute on first call -- it only drafts.
2. The real action fires EXACTLY ONCE on approve, and NEVER on reject.
3. An approval is single-use (a second approve on the same id fails).
4. Approvals are bound to the argument hash captured at draft time.
5. Approving/rejecting an unknown id fails cleanly (404).
6. Rejecting is idempotent-safe; approving an already-rejected/approved
   id fails cleanly (409).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from forge_agent.active.gate import (
    ApprovalAlreadyResolvedError,
    ApprovalError,
    ApprovalNotFoundError,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    ApprovalStoreFullError,
    ToolGate,
    hash_arguments,
)


def _make_gate() -> ToolGate:
    return ToolGate(ApprovalStore())


class _Recorder:
    """A fake gated tool: records every real invocation so tests can
    assert the underlying side effect fired exactly once (or never)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"published": True, **kwargs}


class TestDraftInsteadOfExecute:
    """A gated tool's first call must draft, never execute."""

    async def test_gated_call_does_not_execute_the_real_tool(self) -> None:
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)

        result = await gated(text="hello world")

        assert real_tool.calls == []
        assert result["status"] == "drafted"

    async def test_drafted_result_carries_an_approval_id(self) -> None:
        gate = _make_gate()
        gated = gate.wrap("publish_post", _Recorder())

        result = await gated(text="hello world")

        assert isinstance(result["approval_id"], str)
        assert result["approval_id"]

    async def test_draft_creates_a_pending_approval_request(self) -> None:
        gate = _make_gate()
        gated = gate.wrap("publish_post", _Recorder())

        result = await gated(text="hello world")
        approval = await gate.get_approval(result["approval_id"])

        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.tool_name == "publish_post"
        assert approval.arguments == {"text": "hello world"}

    async def test_approval_request_captures_hash_requester_and_run_id(self) -> None:
        gate = _make_gate()
        gated = gate.wrap(
            "publish_post",
            _Recorder(),
            requested_by="social-manager",
            run_id="run-42",
        )

        result = await gated(text="hello world")
        approval = await gate.get_approval(result["approval_id"])

        assert approval is not None
        assert approval.requested_by == "social-manager"
        assert approval.run_id == "run-42"
        assert approval.argument_hash == hash_arguments({"text": "hello world"})
        assert approval.draft_summary  # a human-readable summary is present
        assert approval.created_at is not None


class TestApproveExecutesExactlyOnce:
    """The real side effect fires exactly once on approve."""

    async def test_approve_executes_the_real_tool(self) -> None:
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)
        draft = await gated(text="hello world")

        result = await gate.approve(draft["approval_id"])

        assert real_tool.calls == [{"text": "hello world"}]
        assert result == {"published": True, "text": "hello world"}

    async def test_approve_marks_the_request_approved(self) -> None:
        gate = _make_gate()
        gated = gate.wrap("publish_post", _Recorder())
        draft = await gated(text="hello world")

        await gate.approve(draft["approval_id"])
        approval = await gate.get_approval(draft["approval_id"])

        assert approval is not None
        assert approval.status == ApprovalStatus.APPROVED

    async def test_second_approve_on_same_id_does_not_execute_again(self) -> None:
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)
        draft = await gated(text="hello world")

        await gate.approve(draft["approval_id"])
        with pytest.raises(ApprovalAlreadyResolvedError):
            await gate.approve(draft["approval_id"])

        assert real_tool.calls == [{"text": "hello world"}]  # exactly once

    async def test_concurrent_double_approve_executes_exactly_once(self) -> None:
        """Two concurrent approve() calls on the same id must not both
        win the transition -- only one may execute the real tool."""
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)
        draft = await gated(text="hello world")

        results = await asyncio.gather(
            gate.approve(draft["approval_id"]),
            gate.approve(draft["approval_id"]),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ApprovalAlreadyResolvedError)
        assert real_tool.calls == [{"text": "hello world"}]


class TestRejectNeverExecutes:
    """Rejecting a draft must never fire the real side effect."""

    async def test_reject_never_calls_the_real_tool(self) -> None:
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)
        draft = await gated(text="hello world")

        await gate.reject(draft["approval_id"])

        assert real_tool.calls == []

    async def test_reject_marks_the_request_rejected(self) -> None:
        gate = _make_gate()
        gated = gate.wrap("publish_post", _Recorder())
        draft = await gated(text="hello world")

        rejected = await gate.reject(draft["approval_id"])

        assert rejected.status == ApprovalStatus.REJECTED

    async def test_reject_is_idempotent(self) -> None:
        gate = _make_gate()
        gated = gate.wrap("publish_post", _Recorder())
        draft = await gated(text="hello world")

        first = await gate.reject(draft["approval_id"])
        second = await gate.reject(draft["approval_id"])

        assert first.status == second.status == ApprovalStatus.REJECTED

    async def test_reject_after_approve_fails_cleanly(self) -> None:
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)
        draft = await gated(text="hello world")
        await gate.approve(draft["approval_id"])

        with pytest.raises(ApprovalAlreadyResolvedError):
            await gate.reject(draft["approval_id"])

        assert real_tool.calls == [{"text": "hello world"}]  # unaffected


class TestArgumentHashBinding:
    """An approval is bound to the exact arguments hashed at draft time."""

    def test_hash_arguments_is_order_independent(self) -> None:
        assert hash_arguments({"a": 1, "b": 2}) == hash_arguments({"b": 2, "a": 1})

    def test_hash_arguments_differs_for_different_args(self) -> None:
        assert hash_arguments({"text": "hello"}) != hash_arguments({"text": "goodbye"})

    async def test_approve_refuses_if_stored_arguments_were_tampered_with(self) -> None:
        """Defense in depth: if the persisted arguments no longer match the
        hash captured at draft time (e.g. a backing-store bug/tamper), the
        gate must refuse to execute rather than run mismatched arguments."""
        gate = _make_gate()
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)
        draft = await gated(text="hello world")

        approval = await gate.get_approval(draft["approval_id"])
        assert approval is not None
        approval.arguments["text"] = "attacker-controlled content"  # simulate tampering

        with pytest.raises(ApprovalError):
            await gate.approve(draft["approval_id"])

        assert real_tool.calls == []


class TestUnknownOrExpiredApprovals:
    """Approving/rejecting an id the store doesn't know about fails cleanly."""

    async def test_approve_unknown_id_raises_not_found(self) -> None:
        gate = _make_gate()
        with pytest.raises(ApprovalNotFoundError):
            await gate.approve("does-not-exist")

    async def test_reject_unknown_id_raises_not_found(self) -> None:
        gate = _make_gate()
        with pytest.raises(ApprovalNotFoundError):
            await gate.reject("does-not-exist")

    def test_not_found_error_has_404_status(self) -> None:
        error = ApprovalNotFoundError("some-id")
        assert error.status_code == 404

    def test_already_resolved_error_has_409_status(self) -> None:
        error = ApprovalAlreadyResolvedError("some-id", ApprovalStatus.APPROVED)
        assert error.status_code == 409


class TestApprovalStoreListing:
    """The store lists pending + recent approvals for the admin surface."""

    async def test_list_all_returns_every_request_newest_first(self) -> None:
        store = ApprovalStore()
        first = await store.create(
            tool_name="a", arguments={}, requested_by=None, run_id=None, draft_summary="a"
        )
        second = await store.create(
            tool_name="b", arguments={}, requested_by=None, run_id=None, draft_summary="b"
        )

        listed = await store.list_all()

        assert [r.id for r in listed] == [second.id, first.id]

    async def test_list_all_reflects_resolved_status(self) -> None:
        gate = _make_gate()
        gated = gate.wrap("publish_post", _Recorder())
        draft = await gated(text="hello")
        await gate.approve(draft["approval_id"])

        listed = await gate.list_approvals()

        assert listed[0].status == ApprovalStatus.APPROVED


class TestApprovalRequestDataclass:
    def test_default_status_is_pending(self) -> None:
        request = ApprovalRequest(
            id="x",
            tool_name="t",
            arguments={},
            argument_hash="h",
            requested_by=None,
            run_id=None,
            draft_summary="d",
            created_at=datetime.now(UTC),
        )
        assert request.status == ApprovalStatus.PENDING


class TestApprovalStoreIsBounded:
    """ADR-0005 SS6.2/SS11, security review finding #4: a chatty or
    adversarial model must not be able to grow the in-memory
    ``ApprovalStore`` without bound (DoS). The store caps total stored
    requests, evicting the oldest RESOLVED entries first; if the cap is
    still exceeded (i.e. every stored request is PENDING), it fails
    closed -- refusing to draft a new request rather than silently
    executing the tool it would have gated."""

    async def test_store_size_never_exceeds_the_configured_cap(self) -> None:
        store = ApprovalStore(max_size=5)
        for i in range(20):
            await store.create(
                tool_name="t",
                arguments={"i": i},
                requested_by=None,
                run_id=None,
                draft_summary="d",
            )
            request = (await store.list_all())[0]
            await store.transition_to_approved(request.id, resolved_by=None)

            assert len(await store.list_all()) <= 5

    async def test_resolved_entries_are_evicted_before_pending_ones(self) -> None:
        store = ApprovalStore(max_size=3)
        resolved_ids = []
        for i in range(2):
            r = await store.create(
                tool_name="t",
                arguments={"i": i},
                requested_by=None,
                run_id=None,
                draft_summary="d",
            )
            await store.transition_to_approved(r.id, resolved_by=None)
            resolved_ids.append(r.id)

        pending = await store.create(
            tool_name="t",
            arguments={"i": "pending"},
            requested_by=None,
            run_id=None,
            draft_summary="d",
        )

        # Store is now at cap (2 resolved + 1 pending == 3). One more
        # draft must evict a RESOLVED entry, never the pending one.
        await store.create(
            tool_name="t",
            arguments={"i": "new"},
            requested_by=None,
            run_id=None,
            draft_summary="d",
        )

        remaining_ids = {r.id for r in await store.list_all()}
        assert pending.id in remaining_ids
        assert len(remaining_ids) <= 3
        assert resolved_ids[0] not in remaining_ids or resolved_ids[1] not in remaining_ids

    async def test_store_full_of_pending_requests_fails_closed(self) -> None:
        """When every stored request is PENDING (nothing resolved to
        evict), a new draft must be refused -- fail-closed -- rather than
        silently executing the tool it would have gated."""
        store = ApprovalStore(max_size=2)
        await store.create(
            tool_name="t",
            arguments={"i": 1},
            requested_by=None,
            run_id=None,
            draft_summary="d",
        )
        await store.create(
            tool_name="t",
            arguments={"i": 2},
            requested_by=None,
            run_id=None,
            draft_summary="d",
        )

        with pytest.raises(ApprovalStoreFullError):
            await store.create(
                tool_name="t",
                arguments={"i": 3},
                requested_by=None,
                run_id=None,
                draft_summary="d",
            )

    async def test_gated_tool_call_raises_when_store_is_full_and_does_not_execute(self) -> None:
        """The fail-closed behavior must propagate all the way through
        ToolGate.wrap: a full store means the gated call raises rather
        than draft OR execute."""
        store = ApprovalStore(max_size=1)
        gate = ToolGate(store)
        real_tool = _Recorder()
        gated = gate.wrap("publish_post", real_tool)

        await gated(text="first")  # fills the store to capacity (1 pending)

        with pytest.raises(ApprovalStoreFullError):
            await gated(text="second")

        assert real_tool.calls == []

    async def test_default_max_size_is_a_generous_sane_cap(self) -> None:
        """A default-constructed store is bounded out of the box -- the
        DoS protection is not opt-in."""
        store = ApprovalStore()
        assert store.max_size > 0
        assert store.max_size <= 100_000
