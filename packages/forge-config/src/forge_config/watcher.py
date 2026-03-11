"""Hot-reload file watcher with debounce."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _DebouncedHandler(FileSystemEventHandler):
    """Watchdog handler that debounces rapid file changes."""

    def __init__(
        self,
        target_path: Path,
        callback: Callable[[Path], None],
        debounce_seconds: float = 1.0,
    ) -> None:
        self._target = target_path.resolve()
        self._callback = callback
        self._debounce = debounce_seconds
        self._timer: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        if Path(str(event.src_path)).resolve() != self._target:
            return

        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._callback(self._target)
                return

        if self._timer is not None:
            self._timer.cancel()

        self._timer = self._loop.call_later(
            self._debounce,
            self._callback,
            self._target,
        )


class ConfigWatcher:
    """Watches a config file for changes and invokes a callback on modification."""

    def __init__(
        self,
        config_path: str | Path,
        on_change: Callable[[Path], None],
        debounce_seconds: float = 1.0,
    ) -> None:
        self._path = Path(config_path).resolve()
        self._handler = _DebouncedHandler(self._path, on_change, debounce_seconds)
        self._observer = Observer()

    def start(self) -> None:
        """Start watching the config file."""
        logger.info("Watching config file: %s", self._path)
        self._observer.schedule(
            self._handler,
            str(self._path.parent),
            recursive=False,
        )
        self._observer.start()

    def stop(self) -> None:
        """Stop watching."""
        self._observer.stop()
        self._observer.join(timeout=5)
        logger.info("Config watcher stopped")
