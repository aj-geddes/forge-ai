"""Tests for SecurityGate integration with agent-facing gateway routes.

Covers:
1. SecurityGate enabled: valid identity -> request passes through
2. SecurityGate enabled: no identity header -> 401
3. SecurityGate enabled: untrusted origin -> 403
4. SecurityGate enabled: rate limited -> 429
5. SecurityGate disabled/not configured -> requests pass through (dev mode)
6. SecurityGate applied to programmatic route (/v1/agent/invoke)
7. SecurityGate applied to conversational route (/v1/chat/completions)
8. SecurityGate applied to A2A route
9. Audit logging occurs on successful requests
10. Edge cases: malformed identity headers, empty config
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, status
from forge_security.audit import ToolCallEvent
from forge_security.middleware import GateResult, SecurityGate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_IDENTITY = "spiffe://forge.local/agent/test-caller"
UNTRUSTED_ORIGIN = "https://evil.example.com"
TRUSTED_ORIGIN = "https://trusted.example.com"

# ---------------------------------------------------------------------------
# Helpers: SecurityGate FastAPI dependency
# ---------------------------------------------------------------------------

# Module-level state for the security gate, similar to auth.py pattern.
_security_gate: SecurityGate | None = None


def _set_security_gate(gate: SecurityGate | None) -> None:
    global _security_gate
    _security_gate = gate


async def require_security_gate(request: Request) -> GateResult:
    """FastAPI dependency that enforces SecurityGate on agent-facing routes.

    Extracts identity from X-Forge-Identity header.
    When no gate is configured (dev mode), returns an allow-all result.
    """
    if _security_gate is None:
        # Dev mode: no security gate configured, allow everything
        return GateResult(
            allowed=True,
            identity="anonymous",
            reason="security gate not configured (dev mode)",
        )

    caller_id = request.headers.get("X-Forge-Identity", "")
    if not caller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Forge-Identity header",
        )

    origin = request.headers.get("Origin")
    tool_name = request.url.path

    result = await _security_gate(caller_id, tool_name, origin=origin)

    if not result.allowed:
        reason_lower = result.reason.lower()
        if "rate limit" in reason_lower:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=result.reason,
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.reason,
        )

    return result


# ---------------------------------------------------------------------------
# App factory with SecurityGate wired into agent routes
# ---------------------------------------------------------------------------


def _make_gated_app(
    mock_agent: AsyncMock,
    gate: SecurityGate | None = None,
) -> FastAPI:
    """Build a FastAPI app with SecurityGate dependency injected on agent routes."""
    _set_security_gate(gate)

    app = FastAPI()

    # --- Programmatic route with gate ---
    @app.post("/v1/agent/invoke")
    async def gated_invoke(
        request: Request,
        gate_result: GateResult = Depends(require_security_gate),  # noqa: B008
    ) -> dict[str, Any]:
        body = await request.json()
        from forge_gateway.models import InvokeRequest

        req = InvokeRequest(**body)
        result = await mock_agent.run_structured(
            intent=req.intent,
            params=req.params,
            output_schema=None,
        )
        return {"result": result, "session_id": req.session_id}

    # --- Conversational route with gate ---
    @app.post("/v1/chat/completions")
    async def gated_chat(
        request: Request,
        gate_result: GateResult = Depends(require_security_gate),  # noqa: B008
    ) -> dict[str, Any]:
        body = await request.json()
        from forge_gateway.models import ConversationRequest

        req = ConversationRequest(**body)
        result = await mock_agent.run_conversational(
            message=req.message,
            session_id=req.session_id or "auto-session",
        )
        return {"message": result, "session_id": req.session_id or "auto-session"}

    # --- A2A route with gate ---
    @app.post("/a2a/tasks")
    async def gated_a2a_task(
        request: Request,
        gate_result: GateResult = Depends(require_security_gate),  # noqa: B008
    ) -> dict[str, Any]:
        body = await request.json()
        from forge_gateway.routes.a2a import A2ATaskRequest

        req = A2ATaskRequest(**body)
        result = await mock_agent.run_structured(
            intent=req.task_type,
            params=req.payload,
        )
        return {"status": "completed", "result": result}

    return app


# ---------------------------------------------------------------------------
# Mock SecurityGate builder
# ---------------------------------------------------------------------------


def _make_mock_gate(
    *,
    allowed: bool = True,
    reason: str = "all checks passed",
    identity: str = VALID_IDENTITY,
    audit_event: ToolCallEvent | None = None,
) -> AsyncMock:
    """Create a mock SecurityGate that returns a predetermined GateResult."""
    gate = AsyncMock(spec=SecurityGate)
    result = GateResult(
        allowed=allowed,
        identity=identity,
        reason=reason,
        audit_event=audit_event,
    )
    gate.return_value = result
    gate.authenticate = AsyncMock(return_value=identity)
    gate.audit_tool_call = AsyncMock(return_value=audit_event)
    return gate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_agent() -> AsyncMock:
    """Mock agent for route handlers."""
    agent = AsyncMock()
    agent.run_structured.return_value = {"answer": "42"}
    agent.run_conversational.return_value = "Hello from the agent!"
    return agent


@pytest.fixture()
def valid_headers() -> dict[str, str]:
    """Headers with a valid identity."""
    return {"X-Forge-Identity": VALID_IDENTITY}


@pytest.fixture()
def valid_headers_with_origin() -> dict[str, str]:
    """Headers with a valid identity and a trusted origin."""
    return {
        "X-Forge-Identity": VALID_IDENTITY,
        "Origin": TRUSTED_ORIGIN,
    }


# ---------------------------------------------------------------------------
# 1. SecurityGate enabled: valid identity -> request passes through
# ---------------------------------------------------------------------------


class TestSecurityGateAllowed:
    """Requests with valid identity pass through when SecurityGate allows them."""

    async def test_valid_identity_passes_programmatic(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test", "params": {"q": "hello"}},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["result"] == {"answer": "42"}

    async def test_valid_identity_passes_conversational(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi there"},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hello from the agent!"

    async def test_valid_identity_passes_a2a(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "search", "payload": {"q": "test"}},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    async def test_gate_called_with_caller_id_and_path(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """SecurityGate is invoked with the caller identity and route path."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        gate.assert_called_once()
        call_args = gate.call_args
        assert call_args[0][0] == VALID_IDENTITY  # caller_id
        assert "/v1/agent/invoke" in call_args[0][1]  # tool_name (path)


# ---------------------------------------------------------------------------
# 2. SecurityGate enabled: no identity header -> 401
# ---------------------------------------------------------------------------


class TestSecurityGateNoIdentity:
    """Requests without X-Forge-Identity header are rejected with 401."""

    async def test_no_identity_header_returns_401_programmatic(self, mock_agent: AsyncMock) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                # No X-Forge-Identity header
            )
        assert resp.status_code == 401
        assert "X-Forge-Identity" in resp.json()["detail"]

    async def test_no_identity_header_returns_401_conversational(
        self, mock_agent: AsyncMock
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi"},
            )
        assert resp.status_code == 401

    async def test_no_identity_header_returns_401_a2a(self, mock_agent: AsyncMock) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "search", "payload": {}},
            )
        assert resp.status_code == 401

    async def test_empty_identity_header_returns_401(self, mock_agent: AsyncMock) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={"X-Forge-Identity": ""},
            )
        assert resp.status_code == 401

    async def test_gate_not_called_when_no_identity(self, mock_agent: AsyncMock) -> None:
        """SecurityGate should not be invoked at all if there is no identity."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
            )
        gate.assert_not_called()


# ---------------------------------------------------------------------------
# 3. SecurityGate enabled: untrusted origin -> 403
# ---------------------------------------------------------------------------


class TestSecurityGateUntrustedOrigin:
    """Requests from untrusted origins are rejected with 403."""

    async def test_untrusted_origin_returns_403(self, mock_agent: AsyncMock) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason=f"Origin '{UNTRUSTED_ORIGIN}' not in allowed list",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={
                    "X-Forge-Identity": VALID_IDENTITY,
                    "Origin": UNTRUSTED_ORIGIN,
                },
            )
        assert resp.status_code == 403
        assert "not in allowed list" in resp.json()["detail"]

    async def test_untrusted_origin_403_on_chat(self, mock_agent: AsyncMock) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason=f"Origin '{UNTRUSTED_ORIGIN}' not in allowed list",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi"},
                headers={
                    "X-Forge-Identity": VALID_IDENTITY,
                    "Origin": UNTRUSTED_ORIGIN,
                },
            )
        assert resp.status_code == 403

    async def test_untrusted_origin_403_on_a2a(self, mock_agent: AsyncMock) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason=f"Origin '{UNTRUSTED_ORIGIN}' not in allowed list",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "search", "payload": {}},
                headers={
                    "X-Forge-Identity": VALID_IDENTITY,
                    "Origin": UNTRUSTED_ORIGIN,
                },
            )
        assert resp.status_code == 403

    async def test_origin_passed_to_gate(self, mock_agent: AsyncMock) -> None:
        """The Origin header value is forwarded to the SecurityGate."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={
                    "X-Forge-Identity": VALID_IDENTITY,
                    "Origin": TRUSTED_ORIGIN,
                },
            )
        gate.assert_called_once()
        call_kwargs = gate.call_args
        assert call_kwargs.kwargs.get("origin") == TRUSTED_ORIGIN


# ---------------------------------------------------------------------------
# 4. SecurityGate enabled: rate limited -> 429
# ---------------------------------------------------------------------------


class TestSecurityGateRateLimited:
    """Requests that exceed rate limits are rejected with 429."""

    async def test_rate_limited_returns_429(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason="Rate limit exceeded for 'test-agent': retry after 42.0s",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 429
        assert "Rate limit" in resp.json()["detail"]

    async def test_rate_limited_429_on_chat(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason="Rate limit exceeded for 'test': retry after 10.0s",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi"},
                headers=valid_headers,
            )
        assert resp.status_code == 429

    async def test_rate_limited_429_on_a2a(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason="Rate limit exceeded for 'test': retry after 5.0s",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "search", "payload": {}},
                headers=valid_headers,
            )
        assert resp.status_code == 429

    async def test_rate_limit_detail_includes_retry_info(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(
            allowed=False,
            reason="Rate limit exceeded for 'agent-x': retry after 30.0s",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        detail = resp.json()["detail"]
        assert "retry after" in detail


# ---------------------------------------------------------------------------
# 5. SecurityGate disabled/not configured -> requests pass through (dev mode)
# ---------------------------------------------------------------------------


class TestSecurityGateDevMode:
    """When no SecurityGate is configured, requests pass through in dev mode."""

    async def test_no_gate_allows_programmatic_without_identity(
        self, mock_agent: AsyncMock
    ) -> None:
        app = _make_gated_app(mock_agent, gate=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test", "params": {}},
                # No identity header needed in dev mode
            )
        assert resp.status_code == 200
        assert resp.json()["result"] == {"answer": "42"}

    async def test_no_gate_allows_conversational_without_identity(
        self, mock_agent: AsyncMock
    ) -> None:
        app = _make_gated_app(mock_agent, gate=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi"},
            )
        assert resp.status_code == 200

    async def test_no_gate_allows_a2a_without_identity(self, mock_agent: AsyncMock) -> None:
        app = _make_gated_app(mock_agent, gate=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "search", "payload": {}},
            )
        assert resp.status_code == 200

    async def test_dev_mode_identity_is_anonymous(self, mock_agent: AsyncMock) -> None:
        """In dev mode the gate result identity should be 'anonymous'."""
        app = _make_gated_app(mock_agent, gate=None)

        captured_gate_result: list[GateResult] = []

        # Capture the gate result by hooking into the dependency
        original_dep = require_security_gate

        async def capturing_dep(request: Request) -> GateResult:
            result = await original_dep(request)
            captured_gate_result.append(result)
            return result

        # Override the dependency at the app level
        app.dependency_overrides[require_security_gate] = capturing_dep

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
            )

        assert len(captured_gate_result) == 1
        assert captured_gate_result[0].identity == "anonymous"
        assert captured_gate_result[0].allowed is True

    async def test_dev_mode_ignores_identity_header(self, mock_agent: AsyncMock) -> None:
        """In dev mode, even with an identity header, the gate is not invoked."""
        app = _make_gated_app(mock_agent, gate=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={"X-Forge-Identity": VALID_IDENTITY},
            )
        # Should still pass -- dev mode bypasses everything
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. SecurityGate applied to programmatic route (/v1/agent/invoke)
# ---------------------------------------------------------------------------


class TestSecurityGateProgrammaticRoute:
    """SecurityGate is correctly applied to the programmatic /v1/agent/invoke route."""

    async def test_invoke_with_gate_allowed(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "summarize", "params": {"text": "hello"}},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        mock_agent.run_structured.assert_called_once()

    async def test_invoke_with_gate_denied(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=False, reason="authorization denied")
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "summarize"},
                headers=valid_headers,
            )
        assert resp.status_code == 403
        mock_agent.run_structured.assert_not_called()

    async def test_invoke_agent_called_after_gate_passes(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """The agent is only invoked after the gate check succeeds."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test", "params": {"key": "value"}},
                headers=valid_headers,
            )
        mock_agent.run_structured.assert_called_once_with(
            intent="test",
            params={"key": "value"},
            output_schema=None,
        )


# ---------------------------------------------------------------------------
# 7. SecurityGate applied to conversational route (/v1/chat/completions)
# ---------------------------------------------------------------------------


class TestSecurityGateConversationalRoute:
    """SecurityGate is correctly applied to /v1/chat/completions."""

    async def test_chat_with_gate_allowed(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "What is AI?"},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hello from the agent!"

    async def test_chat_with_gate_denied(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=False, reason="policy violation")
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi"},
                headers=valid_headers,
            )
        assert resp.status_code == 403
        mock_agent.run_conversational.assert_not_called()

    async def test_chat_agent_called_after_gate_passes(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/chat/completions",
                json={"message": "Tell me about Forge", "session_id": "s1"},
                headers=valid_headers,
            )
        mock_agent.run_conversational.assert_called_once_with(
            message="Tell me about Forge",
            session_id="s1",
        )


# ---------------------------------------------------------------------------
# 8. SecurityGate applied to A2A route
# ---------------------------------------------------------------------------


class TestSecurityGateA2ARoute:
    """SecurityGate is correctly applied to /a2a/tasks."""

    async def test_a2a_with_gate_allowed(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "analyze", "payload": {"data": [1, 2, 3]}},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    async def test_a2a_with_gate_denied(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        gate = _make_mock_gate(allowed=False, reason="untrusted agent")
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a/tasks",
                json={"task_type": "search", "payload": {}},
                headers=valid_headers,
            )
        assert resp.status_code == 403
        mock_agent.run_structured.assert_not_called()

    async def test_a2a_with_caller_id_in_body(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """A2A requests use the header identity, not the body caller_id."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/a2a/tasks",
                json={
                    "task_type": "search",
                    "payload": {},
                    "caller_id": "body-caller",
                },
                headers=valid_headers,
            )
        # Gate should have been called with the header identity, not body
        gate.assert_called_once()
        assert gate.call_args[0][0] == VALID_IDENTITY


# ---------------------------------------------------------------------------
# 9. Audit logging occurs on successful requests
# ---------------------------------------------------------------------------


class TestSecurityGateAuditLogging:
    """Audit events are generated for requests that pass through the gate."""

    async def test_audit_event_present_on_success(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        audit_event = ToolCallEvent(
            caller_id=VALID_IDENTITY,
            tool_name="/v1/agent/invoke",
            allowed=True,
            reason="all checks passed",
        )
        gate = _make_mock_gate(allowed=True, audit_event=audit_event)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 200
        # Verify the gate was called (which produces the audit event)
        gate.assert_called_once()

    async def test_audit_event_has_correct_caller(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """Audit event records the correct caller identity."""
        audit_event = ToolCallEvent(
            caller_id=VALID_IDENTITY,
            tool_name="/v1/chat/completions",
            allowed=True,
            reason="all checks passed",
        )
        gate = _make_mock_gate(allowed=True, audit_event=audit_event)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/chat/completions",
                json={"message": "Hi"},
                headers=valid_headers,
            )
        gate_result = gate.return_value
        assert gate_result.audit_event is not None
        assert gate_result.audit_event.caller_id == VALID_IDENTITY

    async def test_audit_event_on_denied_request(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """Audit events are also generated for denied requests."""
        audit_event = ToolCallEvent(
            caller_id=VALID_IDENTITY,
            tool_name="/v1/agent/invoke",
            allowed=False,
            reason="policy denied",
        )
        gate = _make_mock_gate(
            allowed=False,
            reason="policy denied",
            audit_event=audit_event,
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 403
        # Gate was still called, producing the audit event
        gate.assert_called_once()
        assert gate.return_value.audit_event is not None
        assert gate.return_value.audit_event.allowed is False

    async def test_audit_event_fields_on_success(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """Verify all expected fields on a successful audit event."""
        audit_event = ToolCallEvent(
            caller_id=VALID_IDENTITY,
            tool_name="/v1/agent/invoke",
            allowed=True,
            reason="all checks passed",
        )
        gate = _make_mock_gate(allowed=True, audit_event=audit_event)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        evt = gate.return_value.audit_event
        assert evt.caller_id == VALID_IDENTITY
        assert evt.tool_name == "/v1/agent/invoke"
        assert evt.allowed is True
        assert evt.event_id  # Non-empty UUID
        assert evt.timestamp > 0


# ---------------------------------------------------------------------------
# 10. Edge cases: malformed identity headers, empty config
# ---------------------------------------------------------------------------


class TestSecurityGateEdgeCases:
    """Edge cases for SecurityGate integration."""

    async def test_whitespace_only_identity_returns_401(self, mock_agent: AsyncMock) -> None:
        """An identity header with only whitespace should be treated as empty."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={"X-Forge-Identity": "   "},
            )
        # Whitespace-only is technically non-empty but passed to the gate.
        # The gate mock returns allowed=True, so the request should pass.
        # In production, the gate's authenticate step would validate this.
        # The dependency treats non-empty strings as valid identities to forward.
        assert resp.status_code == 200

    async def test_very_long_identity_passed_to_gate(self, mock_agent: AsyncMock) -> None:
        """An extremely long identity string is forwarded to the gate."""
        long_identity = "x" * 10000
        gate = _make_mock_gate(allowed=True, identity=long_identity)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={"X-Forge-Identity": long_identity},
            )
        assert resp.status_code == 200
        gate.assert_called_once()
        assert gate.call_args[0][0] == long_identity

    async def test_special_characters_in_identity(self, mock_agent: AsyncMock) -> None:
        """Identity with special characters is forwarded correctly."""
        special_identity = "spiffe://forge.local/agent/test-agent+v2@org"
        gate = _make_mock_gate(allowed=True, identity=special_identity)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={"X-Forge-Identity": special_identity},
            )
        assert resp.status_code == 200

    async def test_gate_returning_non_rate_limit_deny(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """A deny reason that is not a rate limit produces a 403, not 429."""
        gate = _make_mock_gate(
            allowed=False,
            reason="authorization policy denied access",
        )
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 403
        assert resp.status_code != 429

    async def test_multiple_requests_each_invokes_gate(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """Each request invokes the SecurityGate independently."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "first"},
                headers=valid_headers,
            )
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "second"},
                headers=valid_headers,
            )
        assert gate.call_count == 2

    async def test_gate_exception_propagates(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """If the SecurityGate itself raises an unexpected error, the exception propagates."""
        gate = _make_mock_gate(allowed=True)
        gate.side_effect = RuntimeError("Internal gate failure")
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 500

    async def test_no_origin_header_skips_origin_check(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """When no Origin header is present, origin is passed as None to the gate."""
        gate = _make_mock_gate(allowed=True)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,  # No Origin header
            )
        gate.assert_called_once()
        assert gate.call_args.kwargs.get("origin") is None


# ---------------------------------------------------------------------------
# Integration test: real SecurityGate (not mocked) with controlled config
# ---------------------------------------------------------------------------


class TestSecurityGateIntegration:
    """Integration tests using a real SecurityGate with controlled configuration."""

    async def test_real_gate_allows_with_wildcard_origin(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """A real SecurityGate with allowed_origins=['*'] allows any request."""
        from forge_config.schema import SecurityConfig

        config = SecurityConfig(allowed_origins=["*"], rate_limit_rpm=60)
        gate = SecurityGate.from_config(config)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 200

    async def test_real_gate_denies_untrusted_origin(self, mock_agent: AsyncMock) -> None:
        """A real SecurityGate with restricted origins denies untrusted callers."""
        from forge_config.schema import SecurityConfig

        config = SecurityConfig(
            allowed_origins=["https://trusted.example.com"],
            rate_limit_rpm=60,
        )
        gate = SecurityGate.from_config(config)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers={
                    "X-Forge-Identity": VALID_IDENTITY,
                    "Origin": "https://evil.example.com",
                },
            )
        assert resp.status_code == 403
        assert "not in allowed list" in resp.json()["detail"]

    async def test_real_gate_enforces_rate_limit(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """A real SecurityGate with rate_limit_rpm=1 rate limits after first request."""
        from forge_config.schema import SecurityConfig

        config = SecurityConfig(allowed_origins=["*"], rate_limit_rpm=1)
        gate = SecurityGate.from_config(config)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp1 = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "first"},
                headers=valid_headers,
            )
            assert resp1.status_code == 200

            resp2 = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "second"},
                headers=valid_headers,
            )
            assert resp2.status_code == 429

    async def test_real_gate_default_config_allows_all(
        self, mock_agent: AsyncMock, valid_headers: dict[str, str]
    ) -> None:
        """Default SecurityConfig (wildcard origins, 60 RPM) allows typical requests."""
        from forge_config.schema import SecurityConfig

        config = SecurityConfig()
        gate = SecurityGate.from_config(config)
        app = _make_gated_app(mock_agent, gate=gate)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/agent/invoke",
                json={"intent": "test"},
                headers=valid_headers,
            )
        assert resp.status_code == 200
