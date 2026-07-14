"""Tests for the admin approval-gate routes (ADR-0005 SS6.2).

A gated tool call drafts an ``ApprovalRequest`` instead of executing (see
``forge_agent.active.gate``). These routes are the human decision surface:

1. ``GET /v1/admin/approvals`` -- list pending + recent (``config:read``).
2. ``POST /v1/admin/approvals/{id}/approve`` -- executes the real gated
   call exactly once (``agent:approve``).
3. ``POST /v1/admin/approvals/{id}/reject`` -- never executes (``agent:approve``).

Security cases covered: auth required (401), ``agent:approve`` required
for approve/reject (403 without it), single-use approval (second approve
-> 409), reject never executes, unknown id fails cleanly (404).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from forge_agent.active.gate import ApprovalStore, ToolGate
from forge_config.schema import AuthorizationConfig, ServiceToken
from forge_gateway import security
from forge_gateway.activity import recent_activity
from forge_gateway.routes import approvals
from forge_security.oidc import Authorizer, ServiceTokenVerifier

ADMIN_TOKEN = "forge_sk_admin-test_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
VIEWER_TOKEN = "forge_sk_viewer-test_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
_ADMIN_DIGEST = hashlib.sha256(ADMIN_TOKEN.encode("utf-8")).hexdigest()
_VIEWER_DIGEST = hashlib.sha256(VIEWER_TOKEN.encode("utf-8")).hexdigest()

ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
VIEWER_HEADERS = {"Authorization": f"Bearer {VIEWER_TOKEN}"}


class _Recorder:
    """A fake gated tool: records every real invocation."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"published": True, **kwargs}


class _FakeAuditTrail:
    """A fake AgentWeave-shaped ``AuditTrail`` (``AuditTrailLike``):
    records every ``record_auth_check`` call so tests can assert
    approve/reject decisions are routed to it, without needing a real
    workload plane (ADR-0005 SS6.3, security review finding #3)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_auth_check(
        self,
        caller_id: str,
        action: str,
        resource: str,
        decision: str,
        duration: float,
        reason: str = "",
    ) -> None:
        self.calls.append(
            {
                "caller_id": caller_id,
                "action": action,
                "resource": resource,
                "decision": decision,
                "reason": reason,
            }
        )


class _RaisingAuditTrail:
    """An AuditTrail double whose ``record_auth_check`` always raises --
    the approve/reject decision must still succeed (degrade gracefully)."""

    async def record_auth_check(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit backend unavailable")


@pytest.fixture()
def _wire_auth() -> Iterator[None]:
    """``admin`` (wildcard -> includes agent:approve) and ``viewer``
    (config:read/metrics:read only -- no agent:approve) service tokens."""
    security.configure_auth(
        session_codec=None,
        service_token_verifier=ServiceTokenVerifier(
            [
                ServiceToken(id="admin-test", secret_sha256=_ADMIN_DIGEST, roles=["admin"]),
                ServiceToken(id="viewer-test", secret_sha256=_VIEWER_DIGEST, roles=["viewer"]),
            ]
        ),
        oidc_verifier=None,
        authorizer=Authorizer(AuthorizationConfig()),
        dev_insecure=False,
    )
    yield
    security.reset_auth()


@pytest.fixture()
def _wire_approvals() -> Iterator[ToolGate]:
    gate = ToolGate(ApprovalStore())
    approvals.set_state(gate)
    yield gate
    approvals.set_state(None)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(approvals.router)
    return app


@pytest.fixture()
def app() -> FastAPI:
    return _make_app()


@pytest.fixture()
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def _gated_draft(_wire_approvals: ToolGate) -> tuple[_Recorder, dict[str, Any]]:
    """Draft a pending approval against a fake gated "publish" tool."""
    real_tool = _Recorder()
    gated = _wire_approvals.wrap("publish_post", real_tool, requested_by="social-manager")
    draft = await gated(text="hello world")
    return real_tool, draft


@pytest.fixture()
async def _gated_draft_with_secret(
    _wire_approvals: ToolGate,
) -> tuple[_Recorder, dict[str, Any]]:
    """Draft a pending approval whose arguments carry a sensitive-named
    field, as a gated OpenAPI/manual tool's real arguments might (e.g. an
    auth token forwarded as a declared parameter)."""
    real_tool = _Recorder()
    gated = _wire_approvals.wrap("publish_post", real_tool, requested_by="social-manager")
    draft = await gated(text="hello world", api_key="super-secret-value")
    return real_tool, draft


# =========================================================================
# 1. Authentication / authorization
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_approvals")
class TestApprovalsAuth:
    async def test_list_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/v1/admin/approvals")
        assert resp.status_code == 401

    async def test_approve_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/v1/admin/approvals/some-id/approve")
        assert resp.status_code == 401

    async def test_reject_requires_auth(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/v1/admin/approvals/some-id/reject")
        assert resp.status_code == 401

    async def test_list_allowed_with_config_read_only(
        self, async_client: httpx.AsyncClient
    ) -> None:
        """Listing only requires config:read -- viewer (no agent:approve) can see."""
        resp = await async_client.get("/v1/admin/approvals", headers=VIEWER_HEADERS)
        assert resp.status_code == 200

    async def test_approve_requires_agent_approve_permission(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/approvals/some-id/approve", headers=VIEWER_HEADERS
        )
        assert resp.status_code == 403

    async def test_reject_requires_agent_approve_permission(
        self, async_client: httpx.AsyncClient
    ) -> None:
        resp = await async_client.post("/v1/admin/approvals/some-id/reject", headers=VIEWER_HEADERS)
        assert resp.status_code == 403

    async def test_approve_succeeds_with_agent_approve_permission(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        _real_tool, draft = _gated_draft
        resp = await async_client.post(
            f"/v1/admin/approvals/{draft['approval_id']}/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200


# =========================================================================
# 2. Listing
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_approvals")
class TestListApprovals:
    async def test_list_is_empty_with_no_gate_configured(
        self, async_client: httpx.AsyncClient
    ) -> None:
        approvals.set_state(None)
        resp = await async_client.get("/v1/admin/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_shows_pending_draft(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        _real_tool, draft = _gated_draft
        resp = await async_client.get("/v1/admin/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        match = next(item for item in body if item["id"] == draft["approval_id"])
        assert match["status"] == "pending"
        assert match["tool"] == "publish_post"
        assert match["arguments"] == {"text": "hello world"}
        assert match["requested_by"] == "social-manager"


# =========================================================================
# 2b. Argument redaction (ADR-0005 SS11, security review finding #2)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_approvals")
class TestListApprovalsRedaction:
    async def test_config_read_only_caller_sees_redacted_sensitive_argument(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft_with_secret: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        """A viewer (config:read only, no agent:approve) must never see a
        gated draft's real secret-shaped argument values."""
        _real_tool, draft = _gated_draft_with_secret
        resp = await async_client.get("/v1/admin/approvals", headers=VIEWER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        match = next(item for item in body if item["id"] == draft["approval_id"])
        assert match["arguments"]["api_key"] == "***REDACTED***"
        assert match["arguments"]["text"] == "hello world"

    async def test_agent_approve_holder_sees_unredacted_arguments(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft_with_secret: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        """A caller who holds `agent:approve` (e.g. admin) needs the real
        arguments to make an informed approve/reject decision."""
        _real_tool, draft = _gated_draft_with_secret
        resp = await async_client.get("/v1/admin/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        match = next(item for item in body if item["id"] == draft["approval_id"])
        assert match["arguments"]["api_key"] == "super-secret-value"


# =========================================================================
# 3. Approve executes exactly once; reject never executes
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_approvals")
class TestApproveAndReject:
    async def test_approve_executes_the_real_tool_and_returns_result(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        real_tool, draft = _gated_draft
        resp = await async_client.post(
            f"/v1/admin/approvals/{draft['approval_id']}/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["result"] == {"published": True, "text": "hello world"}
        assert real_tool.calls == [{"text": "hello world"}]

    async def test_second_approve_on_same_id_is_409_and_does_not_reexecute(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        real_tool, draft = _gated_draft
        approval_id = draft["approval_id"]

        first = await async_client.post(
            f"/v1/admin/approvals/{approval_id}/approve", headers=ADMIN_HEADERS
        )
        assert first.status_code == 200

        second = await async_client.post(
            f"/v1/admin/approvals/{approval_id}/approve", headers=ADMIN_HEADERS
        )
        assert second.status_code == 409
        assert real_tool.calls == [{"text": "hello world"}]  # exactly once

    async def test_reject_never_executes_the_real_tool(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        real_tool, draft = _gated_draft
        resp = await async_client.post(
            f"/v1/admin/approvals/{draft['approval_id']}/reject", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert real_tool.calls == []

    async def test_reject_is_idempotent(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        _real_tool, draft = _gated_draft
        approval_id = draft["approval_id"]

        first = await async_client.post(
            f"/v1/admin/approvals/{approval_id}/reject", headers=ADMIN_HEADERS
        )
        second = await async_client.post(
            f"/v1/admin/approvals/{approval_id}/reject", headers=ADMIN_HEADERS
        )
        assert first.status_code == 200
        assert second.status_code == 200

    async def test_approve_after_reject_is_409(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        real_tool, draft = _gated_draft
        approval_id = draft["approval_id"]

        await async_client.post(f"/v1/admin/approvals/{approval_id}/reject", headers=ADMIN_HEADERS)
        resp = await async_client.post(
            f"/v1/admin/approvals/{approval_id}/approve", headers=ADMIN_HEADERS
        )

        assert resp.status_code == 409
        assert real_tool.calls == []


# =========================================================================
# 3b. Audit trail for approve/reject (ADR-0005 SS6.3, finding #3)
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_approvals")
class TestApprovalDecisionAudit:
    @pytest.fixture(autouse=True)
    def _reset_activity(self) -> Iterator[None]:
        recent_activity.reset()
        yield
        recent_activity.reset()

    async def test_approve_records_an_activity_entry_attributed_to_the_caller(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        _real_tool, draft = _gated_draft
        resp = await async_client.post(
            f"/v1/admin/approvals/{draft['approval_id']}/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200

        records = recent_activity.snapshot()
        match = next(r for r in records if r.arguments.get("approval_id") == draft["approval_id"])
        assert match.arguments["decision"] == "approved"
        assert match.arguments["actor"] == "svc:admin-test"
        assert match.tool == "publish_post"
        assert match.ok is True

    async def test_reject_records_an_activity_entry_attributed_to_the_caller(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        _real_tool, draft = _gated_draft
        resp = await async_client.post(
            f"/v1/admin/approvals/{draft['approval_id']}/reject", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200

        records = recent_activity.snapshot()
        match = next(r for r in records if r.arguments.get("approval_id") == draft["approval_id"])
        assert match.arguments["decision"] == "rejected"
        assert match.arguments["actor"] == "svc:admin-test"

    async def test_approve_routes_to_the_agentweave_audit_trail_when_wired(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        _real_tool, draft = _gated_draft
        audit = _FakeAuditTrail()
        approvals.set_audit_trail(audit)
        try:
            resp = await async_client.post(
                f"/v1/admin/approvals/{draft['approval_id']}/approve", headers=ADMIN_HEADERS
            )
        finally:
            approvals.set_audit_trail(None)

        assert resp.status_code == 200
        assert len(audit.calls) == 1
        assert audit.calls[0]["caller_id"] == "svc:admin-test"
        assert audit.calls[0]["decision"] == "allow"
        assert audit.calls[0]["action"] == "agent:approve"

    async def test_approve_degrades_gracefully_when_audit_trail_raises(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        """A broken/unreachable AgentWeave audit backend must never block
        an approve/reject decision (ADR-0004's non-gating posture)."""
        _real_tool, draft = _gated_draft
        approvals.set_audit_trail(_RaisingAuditTrail())
        try:
            resp = await async_client.post(
                f"/v1/admin/approvals/{draft['approval_id']}/approve", headers=ADMIN_HEADERS
            )
        finally:
            approvals.set_audit_trail(None)

        assert resp.status_code == 200

    async def test_no_audit_trail_wired_is_a_no_op(
        self,
        async_client: httpx.AsyncClient,
        _gated_draft: tuple[_Recorder, dict[str, Any]],
    ) -> None:
        """No workload plane / AuditTrail configured (the common case) --
        approve/reject must still succeed, using only the activity feed."""
        _real_tool, draft = _gated_draft
        resp = await async_client.post(
            f"/v1/admin/approvals/{draft['approval_id']}/reject", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200


# =========================================================================
# 4. Unknown ids fail cleanly
# =========================================================================


@pytest.mark.usefixtures("_wire_auth", "_wire_approvals")
class TestUnknownApprovals:
    async def test_approve_unknown_id_returns_404(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post(
            "/v1/admin/approvals/does-not-exist/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 404

    async def test_reject_unknown_id_returns_404(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post(
            "/v1/admin/approvals/does-not-exist/reject", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 404
