"""AgentWeave A2A endpoint."""

from __future__ import annotations

import logging
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


_agent_card: AgentCard | None = None


def set_agent_card(card: AgentCard) -> None:
    global _agent_card
    _agent_card = card


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
    except Exception as e:
        logger.exception("A2A task failed")
        return A2ATaskResponse(status="failed", error=str(e))
