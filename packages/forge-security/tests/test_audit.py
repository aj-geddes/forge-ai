"""Tests for forge_security.audit."""

from forge_security.audit import AuditAction, AuditLogger, ToolCallEvent


class TestToolCallEvent:
    def test_defaults(self):
        evt = ToolCallEvent(caller_id="agent-1", tool_name="search")
        assert evt.action == AuditAction.TOOL_CALL
        assert evt.allowed is True
        assert evt.reason == ""
        assert evt.event_id  # non-empty UUID
        assert evt.timestamp > 0

    def test_custom_fields(self):
        evt = ToolCallEvent(
            caller_id="agent-2",
            tool_name="delete",
            action=AuditAction.AUTH_FAILURE,
            allowed=False,
            reason="not authorised",
            metadata={"ip": "10.0.0.1"},
        )
        assert evt.allowed is False
        assert evt.reason == "not authorised"
        assert evt.metadata["ip"] == "10.0.0.1"

    def test_unique_event_ids(self):
        e1 = ToolCallEvent(caller_id="a", tool_name="t")
        e2 = ToolCallEvent(caller_id="a", tool_name="t")
        assert e1.event_id != e2.event_id


class TestAuditLogger:
    async def test_log_event(self):
        logger = AuditLogger()
        evt = ToolCallEvent(caller_id="test", tool_name="ping")
        # Should not raise
        await logger.log_event(evt)

    async def test_log_tool_call_returns_event(self):
        logger = AuditLogger()
        evt = await logger.log_tool_call(
            caller_id="caller",
            tool_name="search",
            allowed=True,
            reason="ok",
        )
        assert isinstance(evt, ToolCallEvent)
        assert evt.caller_id == "caller"
        assert evt.tool_name == "search"
        assert evt.allowed is True

    async def test_log_tool_call_denied(self):
        logger = AuditLogger()
        evt = await logger.log_tool_call(
            caller_id="bad-agent",
            tool_name="drop_table",
            allowed=False,
            reason="policy denied",
        )
        assert evt.allowed is False
        assert evt.reason == "policy denied"

    async def test_log_tool_call_extra_metadata(self):
        logger = AuditLogger()
        evt = await logger.log_tool_call(
            caller_id="agent",
            tool_name="tool",
            custom_field="hello",
        )
        assert evt.metadata["custom_field"] == "hello"
