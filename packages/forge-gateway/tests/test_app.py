"""Tests for the FastAPI app factory and ConfigWatcher lifespan integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from forge_gateway.app import (
    _make_reload_callback,
    _rebuild_tool_surface,
    _schedule_tool_rebuild,
    create_app,
    lifespan,
)
from forge_gateway.routes import health

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_health_state() -> Iterator[None]:
    """Ensure health state is reset after each test."""
    yield
    health.set_ready(False)
    health.set_started(False)


@pytest.fixture()
def mock_config() -> MagicMock:
    """A minimal mock ForgeConfig."""
    config = MagicMock()
    config.metadata.name = "test-forge"
    config.security.api_keys = None
    return config


@pytest.fixture()
def mock_watcher() -> MagicMock:
    """A mock ConfigWatcher instance."""
    watcher = MagicMock()
    watcher.start = MagicMock()
    watcher.stop = MagicMock()
    return watcher


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    """A temporary config file on disk."""
    f = tmp_path / "forge.yaml"
    f.write_text("metadata:\n  name: test\n")
    return f


# ---------------------------------------------------------------------------
# App factory tests
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_app_creation(self) -> None:
        app = create_app()
        assert app.title == "Forge AI Gateway"

    def test_routes_registered(self) -> None:
        app = create_app()
        paths = [r.path for r in app.routes]
        assert "/health/live" in paths
        assert "/health/ready" in paths
        assert "/v1/agent/invoke" in paths
        assert "/v1/chat/completions" in paths
        assert "/a2a/agent-card" in paths
        assert "/metrics" in paths


# ---------------------------------------------------------------------------
# 1. ConfigWatcher is created and started during app lifespan startup
# ---------------------------------------------------------------------------


class TestConfigWatcherStartup:
    async def test_watcher_created_and_started_when_config_exists(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """ConfigWatcher should be created and started when a valid config file exists."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=mock_watcher,
            ) as watcher_cls,
        ):
            async with lifespan(app):
                watcher_cls.assert_called_once()
                mock_watcher.start.assert_called_once()

    async def test_watcher_receives_config_path(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """ConfigWatcher constructor receives the correct config path."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=mock_watcher,
            ) as watcher_cls,
        ):
            async with lifespan(app):
                call_args = watcher_cls.call_args
                # First positional arg is the config path
                assert call_args.args[0] == str(config_file)

    async def test_watcher_receives_callable_on_change(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """ConfigWatcher constructor receives a callable on_change callback."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=mock_watcher,
            ) as watcher_cls,
        ):
            async with lifespan(app):
                call_kwargs = watcher_cls.call_args.kwargs
                assert "on_change" in call_kwargs
                assert callable(call_kwargs["on_change"])


# ---------------------------------------------------------------------------
# 2. ConfigWatcher is stopped during app lifespan shutdown
# ---------------------------------------------------------------------------


class TestConfigWatcherShutdown:
    async def test_watcher_stopped_on_shutdown(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """ConfigWatcher.stop() must be called during shutdown."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=mock_watcher,
            ),
        ):
            async with lifespan(app):
                mock_watcher.stop.assert_not_called()

            # After the context manager exits, stop should have been called
            mock_watcher.stop.assert_called_once()

    async def test_watcher_stopped_even_on_error_during_yield(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """ConfigWatcher.stop() is called even if the app raises during operation."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=mock_watcher,
            ),
        ):
            try:
                async with lifespan(app):
                    raise RuntimeError("Simulated app error")
            except RuntimeError:
                pass

            mock_watcher.stop.assert_called_once()

    async def test_shutdown_tolerates_watcher_stop_failure(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """Shutdown should not raise if ConfigWatcher.stop() fails."""
        mock_watcher.stop.side_effect = RuntimeError("Stop failed")

        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=mock_watcher,
            ),
        ):
            # Should not raise despite watcher.stop() failure
            async with lifespan(app):
                pass

            mock_watcher.stop.assert_called_once()


# ---------------------------------------------------------------------------
# 3. App starts successfully even if ConfigWatcher fails to start
# ---------------------------------------------------------------------------


class TestConfigWatcherGracefulDegradation:
    async def test_app_ready_when_watcher_start_fails(
        self,
        mock_config: MagicMock,
        config_file: Path,
    ) -> None:
        """App should become ready even if ConfigWatcher.start() raises."""
        failing_watcher = MagicMock()
        failing_watcher.start.side_effect = OSError("Permission denied")

        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                return_value=failing_watcher,
            ),
        ):
            async with lifespan(app):
                assert health._ready is True
                assert health._started is True

    async def test_app_ready_when_config_watcher_import_fails(
        self,
        mock_config: MagicMock,
        config_file: Path,
    ) -> None:
        """App should become ready even if ConfigWatcher cannot be imported."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                side_effect=ImportError("No module named 'watchdog'"),
            ),
        ):
            async with lifespan(app):
                assert health._ready is True

    async def test_app_ready_when_watcher_constructor_raises(
        self,
        mock_config: MagicMock,
        config_file: Path,
    ) -> None:
        """App should become ready even if ConfigWatcher() constructor raises."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                side_effect=ValueError("Invalid path"),
            ),
        ):
            async with lifespan(app):
                assert health._ready is True

    async def test_no_watcher_stop_when_start_failed(
        self,
        mock_config: MagicMock,
        config_file: Path,
    ) -> None:
        """If watcher creation fails, stop should not be called on shutdown."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
                side_effect=RuntimeError("Cannot create watcher"),
            ) as watcher_cls,
        ):
            async with lifespan(app):
                pass

            # No instance was created, so stop should never be called
            # (The constructor raised, so no object exists to call stop on.)
            watcher_cls.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Config reload callback updates app state correctly
# ---------------------------------------------------------------------------


class TestConfigReloadCallback:
    async def test_reload_callback_loads_new_config(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The reload callback should load the new config from disk."""
        app = FastAPI(lifespan=lifespan)

        new_config = MagicMock()
        new_config.metadata.name = "updated-forge"
        new_config.security.api_keys = None

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
        ):
            async with lifespan(app):
                assert captured_callback is not None

                # Reset the mock to track the reload call separately
                mock_load.reset_mock()
                mock_load.return_value = new_config

                # Invoke the callback as if the file changed
                captured_callback(config_file)

                mock_load.assert_called_once_with(str(config_file))

    async def test_reload_callback_updates_admin_state(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The reload callback should update admin state with the new config."""
        from forge_gateway.routes import admin as admin_module

        app = FastAPI(lifespan=lifespan)

        new_config = MagicMock()
        new_config.metadata.name = "reloaded"
        new_config.security.api_keys = MagicMock()

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
            patch.object(admin_module, "set_state") as mock_set_state,
            patch("forge_gateway.app.set_api_key_config") as mock_set_keys,
        ):
            async with lifespan(app):
                mock_load.return_value = new_config
                mock_set_state.reset_mock()

                captured_callback(config_file)

                mock_set_state.assert_called_once()
                call_kwargs = mock_set_state.call_args.kwargs
                assert call_kwargs["config"] is new_config
                assert call_kwargs["config_path"] == str(config_file)
                # Agent should NOT be passed (preserved from prior state)
                assert "agent" not in call_kwargs

                mock_set_keys.assert_called_with(new_config.security.api_keys)

    async def test_reload_callback_handles_load_failure(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The reload callback should not raise if config loading fails."""
        from forge_gateway.routes import admin as admin_module

        app = FastAPI(lifespan=lifespan)

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
            patch.object(admin_module, "set_state") as mock_set_state,
        ):
            async with lifespan(app):
                mock_load.side_effect = ValueError("Invalid YAML")
                mock_set_state.reset_mock()

                # Should not raise
                captured_callback(config_file)

                # Admin state should NOT have been updated on failure
                mock_set_state.assert_not_called()

    async def test_reload_callback_updates_api_key_config(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The reload callback should update API key auth configuration."""
        app = FastAPI(lifespan=lifespan)

        new_config = MagicMock()
        new_config.metadata.name = "reloaded"
        new_api_keys = MagicMock()
        new_config.security.api_keys = new_api_keys

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
            patch("forge_gateway.app.set_api_key_config") as mock_set_keys,
        ):
            async with lifespan(app):
                mock_load.return_value = new_config
                mock_set_keys.reset_mock()

                captured_callback(config_file)

                mock_set_keys.assert_called_once_with(new_api_keys)


# ---------------------------------------------------------------------------
# 5. App works normally without a config file (watcher not created)
# ---------------------------------------------------------------------------


class TestNoConfigFile:
    async def test_no_watcher_when_config_load_fails(self) -> None:
        """When config loading raises, watcher should not be created."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": "nonexistent.yaml"}),
            patch(
                "forge_config.ConfigWatcher",
            ) as watcher_cls,
        ):
            async with lifespan(app):
                watcher_cls.assert_not_called()
                assert health._ready is True
                assert health._started is True

    async def test_no_watcher_when_config_is_none(self) -> None:
        """When load_config raises an exception, no watcher is created."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": "missing.yaml"}),
            patch(
                "forge_config.load_config",
                side_effect=FileNotFoundError("No such file"),
            ),
            patch(
                "forge_config.ConfigWatcher",
            ) as watcher_cls,
        ):
            async with lifespan(app):
                watcher_cls.assert_not_called()

    async def test_no_watcher_when_config_path_does_not_exist_on_disk(
        self,
        mock_config: MagicMock,
    ) -> None:
        """When config loads but the file path doesn't exist on disk, skip watcher."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict(
                "os.environ",
                {"FORGE_CONFIG_PATH": "/nonexistent/path/forge.yaml"},
            ),
            patch("forge_config.load_config", return_value=mock_config),
            patch(
                "forge_config.ConfigWatcher",
            ) as watcher_cls,
        ):
            async with lifespan(app):
                watcher_cls.assert_not_called()

    async def test_health_ready_without_config(self) -> None:
        """App should reach ready state even without any config."""
        app = FastAPI(lifespan=lifespan)

        with patch.dict("os.environ", {"FORGE_CONFIG_PATH": "nonexistent.yaml"}):
            async with lifespan(app):
                assert health._ready is True

        # After shutdown, health flags should be cleared
        assert health._ready is False
        assert health._started is False


# ---------------------------------------------------------------------------
# 6. Health state transitions during lifespan
# ---------------------------------------------------------------------------


class TestLifespanHealthState:
    async def test_started_set_before_ready(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """started should be True before ready is set during startup."""
        state_during_load: dict[str, bool] = {}

        def capture_during_load(p: Any) -> MagicMock:
            state_during_load["started"] = health._started
            state_during_load["ready"] = health._ready
            return mock_config

        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", side_effect=capture_during_load),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
        ):
            async with lifespan(app):
                pass

        # During config load, started should be True but ready not yet
        assert state_during_load["started"] is True
        assert state_during_load["ready"] is False

    async def test_health_cleared_on_shutdown(self) -> None:
        """Both health flags should be cleared after shutdown."""
        app = FastAPI(lifespan=lifespan)

        with patch.dict("os.environ", {"FORGE_CONFIG_PATH": "nonexistent.yaml"}):
            async with lifespan(app):
                assert health._ready is True
                assert health._started is True

        assert health._ready is False
        assert health._started is False


# ---------------------------------------------------------------------------
# 7. Config reload callback rebuilds agent tools and MCP server
# ---------------------------------------------------------------------------


class TestReloadCallbackToolRebuild:
    """Verify the reload callback triggers build_and_swap and rebuild_mcp_server."""

    async def test_reload_callback_schedules_tool_rebuild(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """Reload callback should call _schedule_tool_rebuild with new config and agent."""
        app = FastAPI(lifespan=lifespan)

        new_config = MagicMock()
        new_config.metadata.name = "updated-forge"
        new_config.security.api_keys = None

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
            patch("forge_gateway.app._schedule_tool_rebuild") as mock_schedule,
        ):
            async with lifespan(app):
                mock_load.return_value = new_config
                mock_schedule.reset_mock()

                captured_callback(config_file)

                mock_schedule.assert_called_once()
                call_args = mock_schedule.call_args
                assert call_args[0][0] is new_config
                # Agent should be present (captured during lifespan startup)
                assert call_args[0][1] is not None

    async def test_reload_callback_refreshes_agent_card_with_agent(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """Reload callback should pass both config and agent to _refresh_agent_card."""
        app = FastAPI(lifespan=lifespan)

        new_config = MagicMock()
        new_config.metadata.name = "updated-forge"
        new_config.security.api_keys = None

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
            patch("forge_gateway.app._refresh_agent_card") as mock_refresh,
        ):
            async with lifespan(app):
                mock_load.return_value = new_config
                mock_refresh.reset_mock()

                captured_callback(config_file)

                mock_refresh.assert_called_once()
                call_args = mock_refresh.call_args
                assert call_args[0][0] is new_config
                # Agent should be present (captured during lifespan startup)
                assert call_args[0][1] is not None


class TestRebuildToolSurface:
    """Unit tests for the _rebuild_tool_surface async function."""

    async def test_rebuild_calls_build_and_swap(self) -> None:
        """_rebuild_tool_surface should call build_and_swap on the agent registry."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.return_value = True
        mock_registry.tool_count = 3
        mock_registry.version = "abc123"

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp"):
            await _rebuild_tool_surface(config, mock_agent)

        mock_registry.build_and_swap.assert_awaited_once_with(config)

    async def test_rebuild_calls_rebuild_mcp_server(self) -> None:
        """_rebuild_tool_surface should rebuild the MCP server after tool swap."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.return_value = True
        mock_registry.tool_count = 5

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp") as mock_mcp_module:
            await _rebuild_tool_surface(config, mock_agent)

        mock_mcp_module.rebuild_mcp_server.assert_called_once_with(mock_registry)

    async def test_rebuild_skips_when_no_agent(self) -> None:
        """_rebuild_tool_surface should be a no-op when agent is None."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()

        with patch("forge_gateway.app.mcp") as mock_mcp_module:
            # Should not raise
            await _rebuild_tool_surface(config, None)

        mock_mcp_module.rebuild_mcp_server.assert_not_called()

    async def test_rebuild_skips_when_config_not_forge_config(self) -> None:
        """_rebuild_tool_surface should skip when config is not a ForgeConfig."""
        mock_agent = MagicMock()

        with patch("forge_gateway.app.mcp") as mock_mcp_module:
            await _rebuild_tool_surface("not-a-config", mock_agent)

        mock_mcp_module.rebuild_mcp_server.assert_not_called()

    async def test_rebuild_handles_build_and_swap_failure(self) -> None:
        """_rebuild_tool_surface should log but not raise on build_and_swap failure."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.side_effect = RuntimeError("build failed")

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp") as mock_mcp_module:
            # Should not raise
            await _rebuild_tool_surface(config, mock_agent)

        # MCP rebuild should still be attempted even if build_and_swap fails
        mock_mcp_module.rebuild_mcp_server.assert_called_once_with(mock_registry)

    async def test_rebuild_handles_mcp_rebuild_failure(self) -> None:
        """_rebuild_tool_surface should log but not raise on MCP rebuild failure."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.return_value = True
        mock_registry.tool_count = 2

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp") as mock_mcp_module:
            mock_mcp_module.rebuild_mcp_server.side_effect = RuntimeError("MCP failed")
            # Should not raise
            await _rebuild_tool_surface(config, mock_agent)

        mock_registry.build_and_swap.assert_awaited_once()

    async def test_rebuild_skips_when_agent_has_no_registry(self) -> None:
        """_rebuild_tool_surface should skip when agent has no _registry attribute."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_agent = MagicMock(spec=[])  # No attributes at all

        with patch("forge_gateway.app.mcp") as mock_mcp_module:
            await _rebuild_tool_surface(config, mock_agent)

        mock_mcp_module.rebuild_mcp_server.assert_not_called()


class TestScheduleToolRebuild:
    """Unit tests for the _schedule_tool_rebuild synchronous function."""

    async def test_schedule_creates_task_on_running_loop(self) -> None:
        """_schedule_tool_rebuild should create an asyncio task when a loop is running."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_agent = MagicMock()
        mock_agent._registry = AsyncMock()
        mock_agent._registry.build_and_swap.return_value = False

        with patch("forge_gateway.app.mcp"):
            # We're in an async test, so a loop is running
            _schedule_tool_rebuild(config, mock_agent)

            # Give the event loop a chance to run the scheduled task
            await asyncio.sleep(0.01)

    def test_schedule_skips_when_no_running_loop(self) -> None:
        """_schedule_tool_rebuild should not raise when no event loop is running."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()

        # Should not raise — logs a warning instead
        _schedule_tool_rebuild(config, None)


class TestMakeReloadCallbackAgent:
    """Test that _make_reload_callback captures the agent reference."""

    def test_callback_captures_agent(self) -> None:
        """The closure returned by _make_reload_callback should capture the agent."""
        mock_agent = MagicMock()

        new_config = MagicMock()
        new_config.metadata.name = "test"
        new_config.security.api_keys = None

        callback = _make_reload_callback("/tmp/forge.yaml", agent=mock_agent)

        with (
            patch("forge_config.load_config", return_value=new_config),
            patch("forge_gateway.app._schedule_tool_rebuild") as mock_schedule,
            patch("forge_gateway.app._init_security_gate"),
            patch("forge_gateway.app._refresh_agent_card"),
        ):
            callback(Path("/tmp/forge.yaml"))

            # Agent should be passed to _schedule_tool_rebuild
            mock_schedule.assert_called_once_with(new_config, mock_agent)

    def test_callback_without_agent(self) -> None:
        """The callback should pass None when no agent was provided."""
        new_config = MagicMock()
        new_config.metadata.name = "test"
        new_config.security.api_keys = None

        callback = _make_reload_callback("/tmp/forge.yaml")

        with (
            patch("forge_config.load_config", return_value=new_config),
            patch("forge_gateway.app._schedule_tool_rebuild") as mock_schedule,
            patch("forge_gateway.app._init_security_gate"),
            patch("forge_gateway.app._refresh_agent_card"),
        ):
            callback(Path("/tmp/forge.yaml"))

            mock_schedule.assert_called_once_with(new_config, None)
