"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from forge_gateway.middleware.logging import RequestLoggingMiddleware
from forge_gateway.routes import a2a, conversational, health, metrics, programmatic

logger = logging.getLogger("forge.gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: build tools on startup, drain on shutdown."""
    logger.info("Forge Gateway starting up")

    # Signal startup complete
    health.set_started(True)

    try:
        # Try to initialize the agent from config
        config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")

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

                logger.info("Agent initialized successfully")
            except ImportError:
                logger.warning("forge-agent not available, running in gateway-only mode")
            except Exception:
                logger.exception("Failed to initialize agent")

        except Exception:
            logger.warning("No config loaded, running with defaults")

        health.set_ready(True)
        logger.info("Forge Gateway ready")

        yield

    finally:
        logger.info("Forge Gateway shutting down")
        health.set_ready(False)
        health.set_started(False)


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Forge AI Gateway",
        description="Config-driven AI agent system with dynamic MCP tool surfaces",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(RequestLoggingMiddleware)

    # Routes
    app.include_router(health.router)
    app.include_router(programmatic.router)
    app.include_router(conversational.router)
    app.include_router(a2a.router)
    app.include_router(metrics.router)

    return app
