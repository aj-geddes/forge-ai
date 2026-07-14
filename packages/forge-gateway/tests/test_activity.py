"""Tests for the in-memory recent tool-activity ring buffer.

``forge_gateway.activity`` holds the last N tool-invocation records with
full detail (arguments, ok/error, timestamps) for the admin dashboard's
"recent agent activity" feed -- a companion to the Prometheus counters in
``metrics_registry`` which retain no per-call detail.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from forge_gateway.activity import RecentActivity, derive_ok_error


@pytest.fixture()
def activity() -> RecentActivity:
    """A fresh, isolated RecentActivity instance per test (not the module
    singleton) so tests never interfere with each other."""
    return RecentActivity(maxlen=5)


class TestRecordAndSnapshot:
    """Basic record()/snapshot() behavior."""

    def test_snapshot_empty_when_nothing_recorded(self, activity: RecentActivity) -> None:
        assert activity.snapshot() == []

    def test_record_adds_one_entry(self, activity: RecentActivity) -> None:
        activity.record(
            tool="get_weather",
            arguments={"city": "SF"},
            ok=True,
            error=None,
            interface="chat",
            session_id="sess-1",
        )
        snap = activity.snapshot()
        assert len(snap) == 1
        entry = snap[0]
        assert entry.tool == "get_weather"
        assert entry.arguments == {"city": "SF"}
        assert entry.ok is True
        assert entry.error is None
        assert entry.interface == "chat"
        assert entry.session_id == "sess-1"

    def test_record_sets_utc_timestamp(self, activity: RecentActivity) -> None:
        before = datetime.now(UTC)
        activity.record(
            tool="t", arguments={}, ok=True, error=None, interface="chat", session_id=None
        )
        after = datetime.now(UTC)
        entry = activity.snapshot()[0]
        assert entry.timestamp.tzinfo is not None
        assert before <= entry.timestamp <= after

    def test_session_id_defaults_to_none(self, activity: RecentActivity) -> None:
        activity.record(tool="t", arguments={}, ok=True, error=None, interface="invoke")
        assert activity.snapshot()[0].session_id is None

    def test_error_records_capture_message(self, activity: RecentActivity) -> None:
        activity.record(
            tool="failing_tool",
            arguments={},
            ok=False,
            error="boom",
            interface="chat",
            session_id="s",
        )
        entry = activity.snapshot()[0]
        assert entry.ok is False
        assert entry.error == "boom"


class TestSnapshotOrdering:
    """snapshot() returns newest-first."""

    def test_snapshot_is_newest_first(self, activity: RecentActivity) -> None:
        activity.record(tool="first", arguments={}, ok=True, error=None, interface="chat")
        activity.record(tool="second", arguments={}, ok=True, error=None, interface="chat")
        activity.record(tool="third", arguments={}, ok=True, error=None, interface="chat")

        snap = activity.snapshot()
        assert [r.tool for r in snap] == ["third", "second", "first"]

    def test_snapshot_respects_limit(self, activity: RecentActivity) -> None:
        for i in range(5):
            activity.record(tool=f"tool-{i}", arguments={}, ok=True, error=None, interface="chat")

        snap = activity.snapshot(limit=2)
        assert [r.tool for r in snap] == ["tool-4", "tool-3"]

    def test_snapshot_limit_larger_than_buffer_returns_all(self, activity: RecentActivity) -> None:
        activity.record(tool="only", arguments={}, ok=True, error=None, interface="chat")
        snap = activity.snapshot(limit=100)
        assert len(snap) == 1


class TestMaxlenEviction:
    """The buffer is bounded -- oldest entries are evicted once full."""

    def test_oldest_entries_evicted_beyond_maxlen(self, activity: RecentActivity) -> None:
        # activity fixture has maxlen=5
        for i in range(8):
            activity.record(tool=f"tool-{i}", arguments={}, ok=True, error=None, interface="chat")

        snap = activity.snapshot(limit=100)
        assert len(snap) == 5
        # newest-first: tool-7 down to tool-3; tool-0..2 evicted
        assert [r.tool for r in snap] == ["tool-7", "tool-6", "tool-5", "tool-4", "tool-3"]

    def test_default_maxlen_is_200(self) -> None:
        default_activity = RecentActivity()
        assert default_activity.maxlen == 200
        for i in range(250):
            default_activity.record(
                tool=f"tool-{i}", arguments={}, ok=True, error=None, interface="chat"
            )
        assert len(default_activity.snapshot(limit=1000)) == 200


class TestArgumentsJsonSafety:
    """Arguments are always JSON-serializable and size-bounded."""

    def test_plain_dict_passes_through(self, activity: RecentActivity) -> None:
        activity.record(
            tool="t", arguments={"a": 1, "b": "x"}, ok=True, error=None, interface="chat"
        )
        assert activity.snapshot()[0].arguments == {"a": 1, "b": "x"}

    def test_non_json_safe_value_is_stringified(self, activity: RecentActivity) -> None:
        class Weird:
            def __str__(self) -> str:
                return "weird-repr"

        activity.record(tool="t", arguments={"obj": Weird()}, ok=True, error=None, interface="chat")
        entry_args = activity.snapshot()[0].arguments
        assert entry_args["obj"] == "weird-repr"

    def test_nested_non_json_safe_value_is_stringified(self, activity: RecentActivity) -> None:
        class Weird:
            def __str__(self) -> str:
                return "nested-weird"

        activity.record(
            tool="t",
            arguments={"outer": {"inner": [Weird()]}},
            ok=True,
            error=None,
            interface="chat",
        )
        entry_args = activity.snapshot()[0].arguments
        assert entry_args["outer"]["inner"] == ["nested-weird"]

    def test_huge_arguments_are_truncated(self, activity: RecentActivity) -> None:
        huge = {"payload": "x" * 100_000}
        activity.record(tool="t", arguments=huge, ok=True, error=None, interface="chat")
        entry_args = activity.snapshot()[0].arguments
        serialized = json.dumps(entry_args)
        assert len(serialized) < 100_000
        assert entry_args.get("_truncated") is True

    def test_result_is_always_json_dumpable(self, activity: RecentActivity) -> None:
        """Whatever gets stored must survive json.dumps -- this is the
        binding correctness contract for the admin API response."""

        class Unserializable:
            pass

        activity.record(
            tool="t",
            arguments={"a": Unserializable(), "b": {1, 2, 3}},
            ok=True,
            error=None,
            interface="chat",
        )
        entry_args = activity.snapshot()[0].arguments
        json.dumps(entry_args)  # must not raise


class TestReset:
    """reset() clears the buffer -- used by tests to isolate state."""

    def test_reset_clears_all_records(self, activity: RecentActivity) -> None:
        activity.record(tool="t", arguments={}, ok=True, error=None, interface="chat")
        assert len(activity.snapshot()) == 1
        activity.reset()
        assert activity.snapshot() == []


class TestDeriveOkError:
    """derive_ok_error() converts a tool's raw return value into (ok, error)."""

    def test_none_result_is_ok(self) -> None:
        assert derive_ok_error(None) == (True, None)

    def test_plain_string_result_is_ok(self) -> None:
        assert derive_ok_error("sunny in SF") == (True, None)

    def test_plain_dict_result_is_ok(self) -> None:
        assert derive_ok_error({"temp": 72}) == (True, None)

    def test_exception_result_is_error(self) -> None:
        exc = RuntimeError("tool blew up")
        ok, error = derive_ok_error(exc)
        assert ok is False
        assert error == "RuntimeError: tool blew up"

    def test_dict_with_truthy_error_key_is_error(self) -> None:
        ok, error = derive_ok_error({"error": "not found"})
        assert ok is False
        assert error == "not found"

    def test_dict_with_exception_error_value_is_stringified(self) -> None:
        ok, error = derive_ok_error({"error": ValueError("bad input")})
        assert ok is False
        assert error == "ValueError: bad input"

    def test_dict_with_falsy_error_key_is_ok(self) -> None:
        """An explicit ``error: null`` (or empty string) should not be
        treated as a failure -- only a truthy error value counts."""
        ok, error = derive_ok_error({"error": None, "temp": 72})
        assert ok is True
        assert error is None

    def test_dict_with_missing_error_key_is_ok(self) -> None:
        ok, error = derive_ok_error({"status": "ok"})
        assert ok is True
        assert error is None
