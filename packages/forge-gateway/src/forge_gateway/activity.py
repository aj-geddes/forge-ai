"""Process-wide recent tool-activity log for the admin dashboard.

Application metrics (``metrics_registry.record_tool_invocation``) track tool
invocation *counts* only -- Prometheus counters retain no per-call detail.
This module holds an actual bounded log of the most recent invocations
(tool name, arguments, outcome, timestamp) so the admin dashboard can show a
live "recent agent activity" feed. It is recorded at the same call sites as
the metrics counters (``routes/conversational.py``, ``routes/programmatic.py``)
so the two stay consistent.

The buffer is a single process-wide singleton (:data:`recent_activity`)
backed by a ``collections.deque(maxlen=...)``. Deque append/pop operations
are atomic under the GIL, so no additional locking is required for this
single-process gateway.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Bounds the serialized size of a single record's arguments so one huge tool
# call can't blow up the in-memory footprint of the ring buffer.
_MAX_ARGUMENT_BYTES = 4096

# Default number of records retained -- recent enough to be useful on a
# dashboard, small enough to stay cheap in memory.
_DEFAULT_MAXLEN = 200


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """A single recorded tool invocation."""

    tool: str
    arguments: dict[str, Any]
    ok: bool
    error: str | None
    timestamp: datetime
    session_id: str | None
    interface: str


def _json_safe(value: Any) -> Any:
    """Recursively convert *value* into a JSON-serializable structure.

    JSON primitives (``None``/``str``/``int``/``float``/``bool``) pass
    through unchanged; dicts and lists/tuples are walked recursively;
    everything else (custom objects, sets, exceptions, ...) is stringified
    via ``str()`` so the result is always safe to hand to ``json.dumps``.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)


def _bounded_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy of *arguments*, size-capped at
    ``_MAX_ARGUMENT_BYTES`` serialized bytes.

    Oversized argument payloads (e.g. a tool called with a huge blob) are
    replaced with a truncated preview rather than stored in full, keeping
    each ring-buffer entry -- and therefore the whole buffer -- bounded.
    """
    safe_arguments: dict[str, Any] = {
        str(key): _json_safe(value) for key, value in arguments.items()
    }
    serialized = json.dumps(safe_arguments, default=str)
    if len(serialized.encode("utf-8")) <= _MAX_ARGUMENT_BYTES:
        return safe_arguments
    return {"_truncated": True, "preview": serialized[:_MAX_ARGUMENT_BYTES]}


def derive_ok_error(result: Any) -> tuple[bool, str | None]:
    """Best-effort derivation of ``(ok, error)`` from a tool's raw return value.

    Tool results have no standard error envelope, so this recognizes the
    common conventions: the result itself is an exception, or it's a dict
    carrying a truthy ``"error"`` key. Everything else -- including an
    explicit ``{"error": None}`` or ``{"error": ""}`` -- is treated as a
    successful result.
    """
    if isinstance(result, BaseException):
        return False, f"{type(result).__name__}: {result}"
    if isinstance(result, dict):
        error_value = result.get("error")
        if error_value:
            if isinstance(error_value, BaseException):
                return False, f"{type(error_value).__name__}: {error_value}"
            return False, str(error_value)
    return True, None


class RecentActivity:
    """A bounded, newest-first, in-memory log of recent tool invocations."""

    def __init__(self, maxlen: int = _DEFAULT_MAXLEN) -> None:
        self.maxlen = maxlen
        self._records: deque[ActivityRecord] = deque(maxlen=maxlen)

    def record(
        self,
        tool: str,
        arguments: dict[str, Any],
        ok: bool,
        error: str | None,
        interface: str,
        session_id: str | None = None,
    ) -> None:
        """Append a new activity record, evicting the oldest if at capacity."""
        self._records.append(
            ActivityRecord(
                tool=tool,
                arguments=_bounded_arguments(arguments),
                ok=ok,
                error=error,
                timestamp=datetime.now(UTC),
                session_id=session_id,
                interface=interface,
            )
        )

    def snapshot(self, limit: int | None = None) -> list[ActivityRecord]:
        """Return the most recent records, newest first.

        Args:
            limit: If given, cap the returned list to this many records.
        """
        records = list(self._records)
        records.reverse()
        if limit is not None:
            records = records[:limit]
        return records

    def reset(self) -> None:
        """Clear all records. Intended for test isolation."""
        self._records.clear()


# Process-wide singleton -- the seams in routes/conversational.py and
# routes/programmatic.py record into this instance; routes/admin.py reads
# from it.
recent_activity = RecentActivity()
