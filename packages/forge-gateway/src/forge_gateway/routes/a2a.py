"""AgentWeave A2A endpoint."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from forge_gateway.security import security_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["a2a"])


class AgentCard(BaseModel):
    """Auto-generated agent card for A2A discovery."""

    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    version: str = "0.1.0"
    endpoint: str = ""
    protocols: list[str] = Field(default_factory=list)


_agent_card: AgentCard | None = None


def set_agent_card(card: AgentCard) -> None:
    """Set the module-level agent card for A2A discovery."""
    global _agent_card
    _agent_card = card


def build_agent_card(
    config: object,
    agent: object | None = None,
) -> AgentCard:
    """Build an ``AgentCard`` from a loaded ``ForgeConfig`` and optional agent.

    Extracts metadata (name, description, version) from the config and tool
    names from the agent's registry when available.  The gateway endpoint URL
    is derived from the ``FORGE_GATEWAY_URL`` environment variable, falling
    back to ``http://localhost:8000``.
    """
    from forge_config.schema import ForgeConfig

    if not isinstance(config, ForgeConfig):
        return AgentCard(name="forge", description="Forge AI Agent")

    # Gather tool names from the live registry when the agent is available.
    capabilities: list[str] = []
    try:
        from forge_agent import ForgeAgent

        if isinstance(agent, ForgeAgent):
            capabilities = [t.name for t in agent.registry.tools]
    except ImportError:
        pass

    # Derive the gateway endpoint from env or sensible default.
    endpoint = os.environ.get("FORGE_GATEWAY_URL", "http://localhost:8000")

    # Determine which protocols this gateway exposes.
    protocols = ["a2a", "rest"]
    if _has_mcp_support():
        protocols.append("mcp")

    return AgentCard(
        name=config.metadata.name,
        description=config.metadata.description or f"{config.metadata.name} AI Agent",
        version=config.metadata.version,
        capabilities=capabilities,
        endpoint=endpoint,
        protocols=protocols,
    )


def _has_mcp_support() -> bool:
    """Return ``True`` when FastMCP dependencies are importable."""
    try:
        import fastmcp  # noqa: F401

        return True
    except ImportError:
        return False


@router.get("/agent-card", response_model=AgentCard)
async def get_agent_card() -> AgentCard:
    """Return the agent card for A2A discovery."""
    if _agent_card is None:
        return AgentCard(name="forge", description="Forge AI Agent")
    return _agent_card


class A2ATaskRequest(BaseModel):
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    caller_id: str = ""


class A2ATaskResponse(BaseModel):
    status: str
    result: Any = None
    error: str | None = None


_forge_agent: Any = None


def set_agent(agent: Any) -> None:
    global _forge_agent
    _forge_agent = agent


@router.post(
    "/tasks",
    response_model=A2ATaskResponse,
    dependencies=[Depends(security_dependency)],
)
async def submit_task(request: A2ATaskRequest) -> A2ATaskResponse:
    """Handle incoming A2A task requests."""
    if _forge_agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    try:
        run_result = await _forge_agent.run_structured(
            intent=request.task_type,
            params=request.payload,
        )
        return A2ATaskResponse(status="completed", result=run_result.output)
    except Exception:
        logger.exception("A2A task failed")
        return A2ATaskResponse(status="failed", error="Internal server error")
