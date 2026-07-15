"""Tests for forge_config.writable_store: atomic-write primitives, the
OverlayStore (overlay.yaml + state.json + history/ + hash-chained audit
journal). Crash-safety tests mirror
forge_security.oidc.user_tokens.FileUserTokenStore's existing test suite.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml
from forge_config.writable_store import (
    AuditChainError,
    OverlayConflictError,
    OverlayState,
    OverlayStore,
    write_atomic_bytes,
    write_atomic_json,
    write_atomic_yaml,
)

# --- Atomic write primitive: crash-safety (mirrors user_tokens tests) ---


class TestWriteAtomicBytes:
    async def test_writes_file_with_expected_content_and_mode(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        await write_atomic_bytes(target, b"hello world")
        assert target.read_bytes() == b"hello world"
        assert (os.stat(target).st_mode & 0o777) == 0o600

    async def test_no_leftover_temp_files_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        await write_atomic_bytes(target, b"data")
        leftovers = [p for p in tmp_path.iterdir() if p.name != "out.bin"]
        assert leftovers == []

    async def test_temp_file_cleaned_up_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"

        def _boom(_src: str, _dst: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            await write_atomic_bytes(target, b"data")

        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    async def test_never_leaves_a_torn_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed write must never leave a partially-written target file --
        either the previous complete content, or nothing at all."""
        target = tmp_path / "out.bin"
        await write_atomic_bytes(target, b"version-1")

        def _boom(_src: str, _dst: str) -> None:
            raise OSError("simulated crash mid-replace")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            await write_atomic_bytes(target, b"version-2-never-lands")

        assert target.read_bytes() == b"version-1"

    async def test_write_atomic_yaml_round_trips(self, tmp_path: Path) -> None:
        target = tmp_path / "overlay.yaml"
        await write_atomic_yaml(target, {"tools": {"manual_tools": []}})
        assert yaml.safe_load(target.read_text()) == {"tools": {"manual_tools": []}}

    async def test_write_atomic_json_round_trips(self, tmp_path: Path) -> None:
        target = tmp_path / "state.json"
        await write_atomic_json(target, {"rev": 3})
        assert json.loads(target.read_text()) == {"rev": 3}


# --- OverlayStore: overlay.yaml + state.json + history ---


@pytest.fixture
def store(tmp_path: Path) -> OverlayStore:
    return OverlayStore(overlay_path=tmp_path / "overlay" / "forge.overlay.yaml")


class TestOverlayStoreWrite:
    async def test_first_write_creates_rev_1(self, store: OverlayStore) -> None:
        state = await store.write_overlay(
            {"tools": {"manual_tools": []}}, base_rev="h0", updated_by="alice@example.com"
        )
        assert state.rev == 1
        assert state.base_rev == "h0"
        assert state.updated_by == "alice@example.com"

    async def test_second_write_bumps_rev(self, store: OverlayStore) -> None:
        await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="alice")
        state2 = await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="bob")
        assert state2.rev == 2

    async def test_overlay_file_is_readable_after_write(self, store: OverlayStore) -> None:
        await store.write_overlay(
            {"tools": {"manual_tools": [{"name": "x"}]}}, base_rev="h0", updated_by="alice"
        )
        on_disk = store.read_overlay()
        assert on_disk["tools"]["manual_tools"] == [{"name": "x"}]
        assert on_disk["_rev"] == 1
        assert on_disk["_base_rev"] == "h0"

    async def test_state_file_matches_returned_state(self, store: OverlayStore) -> None:
        await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="alice")
        persisted = store.read_state()
        assert persisted is not None
        assert persisted.rev == 1
        assert persisted.base_rev == "h0"

    async def test_history_snapshot_created_on_second_write(self, store: OverlayStore) -> None:
        await store.write_overlay(
            {"llm": {"default_model": "a"}}, base_rev="h0", updated_by="alice"
        )
        await store.write_overlay(
            {"llm": {"default_model": "b"}}, base_rev="h0", updated_by="alice"
        )
        snapshots = list(store.history_dir.glob("overlay-*.yaml"))
        assert len(snapshots) == 1

    async def test_history_retains_last_n_only(self, tmp_path: Path) -> None:
        store = OverlayStore(overlay_path=tmp_path / "overlay.yaml", history_retain=3)
        for i in range(6):
            await store.write_overlay(
                {"llm": {"default_model": str(i)}}, base_rev="h0", updated_by="a"
            )
        snapshots = list(store.history_dir.glob("overlay-*.yaml"))
        assert len(snapshots) == 3

    async def test_optimistic_concurrency_conflict(self, store: OverlayStore) -> None:
        await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="alice")
        with pytest.raises(OverlayConflictError) as exc_info:
            await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="bob", expected_rev=0)
        assert exc_info.value.current_rev == 1

    async def test_matching_expected_rev_succeeds(self, store: OverlayStore) -> None:
        s1 = await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="alice")
        s2 = await store.write_overlay(
            {"llm": {}}, base_rev="h0", updated_by="alice", expected_rev=s1.rev
        )
        assert s2.rev == 2

    async def test_unwritable_directory_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = OverlayStore(overlay_path=tmp_path / "overlay" / "forge.overlay.yaml")

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            await store.write_overlay({"llm": {}}, base_rev="h0", updated_by="alice")

    async def test_concurrent_writes_are_serialized(self, store: OverlayStore) -> None:
        import asyncio

        async def _write(n: int) -> OverlayState:
            return await store.write_overlay(
                {"llm": {"default_model": str(n)}}, base_rev="h0", updated_by="alice"
            )

        results = await asyncio.gather(*[_write(i) for i in range(10)])
        revs = sorted(r.rev for r in results)
        assert revs == list(range(1, 11))
        final = store.read_state()
        assert final is not None
        assert final.rev == 10

    async def test_read_state_reconstructs_from_overlay_when_state_json_missing(
        self, store: OverlayStore
    ) -> None:
        """Finding [MEDIUM] crash-consistency: overlay.yaml and state.json are
        two separate atomic writes. A crash BETWEEN them leaves the edit
        durable but no state.json -- read_state must reconstruct rev/base_rev
        from the overlay's own provenance stamps instead of silently reporting
        rev=0 (which would discard the durable change and mask drift)."""
        await store.write_overlay(
            {"tools": {"manual_tools": [{"name": "x"}]}},
            base_rev="basehash",
            updated_by="alice@example.com",
        )
        # Simulate the crash: the overlay is on disk, state.json never landed.
        store.state_path.unlink()
        assert not store.state_path.exists()
        assert store.overlay_path.exists()

        recovered = store.read_state()
        assert recovered is not None
        assert recovered.rev == 1
        assert recovered.base_rev == "basehash"
        assert recovered.updated_by == "alice@example.com"

    async def test_read_state_is_none_when_no_overlay_and_no_state(
        self, store: OverlayStore
    ) -> None:
        """A truly empty store (no overlay, no state) still reads back None."""
        assert store.read_state() is None


# --- Audit journal: hash-chained, fsync-appended ---


@pytest.fixture
def audit_store(tmp_path: Path) -> OverlayStore:
    return OverlayStore(
        overlay_path=tmp_path / "data" / "overlay" / "forge.overlay.yaml",
        audit_path=tmp_path / "data" / "audit" / "config-audit.jsonl",
    )


def _entry_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "actor_sub": "user:alice",
        "actor_email": "alice@example.com",
        "permission_used": "config:write",
        "section": "tools",
        "op": "update",
        "outcome": "applied",
        "rev": 1,
        "base_rev": "h0",
        "diff": {},
    }
    fields.update(overrides)
    return fields


class TestAuditJournal:
    async def test_first_entry_chains_to_genesis(self, audit_store: OverlayStore) -> None:
        entry = await audit_store.append_audit_locked(_entry_fields())
        assert entry.prev_entry_sha256 == "0" * 64
        assert entry.seq == 1

    async def test_second_entry_chains_to_first(self, audit_store: OverlayStore) -> None:
        e1 = await audit_store.append_audit_locked(_entry_fields())
        e2 = await audit_store.append_audit_locked(_entry_fields(op="delete"))
        assert e2.seq == 2
        assert e2.prev_entry_sha256 == hashlib.sha256(e1.canonical_body().encode()).hexdigest()

    async def test_journal_is_fsync_appended_and_persisted(self, audit_store: OverlayStore) -> None:
        await audit_store.append_audit_locked(_entry_fields())
        await audit_store.append_audit_locked(_entry_fields(op="delete"))
        lines = audit_store.audit_path.read_text().strip().splitlines()
        assert len(lines) == 2

    async def test_verify_audit_chain_passes_on_untampered_journal(
        self, audit_store: OverlayStore
    ) -> None:
        await audit_store.append_audit_locked(_entry_fields())
        await audit_store.append_audit_locked(_entry_fields(op="delete"))
        await audit_store.append_audit_locked(_entry_fields(op="create"))
        audit_store.verify_audit_chain()  # must not raise

    async def test_verify_audit_chain_detects_edited_line(self, audit_store: OverlayStore) -> None:
        await audit_store.append_audit_locked(_entry_fields())
        await audit_store.append_audit_locked(_entry_fields(op="delete"))

        lines = audit_store.audit_path.read_text().splitlines()
        tampered = json.loads(lines[0])
        tampered["op"] = "TAMPERED"
        lines[0] = json.dumps(tampered)
        audit_store.audit_path.write_text("\n".join(lines) + "\n")

        with pytest.raises(AuditChainError):
            audit_store.verify_audit_chain()

    async def test_verify_audit_chain_detects_truncated_line(
        self, audit_store: OverlayStore
    ) -> None:
        await audit_store.append_audit_locked(_entry_fields())
        await audit_store.append_audit_locked(_entry_fields(op="delete"))
        await audit_store.append_audit_locked(_entry_fields(op="create"))

        lines = audit_store.audit_path.read_text().splitlines()
        del lines[1]  # drop the middle line
        audit_store.audit_path.write_text("\n".join(lines) + "\n")

        with pytest.raises(AuditChainError):
            audit_store.verify_audit_chain()

    async def test_denials_are_also_recorded(self, audit_store: OverlayStore) -> None:
        entry = await audit_store.append_audit_locked(
            _entry_fields(outcome="denied", reason="escalation_denied")
        )
        assert entry.outcome == "denied"
        assert entry.reason == "escalation_denied"

    async def test_rotation_carries_the_chain_forward(self, audit_store: OverlayStore) -> None:
        await audit_store.append_audit_locked(_entry_fields())
        await audit_store.append_audit_locked(_entry_fields(op="delete"))

        archive = await audit_store.rotate_audit()
        assert archive is not None
        assert archive.exists()
        assert not audit_store.audit_path.exists()

        entry = await audit_store.append_audit_locked(_entry_fields(op="create"))
        assert entry.seq == 3
        assert entry.prev_entry_sha256 != "0" * 64
        audit_store.verify_audit_chain()  # must not raise post-rotation

    async def test_read_audit_entries_returns_all_lines_in_order(
        self, audit_store: OverlayStore
    ) -> None:
        await audit_store.append_audit_locked(_entry_fields(section="tools"))
        await audit_store.append_audit_locked(_entry_fields(section="agents"))
        entries = audit_store.read_audit_entries()
        assert [e.section for e in entries] == ["tools", "agents"]


# --- OverlayStore.transaction(): locked read-validate-write (TOCTOU close) ---


class TestOverlayTransaction:
    async def test_read_validate_write_happens_under_one_lock_acquisition(
        self, store: OverlayStore
    ) -> None:
        async with store.transaction() as txn:
            current = txn.read_overlay()
            assert current == {}
            state = txn.read_state()
            assert state is None
            new_state = await txn.write(
                {"llm": {"default_model": "x"}}, base_rev="h0", updated_by="alice"
            )
            assert new_state.rev == 1
            audit = await txn.append_audit(
                {
                    "actor_sub": "user:alice",
                    "permission_used": "config:write",
                    "section": "llm",
                    "op": "update",
                    "outcome": "applied",
                    "rev": new_state.rev,
                    "base_rev": "h0",
                    "diff": {},
                }
            )
            assert audit.rev == 1

        # Persisted after the transaction closes.
        persisted = store.read_state()
        assert persisted is not None
        assert persisted.rev == 1

    async def test_transaction_serializes_against_concurrent_writers(
        self, store: OverlayStore
    ) -> None:
        """A TOCTOU race would let two concurrent callers both read rev=0
        and both attempt to write rev=1. The lock inside ``transaction()``
        makes this impossible: the second caller's transaction only
        starts after the first fully commits, so it observes rev=1 and
        produces rev=2 -- never a lost update."""
        import asyncio

        results: list[int] = []

        async def _txn_bump() -> None:
            async with store.transaction() as txn:
                state = txn.read_state()
                current_rev = state.rev if state else 0
                # Simulate work between read and write -- if the lock
                # were not held for the whole transaction, this would
                # open a TOCTOU window for another writer to interleave.
                await asyncio.sleep(0)
                new_state = await txn.write(
                    {"llm": {"default_model": str(current_rev)}},
                    base_rev="h0",
                    updated_by="alice",
                    expected_rev=current_rev,
                )
                results.append(new_state.rev)

        await asyncio.gather(*[_txn_bump() for _ in range(5)])
        assert sorted(results) == [1, 2, 3, 4, 5]

    async def test_transaction_writes_nothing_when_caller_never_calls_write(
        self, store: OverlayStore
    ) -> None:
        """If the caller decides NOT to write (e.g. validation of the
        merged whole-config failed) inside the transaction, nothing is
        persisted -- read-only use of a transaction is safe."""
        async with store.transaction() as txn:
            _ = txn.read_overlay()
            # Caller decides the proposed change is invalid and never
            # calls txn.write(...).

        assert store.read_state() is None
