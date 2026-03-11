"""Conversational endpoint and MCP mount."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from forge_gateway.models import ConversationRequest, ConversationResponse, ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["conversational"])

_forge_agent: Any = None


def set_agent(agent: Any) -> None:
    global _forge_agent
    _forge_agent = agent


@router.post(
    "/completions",
    response_model=ConversationResponse,
    responses={500: {"model": ErrorResponse}},
)
async def chat(request: ConversationRequest) -> ConversationResponse:
    """Send a conversational message to the agent."""
    if _forge_agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    session_id = request.session_id or str(uuid.uuid4())

    try:
        result = await _forge_agent.run_conversational(
            message=request.message,
            session_id=session_id,
        )
        return ConversationResponse(
            message=result,
            session_id=session_id,
        )
    except Exception as e:
        logger.exception("Conversational request failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
