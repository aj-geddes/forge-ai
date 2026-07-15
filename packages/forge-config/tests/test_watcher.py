"""Tests for config file watcher."""

import os
import tempfile
import time
from pathlib import Path

from forge_config.watcher import ConfigWatcher, _DebouncedHandler
from watchdog.events import FileCreatedEvent, FileMovedEvent


class TestConfigWatcher:
    def test_detects_file_change(self, tmp_path: Path) -> None:
        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: original\n")

        changes: list[Path] = []

        def on_change(path: Path) -> None:
            changes.append(path)

        watcher = ConfigWatcher(config_file, on_change, debounce_seconds=0.1)
        watcher.start()

        try:
            time.sleep(0.2)
            config_file.write_text("metadata:\n  name: updated\n")
            time.sleep(1.5)  # Wait for debounce + detection
        finally:
            watcher.stop()

        assert len(changes) >= 1
        assert changes[0] == config_file.resolve()

    def test_start_stop(self, tmp_path: Path) -> None:
        config_file = tmp_path / "forge.yaml"
        config_file.write_text("metadata:\n  name: test\n")

        watcher = ConfigWatcher(config_file, lambda p: None)
        watcher.start()
        watcher.stop()


class TestAtomicWriteDetection:
    """The overlay/user-token stores write ATOMICALLY (mkstemp -> fsync ->
    os.replace). On Linux inotify (the production pod) that emits an
    on_moved (dest_path == target) and/or on_created for the target -- NOT
    an on_modified whose src_path is the target -- so a handler that only
    implements on_modified never fires on an overlay write.

    These handler-level tests dispatch the exact events os.replace produces
    and are platform-independent (with no running event loop the handler
    invokes the callback synchronously), unlike a real-Observer test whose
    event types depend on the OS backend (macOS FSEvents also synthesizes a
    modified event, which would mask the bug)."""

    def test_atomic_replace_fires_callback_via_moved_event(self, tmp_path: Path) -> None:
        target = tmp_path / "forge.overlay.yaml"
        target.write_text("metadata:\n  description: original\n")

        changes: list[Path] = []
        handler = _DebouncedHandler(target, changes.append, debounce_seconds=0.05)

        # Simulate writable_store._write_atomic_bytes_sync: a temp file in the
        # same dir replaced onto the target -> a moved event whose dest_path
        # is the target (src_path is the now-consumed temp file).
        handler.dispatch(FileMovedEvent(str(tmp_path / ".forge.overlay.yaml-abc.tmp"), str(target)))

        assert changes == [target.resolve()]

    def test_first_overlay_write_fires_callback_via_created_event(self, tmp_path: Path) -> None:
        target = tmp_path / "forge.overlay.yaml"

        changes: list[Path] = []
        handler = _DebouncedHandler(target, changes.append, debounce_seconds=0.05)

        # A first-ever overlay write creates the file where none existed.
        target.write_text("metadata:\n  description: first\n")
        handler.dispatch(FileCreatedEvent(str(target)))

        assert changes == [target.resolve()]

    def test_moved_event_to_other_path_does_not_fire(self, tmp_path: Path) -> None:
        target = tmp_path / "forge.overlay.yaml"
        target.write_text("x: 1\n")

        changes: list[Path] = []
        handler = _DebouncedHandler(target, changes.append, debounce_seconds=0.05)

        other = tmp_path / "unrelated.yaml"
        handler.dispatch(FileMovedEvent(str(tmp_path / ".t.tmp"), str(other)))

        assert changes == []


class TestParentDirCreatedBeforeStart:
    """A ConfigWatcher constructed for a file whose parent directory was
    only just created (as at pod startup, where /app/data/overlay does not
    exist yet) must start cleanly and observe the subsequent first write."""

    def test_start_then_first_write_fires_callback(self, tmp_path: Path) -> None:
        overlay_dir = tmp_path / "overlay"
        target = overlay_dir / "forge.overlay.yaml"

        changes: list[Path] = []

        # Parent dir did not exist; create it just before start (mirrors the
        # gateway lifespan's mkdir-before-start for the overlay watcher).
        overlay_dir.mkdir(parents=True, exist_ok=True)
        watcher = ConfigWatcher(target, changes.append, debounce_seconds=0.1)
        watcher.start()

        try:
            time.sleep(0.3)
            # Atomic first write into the freshly-created directory.
            fd, tmp_name = tempfile.mkstemp(
                prefix=".forge.overlay.yaml-", suffix=".tmp", dir=str(overlay_dir)
            )
            with os.fdopen(fd, "wb") as f:
                f.write(b"metadata:\n  description: first\n")
            os.replace(tmp_name, target)
            time.sleep(1.5)
        finally:
            watcher.stop()

        assert len(changes) >= 1
        assert changes[0] == target.resolve()
