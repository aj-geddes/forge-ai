"""Tests for the FastAPI app factory and ConfigWatcher lifespan integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
    health.set_version("")
    health.reset_components()


@pytest.fixture()
def mock_config() -> MagicMock:
    """A minimal mock ForgeConfig."""
    config = MagicMock()
    config.metadata.name = "test-forge"
    config.metadata.version = "0.0.0-test"
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

    async def test_shutdown_tolerates_mcp_shutdown_failure(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """Shutdown should not raise if stopping the active MCP app fails."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch(
                "forge_gateway.app.mcp.shutdown_active_asgi_app",
                new_callable=AsyncMock,
                side_effect=RuntimeError("MCP shutdown failed"),
            ),
        ):
            # Should not raise despite mcp.shutdown_active_asgi_app() failure
            async with lifespan(app):
                pass

            assert health._ready is False


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
            patch("forge_gateway.app._schedule_auth_reinit") as mock_auth_reinit,
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

                mock_auth_reinit.assert_called_once_with(new_config)

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

    async def test_reload_callback_schedules_auth_reinit(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The reload callback should re-wire the auth subsystem (ADR-0001):
        updated bindings/service tokens/OIDC settings must take effect on
        hot-reload, not just at process startup."""
        app = FastAPI(lifespan=lifespan)

        new_config = MagicMock()
        new_config.metadata.name = "reloaded"

        captured_callback: Any = None

        def capture_watcher(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal captured_callback
            captured_callback = kwargs.get("on_change")
            return mock_watcher

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config) as mock_load,
            patch("forge_config.ConfigWatcher", side_effect=capture_watcher),
            patch("forge_gateway.app._schedule_auth_reinit") as mock_auth_reinit,
        ):
            async with lifespan(app):
                mock_load.return_value = new_config
                mock_auth_reinit.reset_mock()

                captured_callback(config_file)

                mock_auth_reinit.assert_called_once_with(new_config)


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

        with patch("forge_gateway.app.mcp", autospec=True):
            await _rebuild_tool_surface(config, mock_agent)

        mock_registry.build_and_swap.assert_awaited_once_with(config)

    async def test_rebuild_calls_rebuild_and_activate(self) -> None:
        """_rebuild_tool_surface should rebuild AND activate the MCP server.

        Activation (not just rebuild_mcp_server) is required so hot-reloaded
        tools are actually served at the live /mcp mount, not just recorded
        in an inert module-level reference.
        """
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.return_value = True
        mock_registry.tool_count = 5

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp", autospec=True) as mock_mcp_module:
            await _rebuild_tool_surface(config, mock_agent)

        mock_mcp_module.rebuild_and_activate.assert_awaited_once_with(mock_registry)

    async def test_rebuild_skips_when_no_agent(self) -> None:
        """_rebuild_tool_surface should be a no-op when agent is None."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()

        with patch("forge_gateway.app.mcp", autospec=True) as mock_mcp_module:
            # Should not raise
            await _rebuild_tool_surface(config, None)

        mock_mcp_module.rebuild_and_activate.assert_not_called()

    async def test_rebuild_skips_when_config_not_forge_config(self) -> None:
        """_rebuild_tool_surface should skip when config is not a ForgeConfig."""
        mock_agent = MagicMock()

        with patch("forge_gateway.app.mcp", autospec=True) as mock_mcp_module:
            await _rebuild_tool_surface("not-a-config", mock_agent)

        mock_mcp_module.rebuild_and_activate.assert_not_called()

    async def test_rebuild_handles_build_and_swap_failure(self) -> None:
        """_rebuild_tool_surface should log but not raise on build_and_swap failure."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.side_effect = RuntimeError("build failed")

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp", autospec=True) as mock_mcp_module:
            # Should not raise
            await _rebuild_tool_surface(config, mock_agent)

        # MCP rebuild should still be attempted even if build_and_swap fails
        mock_mcp_module.rebuild_and_activate.assert_awaited_once_with(mock_registry)

    async def test_rebuild_handles_mcp_rebuild_failure(self) -> None:
        """_rebuild_tool_surface should log but not raise on MCP rebuild failure."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_registry = AsyncMock()
        mock_registry.build_and_swap.return_value = True
        mock_registry.tool_count = 2

        mock_agent = MagicMock()
        mock_agent._registry = mock_registry

        with patch("forge_gateway.app.mcp", autospec=True) as mock_mcp_module:
            mock_mcp_module.rebuild_and_activate.side_effect = RuntimeError("MCP failed")
            # Should not raise
            await _rebuild_tool_surface(config, mock_agent)

        mock_registry.build_and_swap.assert_awaited_once()

    async def test_rebuild_skips_when_agent_has_no_registry(self) -> None:
        """_rebuild_tool_surface should skip when agent has no _registry attribute."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_agent = MagicMock(spec=[])  # No attributes at all

        with patch("forge_gateway.app.mcp", autospec=True) as mock_mcp_module:
            await _rebuild_tool_surface(config, mock_agent)

        mock_mcp_module.rebuild_and_activate.assert_not_called()


class TestScheduleToolRebuild:
    """Unit tests for the _schedule_tool_rebuild synchronous function."""

    async def test_schedule_creates_task_on_running_loop(self) -> None:
        """_schedule_tool_rebuild should create an asyncio task when a loop is running."""
        from forge_config.schema import ForgeConfig

        config = ForgeConfig()
        mock_agent = MagicMock()
        mock_agent._registry = AsyncMock()
        mock_agent._registry.build_and_swap.return_value = False

        with patch("forge_gateway.app.mcp", autospec=True):
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
            patch("forge_gateway.app._schedule_auth_reinit"),
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
            patch("forge_gateway.app._schedule_auth_reinit"),
            patch("forge_gateway.app._refresh_agent_card"),
        ):
            callback(Path("/tmp/forge.yaml"))

            mock_schedule.assert_called_once_with(new_config, None)


# ---------------------------------------------------------------------------
# 8. Agent initialization outcome drives readiness health state
# ---------------------------------------------------------------------------


class TestAgentInitializationHealthState:
    """Verify agent init success/failure/absence is reflected in health state."""

    async def test_agent_init_failure_sets_component_failed(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """A raised exception from agent.initialize() must mark 'agent' as failed."""
        app = FastAPI(lifespan=lifespan)

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", return_value=mock_agent),
        ):
            async with lifespan(app):
                assert health._components.get("agent") == "failed"

    async def test_agent_init_failure_makes_readiness_endpoint_return_503(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The public /health/ready endpoint must surface the agent failure as 503."""
        app = FastAPI(lifespan=lifespan)
        app.include_router(health.router)

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", return_value=mock_agent),
        ):
            async with lifespan(app):
                client = TestClient(app)
                response = client.get("/health/ready")
                assert response.status_code == 503

    async def test_agent_init_success_sets_component_ready(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """A successful agent.initialize() call must mark 'agent' as ready."""
        app = FastAPI(lifespan=lifespan)

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(return_value=None)
        mock_agent._registry = None

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", return_value=mock_agent),
        ):
            async with lifespan(app):
                assert health._components.get("agent") == "ready"

    async def test_agent_init_success_readiness_endpoint_returns_200(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The public /health/ready endpoint must return 200 when the agent is
        ready and the auth subsystem is healthy. ``mock_config`` is a loosely
        typed MagicMock (not a real ForgeConfig), so ``_init_auth`` is
        stubbed here -- this test's concern is agent-readiness reporting,
        covered separately by the auth-specific readiness tests in
        test_auth_enforcement.py."""
        app = FastAPI(lifespan=lifespan)
        app.include_router(health.router)

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(return_value=None)
        mock_agent._registry = None

        async def _fake_init_auth(config: object) -> None:
            health.set_auth_healthy(True)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", return_value=mock_agent),
            patch("forge_gateway.app._init_auth", _fake_init_auth),
        ):
            async with lifespan(app):
                client = TestClient(app)
                response = client.get("/health/ready")
                assert response.status_code == 200
                assert response.json()["status"] == "ready"

    async def test_agent_unavailable_when_forge_agent_import_fails(
        self,
        mock_config: MagicMock,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """forge-agent not being installed is a valid gateway-only mode, not a failure."""
        app = FastAPI(lifespan=lifespan)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=mock_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", side_effect=ImportError("no module")),
        ):
            async with lifespan(app):
                assert health._components.get("agent") == "unavailable"


# ---------------------------------------------------------------------------
# 8b. Conversation store wiring (ADR-0003 WS-7)
# ---------------------------------------------------------------------------


class TestConversationStoreWiring:
    """The lifespan builds a ConversationStore from config, injects it into
    the agent, and closes it on shutdown."""

    async def test_builds_store_from_config_and_injects_into_agent(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        from forge_config.schema import ForgeConfig

        real_config = ForgeConfig()
        app = FastAPI(lifespan=lifespan)

        sentinel_store = MagicMock()
        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(return_value=None)
        mock_agent._registry = None
        mock_agent.context = sentinel_store
        forge_agent_ctor = MagicMock(return_value=mock_agent)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=real_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", forge_agent_ctor),
            patch(
                "forge_agent.agent.store.build_conversation_store",
                return_value=sentinel_store,
            ) as mock_build,
        ):
            async with lifespan(app):
                pass

        mock_build.assert_called_once()
        assert mock_build.call_args.args[0] is real_config.conversation_store
        forge_agent_ctor.assert_called_once()
        assert forge_agent_ctor.call_args.kwargs["conversation_store"] is sentinel_store

    async def test_closes_conversation_store_on_shutdown(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        from forge_config.schema import ForgeConfig

        real_config = ForgeConfig()
        app = FastAPI(lifespan=lifespan)

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(return_value=None)
        mock_agent._registry = None
        mock_agent.aclose = AsyncMock(return_value=None)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=real_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", return_value=mock_agent),
        ):
            async with lifespan(app):
                pass

        mock_agent.aclose.assert_awaited_once()

    async def test_closes_orphaned_store_when_agent_construction_fails(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """If the store is built successfully but ForgeAgent itself never
        gets constructed, the orphaned store must still be closed on
        shutdown -- not just leaked."""
        from forge_config.schema import ForgeConfig

        real_config = ForgeConfig()
        app = FastAPI(lifespan=lifespan)

        orphaned_store = MagicMock()
        orphaned_store.close = AsyncMock(return_value=None)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=real_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", side_effect=RuntimeError("agent boom")),
            patch(
                "forge_agent.agent.store.build_conversation_store",
                return_value=orphaned_store,
            ),
        ):
            async with lifespan(app):
                pass

        orphaned_store.close.assert_awaited_once()

    async def test_shutdown_tolerates_orphaned_store_close_failure(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """A failure while closing the orphaned store on shutdown must be
        logged, not raised -- shutdown must always complete."""
        from forge_config.schema import ForgeConfig

        real_config = ForgeConfig()
        app = FastAPI(lifespan=lifespan)

        orphaned_store = MagicMock()
        orphaned_store.close = AsyncMock(side_effect=RuntimeError("close boom"))

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=real_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", side_effect=RuntimeError("agent boom")),
            patch(
                "forge_agent.agent.store.build_conversation_store",
                return_value=orphaned_store,
            ),
        ):
            async with lifespan(app):
                pass  # must not raise on exit either

        orphaned_store.close.assert_awaited_once()

    async def test_store_build_failure_falls_back_gracefully(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """A broken conversation_store config (e.g. an unresolvable Redis
        secret) must never prevent the gateway from starting -- the agent
        still initializes with its own (in-memory) default."""
        from forge_config.schema import ForgeConfig

        real_config = ForgeConfig()
        app = FastAPI(lifespan=lifespan)

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock(return_value=None)
        mock_agent._registry = None

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=real_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
            patch("forge_agent.ForgeAgent", return_value=mock_agent),
            patch(
                "forge_agent.agent.store.build_conversation_store",
                side_effect=RuntimeError("boom"),
            ),
        ):
            async with lifespan(app):
                assert health._components.get("agent") == "ready"


# ---------------------------------------------------------------------------
# 9. Version is populated from config metadata
# ---------------------------------------------------------------------------


class TestVersionReportedFromConfigMetadata:
    """Verify health.set_version() is driven by config.metadata.version."""

    async def test_version_set_from_config_metadata(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The lifespan must propagate config.metadata.version into health state."""
        app = FastAPI(lifespan=lifespan)

        local_config = MagicMock()
        local_config.metadata.name = "test-forge"
        local_config.metadata.version = "3.4.5"
        local_config.security.api_keys = None

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=local_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
        ):
            async with lifespan(app):
                assert health._version == "3.4.5"

    async def test_readiness_endpoint_reports_version_from_config(
        self,
        mock_watcher: MagicMock,
        config_file: Path,
    ) -> None:
        """The public /health/ready endpoint must report the configured version."""
        app = FastAPI(lifespan=lifespan)
        app.include_router(health.router)

        local_config = MagicMock()
        local_config.metadata.name = "test-forge"
        local_config.metadata.version = "3.4.5"
        local_config.security.api_keys = None

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": str(config_file)}),
            patch("forge_config.load_config", return_value=local_config),
            patch("forge_config.ConfigWatcher", return_value=mock_watcher),
        ):
            async with lifespan(app):
                client = TestClient(app)
                response = client.get("/health/ready")
                assert response.json()["version"] == "3.4.5"


# ---------------------------------------------------------------------------
# 10. Static directory resolution is robust across Docker and local dev
# ---------------------------------------------------------------------------


class TestResolveStaticDir:
    """_resolve_static_dir() finds the built UI in Docker, local dev, or nowhere."""

    def test_prefers_docker_static_dir_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Docker-image location (/app/static) takes priority when it exists."""
        import forge_gateway.app as app_module

        docker_dir = tmp_path / "app_static"
        docker_dir.mkdir()
        monkeypatch.setattr(app_module, "_DOCKER_STATIC_DIR", docker_dir)

        assert app_module._resolve_static_dir() == docker_dir

    def test_falls_back_to_forge_ui_dist_when_no_docker_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outside Docker, the local `npm run build` output (forge-ui/dist) is used."""
        import forge_gateway.app as app_module

        missing_docker = tmp_path / "no-such-docker-static"
        packages_root = tmp_path / "packages"
        ui_dist = packages_root / "forge-ui" / "dist"
        ui_dist.mkdir(parents=True)

        monkeypatch.setattr(app_module, "_DOCKER_STATIC_DIR", missing_docker)
        monkeypatch.setattr(app_module, "_packages_dir", lambda: packages_root)

        assert app_module._resolve_static_dir() == ui_dist

    def test_falls_back_to_legacy_static_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A legacy manually-populated packages/static dir is still honored."""
        import forge_gateway.app as app_module

        missing_docker = tmp_path / "no-such-docker-static"
        packages_root = tmp_path / "packages"
        legacy_static = packages_root / "static"
        legacy_static.mkdir(parents=True)

        monkeypatch.setattr(app_module, "_DOCKER_STATIC_DIR", missing_docker)
        monkeypatch.setattr(app_module, "_packages_dir", lambda: packages_root)

        assert app_module._resolve_static_dir() == legacy_static

    def test_returns_none_when_nothing_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no built UI exists anywhere, resolution returns None (not an error)."""
        import forge_gateway.app as app_module

        missing_docker = tmp_path / "no-such-docker-static"
        empty_packages_root = tmp_path / "packages-empty"

        monkeypatch.setattr(app_module, "_DOCKER_STATIC_DIR", missing_docker)
        monkeypatch.setattr(app_module, "_packages_dir", lambda: empty_packages_root)

        assert app_module._resolve_static_dir() is None

    def test_create_app_logs_clearly_when_no_ui_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """create_app() logs a clear, actionable warning when no built UI is found."""
        import logging

        import forge_gateway.app as app_module

        monkeypatch.setattr(app_module, "_resolve_static_dir", lambda: None)

        with (
            patch.dict("os.environ", {"FORGE_CONFIG_PATH": "nonexistent.yaml"}),
            caplog.at_level(logging.WARNING, logger="forge.gateway"),
        ):
            create_app()

        assert any("No built frontend UI found" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 11. SPA fallback cannot escape the static directory (path traversal)
# ---------------------------------------------------------------------------


class TestSafeStaticPath:
    """_safe_static_path() refuses to resolve outside of the static directory."""

    def test_serves_file_within_static_dir(self, tmp_path: Path) -> None:
        from forge_gateway.app import _safe_static_path

        (tmp_path / "app.js").write_text("console.log(1);")

        result = _safe_static_path(tmp_path, "app.js")

        assert result == (tmp_path / "app.js").resolve()

    def test_serves_file_in_nested_subdirectory(self, tmp_path: Path) -> None:
        from forge_gateway.app import _safe_static_path

        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log(1);")

        result = _safe_static_path(tmp_path, "assets/app.js")

        assert result == (assets / "app.js").resolve()

    def test_blocks_dotdot_traversal_outside_static_dir(self, tmp_path: Path) -> None:
        """A ``../`` sequence that would escape the static dir is refused."""
        from forge_gateway.app import _safe_static_path

        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        result = _safe_static_path(static_dir, "../secret.txt")

        assert result is None

    def test_blocks_deeply_nested_dotdot_traversal(self, tmp_path: Path) -> None:
        from forge_gateway.app import _safe_static_path

        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        result = _safe_static_path(static_dir, "../../../../../../secret.txt")

        assert result is None

    def test_blocks_absolute_path_escape(self, tmp_path: Path) -> None:
        """pathlib silently discards the base when joined with an absolute path;
        this must not translate into serving an arbitrary absolute path."""
        from forge_gateway.app import _safe_static_path

        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("do not serve me")
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        result = _safe_static_path(static_dir, str(outside_file))

        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        from forge_gateway.app import _safe_static_path

        assert _safe_static_path(tmp_path, "does-not-exist.html") is None

    def test_returns_none_for_directory(self, tmp_path: Path) -> None:
        from forge_gateway.app import _safe_static_path

        sub = tmp_path / "sub"
        sub.mkdir()

        assert _safe_static_path(tmp_path, "sub") is None


class TestSpaFallbackPathTraversalIntegration:
    """The real /{path} route wired in create_app() refuses traversal attempts."""

    def _build_app_with_static_dir(
        self, static_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> FastAPI:
        import forge_gateway.app as app_module

        (static_dir / "index.html").write_text("<html>SPA shell</html>")
        (static_dir / "assets").mkdir()
        (static_dir / "assets" / "app.js").write_text("console.log('ok');")

        monkeypatch.setattr(app_module, "_resolve_static_dir", lambda: static_dir)

        with patch.dict("os.environ", {"FORGE_CONFIG_PATH": "nonexistent.yaml"}):
            return create_app()

    def test_legitimate_asset_is_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        app = self._build_app_with_static_dir(static_dir, monkeypatch)
        client = TestClient(app)

        resp = client.get("/assets/app.js")

        assert resp.status_code == 200
        assert "console.log" in resp.text

    def test_top_level_static_file_served_via_catch_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real file directly under static_dir (e.g. favicon.svg) is served
        through the SPA catch-all route's _safe_static_path branch, not just
        via the separate /assets mount."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        app = self._build_app_with_static_dir(static_dir, monkeypatch)
        (static_dir / "favicon.svg").write_text("<svg>fav</svg>")
        client = TestClient(app)

        resp = client.get("/favicon.svg")

        assert resp.status_code == 200
        assert "fav" in resp.text

    def test_encoded_dotdot_traversal_does_not_leak_file_contents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A percent-encoded ``..`` segment must not reach a file outside static_dir."""
        secret = tmp_path / "secret.txt"
        secret.write_text("do-not-serve-me")
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        app = self._build_app_with_static_dir(static_dir, monkeypatch)
        client = TestClient(app)

        resp = client.get("/%2e%2e/secret.txt")

        assert "do-not-serve-me" not in resp.text

    def test_unknown_path_returns_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        app = self._build_app_with_static_dir(static_dir, monkeypatch)
        client = TestClient(app)

        resp = client.get("/this-does-not-exist-anywhere")

        assert resp.status_code == 404

    def test_known_spa_route_serves_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        app = self._build_app_with_static_dir(static_dir, monkeypatch)
        client = TestClient(app)

        resp = client.get("/config")

        assert resp.status_code == 200
        assert "SPA shell" in resp.text


# ---------------------------------------------------------------------------
# 12. The /mcp mount is always present, regardless of agent availability
# ---------------------------------------------------------------------------


class TestMCPMountAlwaysPresent:
    """The persistent /mcp dispatcher is mounted in create_app() unconditionally.

    This ensures unauthenticated/unavailable-agent requests get a clean,
    auth-gated 401/503 response rather than a bare 404 that leaks whether
    MCP is configured at all, and means rebuilds never need to re-mount
    routes on a running app (which FastAPI does not support).
    """

    def test_mcp_mount_present_without_agent(self) -> None:
        with patch.dict("os.environ", {"FORGE_CONFIG_PATH": "nonexistent.yaml"}):
            app = create_app()

        mount_names = [r.name for r in app.routes if hasattr(r, "name")]
        assert "mcp" in mount_names
