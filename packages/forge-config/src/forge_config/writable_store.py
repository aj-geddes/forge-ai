"""Durable storage for the config overlay (layered BASE+OVERLAY config).

Owns ``overlay.yaml`` (the whitelisted editable subset), ``state.json``
(monotonic revision + the BASE hash it was written against),
``history/`` snapshots (last N overlay revisions, for revert), and the
hash-chained, fsync-appended config audit journal.

The atomic-write primitive (:func:`write_atomic_bytes`, and the sync
helper it wraps) is a direct, verbatim port of the algorithm in
``forge_security.oidc.user_tokens.FileUserTokenStore._write_atomic``:
``tempfile.mkstemp`` in the *same directory* as the target -> write ->
``flush`` -> ``os.fsync`` -> ``os.chmod(0o600)`` -> ``os.replace`` (atomic
rename on POSIX). A crash or exception mid-write leaves either the
previous complete file or the new complete file -- never a torn one --
and the temp file is always removed on failure.

All mutating :class:`OverlayStore` operations are serialized through a
single ``asyncio.Lock``, mirroring ``FileUserTokenStore``'s single-writer
discipline (and the deployment's single-replica RWO fail-guard).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

#: Hash chained to by the first audit entry ever written (no predecessor).
GENESIS_HASH = "0" * 64

#: Default number of overlay revision snapshots retained in history/.
DEFAULT_HISTORY_RETAIN = 50


# --- Atomic write primitives ---


def _write_atomic_bytes_sync(path: Path, payload: bytes) -> None:
    """Synchronous half of :func:`write_atomic_bytes`. Must be called off
    the event loop (via ``asyncio.to_thread``)."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


async def write_atomic_bytes(path: Path, payload: bytes) -> None:
    """Write *payload* to *path* atomically (mkstemp/fsync/chmod/replace).

    Never leaves a torn file: on any failure the temp file is removed and
    the previous content at *path* (if any) is untouched. Raises
    ``OSError`` on any durable-write failure -- never swallowed here.
    """
    await asyncio.to_thread(_write_atomic_bytes_sync, path, payload)


async def write_atomic_yaml(path: Path, data: dict[str, Any]) -> None:
    payload = yaml.dump(data, default_flow_style=False, sort_keys=False).encode("utf-8")
    await write_atomic_bytes(path, payload)


async def write_atomic_json(path: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, indent=2, sort_keys=True, default=str).encode("utf-8")
    await write_atomic_bytes(path, payload)


# --- Models ---


class OverlayState(BaseModel):
    """The on-disk ``state.json`` shape: the overlay's monotonic revision
    and the BASE hash it was written against."""

    rev: int = 0
    base_rev: str | None = None
    updated_at: datetime
    updated_by: str


class AuditEntry(BaseModel):
    """One hash-chained line of the config audit journal.

    ``prev_entry_sha256`` is the SHA-256 of the *previous* entry's
    :meth:`canonical_body` (or :data:`GENESIS_HASH` for the first entry
    ever written) -- a Merkle-style chain that makes editing or deleting
    any line detectable (:meth:`OverlayStore.verify_audit_chain`).
    """

    seq: int
    ts: datetime
    actor_sub: str
    actor_email: str | None = None
    permission_used: str
    section: str
    op: str
    outcome: str  # "applied" | "denied"
    rev: int | None = None
    base_rev: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    diff: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    prev_entry_sha256: str

    def canonical_body(self) -> str:
        """The canonical (sorted-key, separator-tight) JSON of this entry
        EXCLUDING ``prev_entry_sha256`` itself -- what gets hashed to
        produce the *next* entry's ``prev_entry_sha256`` (including it
        would be circular)."""
        body = self.model_dump(mode="json", exclude={"prev_entry_sha256"})
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_body().encode("utf-8")).hexdigest()


class OverlayConflictError(Exception):
    """Raised by :meth:`OverlayStore.write_overlay` on optimistic-
    concurrency mismatch (``If-Match`` rev != current rev). The gateway
    maps this to ``HTTP 409`` with the current rev."""

    def __init__(self, expected_rev: int, current_rev: int) -> None:
        self.expected_rev = expected_rev
        self.current_rev = current_rev
        super().__init__(f"expected rev {expected_rev}, current rev is {current_rev}")


class AuditChainError(Exception):
    """Raised by :meth:`OverlayStore.verify_audit_chain` when a line's
    ``prev_entry_sha256`` does not match the hash of the entry that
    precedes it -- tampering or truncation of the journal."""


# --- OverlayStore ---


class OverlayTxn:
    """A single locked read-modify-write transaction handle, yielded by
    :meth:`OverlayStore.transaction`. Exposes the read/write/audit
    primitives WITHOUT taking the lock themselves (the enclosing
    ``async with store.transaction()`` block already holds it) so a
    caller can read the current overlay, validate a proposed change
    against it, and persist -- all atomically, with no other writer able
    to interleave.
    """

    def __init__(self, store: OverlayStore) -> None:
        self._store = store

    def read_overlay(self) -> dict[str, Any]:
        return self._store.read_overlay()

    def read_state(self) -> OverlayState | None:
        return self._store.read_state()

    async def write(
        self,
        overlay_dict: dict[str, Any],
        *,
        base_rev: str,
        updated_by: str,
        expected_rev: int | None = None,
    ) -> OverlayState:
        return await self._store._write_overlay_unlocked(  # noqa: SLF001 -- same-module cooperating class
            overlay_dict, base_rev=base_rev, updated_by=updated_by, expected_rev=expected_rev
        )

    async def append_audit(self, entry_fields: dict[str, Any]) -> AuditEntry:
        return await self._store.append_audit(entry_fields)


class OverlayStore:
    """Owns ``overlay.yaml``, ``state.json``, ``history/`` snapshots, and
    the hash-chained audit journal for one Forge deployment's config
    overlay.

    Parameters
    ----------
    overlay_path:
        Path to the overlay YAML document, e.g.
        ``/app/data/overlay/forge.overlay.yaml``.
    state_path:
        Defaults to ``overlay_path.parent / "state.json"``.
    history_dir:
        Defaults to ``overlay_path.parent / "history"``.
    audit_path:
        Defaults to ``overlay_path.parent.parent / "audit" / "config-audit.jsonl"``
        (i.e. a sibling ``audit/`` directory next to ``overlay/``, matching
        ``/app/data/audit/config-audit.jsonl`` in the design doc).
    history_retain:
        Number of overlay revision snapshots to keep in ``history_dir``.
    clock:
        Injectable UTC-``datetime`` clock, for deterministic tests.
    """

    def __init__(
        self,
        overlay_path: str | Path,
        *,
        state_path: str | Path | None = None,
        history_dir: str | Path | None = None,
        audit_path: str | Path | None = None,
        history_retain: int = DEFAULT_HISTORY_RETAIN,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._overlay_path = Path(overlay_path)
        base_dir = self._overlay_path.parent
        self._state_path = Path(state_path) if state_path else base_dir / "state.json"
        self._history_dir = Path(history_dir) if history_dir else base_dir / "history"
        self._audit_path = (
            Path(audit_path) if audit_path else base_dir.parent / "audit" / "config-audit.jsonl"
        )
        self._history_retain = history_retain
        self._clock = clock
        self._lock = asyncio.Lock()

    # --- paths (read-only, for tests/inspection) ---

    @property
    def overlay_path(self) -> Path:
        return self._overlay_path

    @property
    def state_path(self) -> Path:
        return self._state_path

    @property
    def history_dir(self) -> Path:
        return self._history_dir

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    # --- overlay.yaml / state.json ---

    def read_overlay(self) -> dict[str, Any]:
        """Read the current overlay document. Returns ``{}`` (never
        raises) if the file does not exist yet -- an absent overlay is
        the normal "no edits yet" state, not an error."""
        if not self._overlay_path.exists():
            return {}
        raw = self._overlay_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        return data or {}

    def read_state(self) -> OverlayState | None:
        if self._state_path.exists():
            raw = self._state_path.read_text(encoding="utf-8")
            return OverlayState.model_validate_json(raw)
        # Crash-consistency: overlay.yaml and state.json are two separate
        # atomic writes (overlay first). A crash BETWEEN them leaves the
        # edit durable on disk but no state.json -- naively that reads back
        # as rev=0 / drift=false, silently discarding a persisted change.
        # Reconstruct the state from the overlay's own provenance stamps
        # (_rev/_base_rev/_updated_by/_updated_at) instead, so the durable
        # edit is honestly reflected until the next write re-materializes
        # state.json.
        if self._overlay_path.exists():
            overlay = self.read_overlay()
            rev = overlay.get("_rev")
            if isinstance(rev, int):
                updated_at_raw = overlay.get("_updated_at")
                updated_at = self._clock()
                if isinstance(updated_at_raw, str):
                    try:
                        updated_at = datetime.fromisoformat(updated_at_raw)
                    except ValueError:
                        updated_at = self._clock()
                base_rev = overlay.get("_base_rev")
                updated_by = overlay.get("_updated_by")
                return OverlayState(
                    rev=rev,
                    base_rev=base_rev if isinstance(base_rev, str) else None,
                    updated_at=updated_at,
                    updated_by=updated_by if isinstance(updated_by, str) else "unknown",
                )
        return None

    async def write_overlay(
        self,
        overlay_dict: dict[str, Any],
        *,
        base_rev: str,
        updated_by: str,
        expected_rev: int | None = None,
    ) -> OverlayState:
        """Atomically persist *overlay_dict* as the new overlay content.

        Takes the write lock itself -- see :meth:`transaction` for a
        version usable from inside a larger locked read-validate-write
        sequence (closing the TOCTOU window between reading the current
        overlay and writing a new one derived from it).

        Raises
        ------
        OverlayConflictError
            If *expected_rev* is given and does not match the current rev.
        OSError
            On any durable-write failure -- never swallowed; the caller
            (forge-gateway) maps this to ``HTTP 507``.
        """
        async with self._lock:
            return await self._write_overlay_unlocked(
                overlay_dict, base_rev=base_rev, updated_by=updated_by, expected_rev=expected_rev
            )

    async def _write_overlay_unlocked(
        self,
        overlay_dict: dict[str, Any],
        *,
        base_rev: str,
        updated_by: str,
        expected_rev: int | None = None,
    ) -> OverlayState:
        """The body of :meth:`write_overlay` -- checks optimistic
        concurrency, snapshots the OUTGOING overlay content into
        ``history_dir`` (best-effort continuity for revert), prunes
        snapshots beyond ``history_retain``, writes the new
        ``overlay.yaml`` (stamped with ``_rev``/``_base_rev``/
        ``_updated_by``/``_updated_at``), then writes the new
        ``state.json``. Both writes use the atomic
        mkstemp/fsync/chmod/replace primitive. Caller MUST already hold
        ``self._lock``.
        """
        current_state = self.read_state()
        current_rev = current_state.rev if current_state else 0
        if expected_rev is not None and expected_rev != current_rev:
            raise OverlayConflictError(expected_rev, current_rev)

        now = self._clock()

        if self._overlay_path.exists():
            ts = now.strftime("%Y%m%dT%H%M%S%f")
            snapshot_path = self._history_dir / f"overlay-{current_rev}-{ts}.yaml"
            await write_atomic_bytes(snapshot_path, self._overlay_path.read_bytes())
            await self._prune_history()

        new_rev = current_rev + 1
        stamped = dict(overlay_dict)
        stamped["_rev"] = new_rev
        stamped["_base_rev"] = base_rev
        stamped["_updated_by"] = updated_by
        stamped["_updated_at"] = now.isoformat()

        await write_atomic_yaml(self._overlay_path, stamped)

        new_state = OverlayState(
            rev=new_rev, base_rev=base_rev, updated_at=now, updated_by=updated_by
        )
        await write_atomic_json(self._state_path, new_state.model_dump(mode="json"))

        return new_state

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[OverlayTxn]:
        """One locked read-validate-write transaction.

        ::

            async with store.transaction() as txn:
                current = txn.read_overlay()
                # ... restore secrets, merge, validate the WHOLE
                # ForgeConfig against BASE + the proposed overlay ...
                state = await txn.write(new_overlay, base_rev=..., updated_by=...)
                await txn.append_audit({...})

        Everything inside the ``async with`` block runs under the same
        lock acquisition, so no other writer can observe (or race) the
        overlay between the read and the write -- this is what closes
        the ``_restore_secrets`` TOCTOU window in forge-gateway's
        ``apply_overlay_mutation``.
        """
        async with self._lock:
            yield OverlayTxn(self)

    async def _prune_history(self) -> None:
        if not self._history_dir.exists():
            return
        snapshots = sorted(self._history_dir.glob("overlay-*.yaml"))
        excess = len(snapshots) - self._history_retain
        if excess <= 0:
            return
        for stale in snapshots[:excess]:
            with contextlib.suppress(OSError):
                stale.unlink()

    def list_history(self) -> list[Path]:
        """Overlay snapshots, oldest first."""
        if not self._history_dir.exists():
            return []
        return sorted(self._history_dir.glob("overlay-*.yaml"))

    # --- Audit journal ---

    def _chain_carry_path(self) -> Path:
        return self._audit_path.parent / ".audit-chain-carry.json"

    def _read_chain_carry(self) -> dict[str, Any] | None:
        p = self._chain_carry_path()
        if not p.exists():
            return None
        loaded: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return loaded

    def _last_entry(self) -> AuditEntry | None:
        """The last entry in the CURRENT (post-rotation) journal file,
        reconstructed fresh from disk every call -- no in-memory chain
        state is kept, so this is correct across process restarts."""
        if not self._audit_path.exists():
            return None
        last_line: str | None = None
        with self._audit_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if stripped:
                    last_line = stripped
        if last_line is None:
            return None
        return AuditEntry.model_validate_json(last_line)

    def _tail_hash_and_seq(self) -> tuple[str, int]:
        last = self._last_entry()
        if last is not None:
            return last.content_hash(), last.seq
        carry = self._read_chain_carry()
        if carry is not None:
            return carry["last_hash"], carry["last_seq"]
        return GENESIS_HASH, 0

    async def append_audit_locked(self, entry_fields: dict[str, Any]) -> AuditEntry:
        """Append one hash-chained line to the audit journal, taking the
        store's write lock itself. Use :meth:`append_audit` instead when
        called from inside a larger operation that already holds the
        lock (e.g. :func:`apply_overlay_mutation` in forge-gateway)."""
        async with self._lock:
            return await self.append_audit(entry_fields)

    async def append_audit(self, entry_fields: dict[str, Any]) -> AuditEntry:
        """Append one hash-chained line. Caller must already hold
        ``self._lock`` (or accept the -- normally harmless, since each
        append recomputes the tail from disk -- race of an unlocked
        call)."""
        prev_hash, prev_seq = self._tail_hash_and_seq()
        fields = dict(entry_fields)
        fields.setdefault("ts", self._clock())
        entry = AuditEntry(seq=prev_seq + 1, prev_entry_sha256=prev_hash, **fields)
        line = entry.model_dump_json() + "\n"
        await asyncio.to_thread(self._append_line, line)
        return entry

    def _append_line(self, line: str) -> None:
        directory = self._audit_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._audit_path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_audit_entries(self) -> list[AuditEntry]:
        """All entries in the CURRENT (post-rotation) journal file, in
        order. Does not include archived (pre-rotation) files."""
        if not self._audit_path.exists():
            return []
        entries: list[AuditEntry] = []
        with self._audit_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if stripped:
                    entries.append(AuditEntry.model_validate_json(stripped))
        return entries

    def verify_audit_chain(self) -> None:
        """Verify every line in the current journal file chains correctly.

        The expected ``prev_entry_sha256`` for the first line in the
        current file is the carried-forward tail hash from the last
        rotation (or :data:`GENESIS_HASH` if the journal has never been
        rotated) -- so tampering with a line either before or after a
        rotation boundary is detected.

        Raises
        ------
        AuditChainError
            On the first mismatch found.
        """
        entries = self.read_audit_entries()
        carry = self._read_chain_carry()
        expected_prev = carry["last_hash"] if carry else GENESIS_HASH
        for entry in entries:
            if entry.prev_entry_sha256 != expected_prev:
                msg = (
                    f"audit chain broken at seq={entry.seq}: expected "
                    f"prev_entry_sha256={expected_prev!r}, got "
                    f"{entry.prev_entry_sha256!r}"
                )
                raise AuditChainError(msg)
            expected_prev = entry.content_hash()

    async def rotate_audit(self) -> Path | None:
        """Archive the current journal file and continue the hash chain
        in a fresh file.

        The tail hash/seq of the outgoing file is preserved in a small
        sidecar (``.audit-chain-carry.json``, itself atomically written)
        so the next :meth:`append_audit` call -- even after a process
        restart -- still chains correctly, and :meth:`verify_audit_chain`
        can validate continuity across the rotation boundary.

        Returns the archive path, or ``None`` if there was nothing to
        rotate (no journal file / an empty journal).
        """
        async with self._lock:
            last = self._last_entry()
            if last is None:
                return None

            carry = {"last_hash": last.content_hash(), "last_seq": last.seq}
            await write_atomic_json(self._chain_carry_path(), carry)

            ts = self._clock().strftime("%Y%m%dT%H%M%S%f")
            archive_path = self._audit_path.parent / f"config-audit-{ts}.jsonl"
            os.replace(self._audit_path, archive_path)
            return archive_path
