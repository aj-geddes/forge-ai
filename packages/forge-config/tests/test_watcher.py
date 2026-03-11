"""Tests for config file watcher."""

import time
from pathlib import Path

from forge_config.watcher import ConfigWatcher


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
