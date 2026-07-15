"""Hot-reload file watcher with debounce."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _DebouncedHandler(FileSystemEventHandler):
    """Watchdog handler that debounces rapid file changes.

    Fires the callback on ``on_modified`` AND on ``on_created`` /
    ``on_moved``: an atomic write (``tempfile.mkstemp`` -> ``os.replace``,
    as used by ``writable_store`` for the config overlay and by the user
    token store) does NOT emit an ``on_modified`` whose ``src_path`` is the
    target -- on Linux inotify it emits an ``on_moved`` (``dest_path`` ==
    target) and/or an ``on_created`` -- so a handler that only watched
    ``on_modified`` never observed an overlay write.
    """

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

    def _schedule_if_target(self, path_str: str) -> None:
        """Debounce+fire the callback when *path_str* resolves to the watched
        target; ignore any other path in the parent directory.

        With no running event loop (e.g. a synchronous test dispatch) the
        callback is invoked directly; otherwise it is scheduled via
        ``loop.call_later`` and coalesced with any pending timer.
        """
        if Path(path_str).resolve() != self._target:
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

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._schedule_if_target(str(event.src_path))

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._schedule_if_target(str(event.src_path))

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        # An atomic replace surfaces as a move whose DEST is the target
        # (the temp file is the src); fall back to src_path defensively.
        dest = getattr(event, "dest_path", "") or event.src_path
        self._schedule_if_target(str(dest))


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
