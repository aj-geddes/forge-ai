"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from forge_gateway.auth import set_api_key_config
from forge_gateway.middleware.logging import RequestLoggingMiddleware
from forge_gateway.routes import a2a, admin, conversational, health, mcp, metrics, programmatic
from forge_gateway.security import set_security_gate

logger = logging.getLogger("forge.gateway")


def _make_reload_callback(config_path: str) -> Callable[[Path], None]:
    """Create a config-reload callback bound to a specific config path.

    The returned callable accepts a ``Path`` argument (provided by ConfigWatcher)
    and reloads the config, updating admin state and API key auth.  The existing
    agent reference is preserved across reloads.
    """

    def _on_config_change(changed_path: Path) -> None:
        logger.info("Config file changed: %s, triggering reload", changed_path)
        try:
            from forge_config import load_config

            new_config = load_config(str(changed_path))
            logger.info("Reloaded config: %s", new_config.metadata.name)

            # Preserve the current agent reference across config reloads
            admin.set_state(config=new_config, config_path=config_path)
            set_api_key_config(new_config.security.api_keys)
            _init_security_gate(new_config)
        except Exception:
            logger.exception("Failed to reload config from %s", changed_path)

    return _on_config_change


def _init_security_gate(config: object | None) -> None:
    """Build and wire a ``SecurityGate`` from the loaded config.

    When *config* is ``None`` or its ``security.agentweave.enabled`` flag is
    ``False``, the gate is set to ``None`` which activates development mode
    (unauthenticated access with a logged warning).
    """
    from forge_config.schema import ForgeConfig

    if config is None or not isinstance(config, ForgeConfig):
        set_security_gate(None)
        return

    if not config.security.agentweave.enabled:
        logger.info("AgentWeave security disabled in config — development mode active")
        set_security_gate(None)
        return

    try:
        from forge_security import SecurityGate

        gate = SecurityGate.from_config(config.security)
        set_security_gate(gate)
        logger.info(
            "SecurityGate initialized for trust domain '%s'",
            config.security.agentweave.trust_domain,
        )
    except Exception:
        logger.exception("Failed to initialize SecurityGate — falling back to development mode")
        set_security_gate(None)


def _init_mcp_server(app: FastAPI, agent: object, config: object) -> None:
    """Build the FastMCP server from the agent's tool registry and mount it.

    When the agent has an initialized tool registry, this creates an MCP
    server exposing those tools and mounts its ASGI app at ``/mcp``.
    Failures are logged but do not prevent the gateway from starting.
    """
    try:
        from forge_agent import ForgeAgent
        from forge_config.schema import ForgeConfig

        if not isinstance(agent, ForgeAgent) or not isinstance(config, ForgeConfig):
            return

        server_name = config.metadata.name or "Forge AI"
        mcp_server = mcp.build_mcp_server(agent.registry, name=server_name)
        mcp_app = mcp.get_mcp_asgi_app(mcp_server)
        app.mount("/mcp", mcp_app, name="mcp")
        logger.info("MCP server mounted at /mcp with %d tools", agent.registry.tool_count)
    except ImportError:
        logger.debug("MCP dependencies not available, skipping MCP server")
    except Exception:
        logger.exception("Failed to initialize MCP server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: build tools on startup, drain on shutdown."""
    logger.info("Forge Gateway starting up")

    # Signal startup complete
    health.set_started(True)

    watcher = None

    try:
        # Try to initialize the agent from config
        config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")
        config = None
        agent = None

        try:
            from forge_config import load_config

            config = load_config(config_path)
            logger.info("Loaded config: %s", config.metadata.name)

            # Try to build the agent
            try:
                from forge_agent import ForgeAgent

                agent = ForgeAgent(config)
                await agent.initialize()

                # Wire agent into all route modules
                programmatic.set_agent(agent)
                conversational.set_agent(agent)
                a2a.set_agent(agent)

                # Build MCP server from the agent's tool registry
                _init_mcp_server(app, agent, config)

                logger.info("Agent initialized successfully")
            except ImportError:
                logger.warning("forge-agent not available, running in gateway-only mode")
            except Exception:
                logger.exception("Failed to initialize agent")

        except Exception:
            logger.warning("No config loaded, running with defaults")

        # Wire admin state, API key auth, and SecurityGate
        admin.set_state(config=config, config_path=config_path, agent=agent)
        if config is not None:
            set_api_key_config(config.security.api_keys)
        _init_security_gate(config)

        # Start config file watcher for hot-reload
        if config is not None and Path(config_path).exists():
            try:
                from forge_config import ConfigWatcher

                callback = _make_reload_callback(config_path)
                watcher = ConfigWatcher(config_path, on_change=callback)
                watcher.start()
            except ImportError:
                logger.warning("ConfigWatcher not available, hot-reload disabled")
            except Exception:
                logger.exception("Failed to start config watcher, hot-reload disabled")

        health.set_ready(True)
        logger.info("Forge Gateway ready")

        yield

    finally:
        logger.info("Forge Gateway shutting down")
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                logger.exception("Error stopping config watcher")
        health.set_ready(False)
        health.set_started(False)


def _resolve_cors_origins() -> list[str]:
    """Read ``allowed_origins`` from the config file for CORS setup.

    Falls back to ``["*"]`` with a logged warning when the config cannot be
    loaded or no origins are explicitly configured.
    """
    config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")
    try:
        from forge_config import load_config

        config = load_config(config_path)
        origins: list[str] = config.security.allowed_origins
        if origins:
            return origins
    except Exception:
        logger.debug("Could not load config for CORS origins, using permissive defaults")

    logger.warning("CORS allowed_origins not configured — defaulting to ['*'] (dev mode)")
    return ["*"]


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Forge AI Gateway",
        description="Config-driven AI agent system with dynamic MCP tool surfaces",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware — CORS must be added before startup
    origins = _resolve_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    # API Routes
    app.include_router(health.router)
    app.include_router(programmatic.router)
    app.include_router(conversational.router)
    app.include_router(a2a.router)
    app.include_router(metrics.router)
    app.include_router(admin.router)

    # Serve frontend SPA if static directory exists
    static_dir = Path(__file__).parent.parent.parent.parent / "static"
    if not static_dir.exists():
        # Also check for an absolute /app/static path (Docker)
        static_dir = Path("/app/static")

    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")
        logger.info("Serving UI from %s", static_dir)

    return app
