"""Programmatic API endpoint: POST /v1/agent/invoke."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from forge_gateway.models import ErrorResponse, InvokeRequest, InvokeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agent", tags=["programmatic"])

# Set by app lifespan
_forge_agent: Any = None


def set_agent(agent: Any) -> None:
    global _forge_agent
    _forge_agent = agent


@router.post(
    "/invoke",
    response_model=InvokeResponse,
    responses={500: {"model": ErrorResponse}},
)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    """Invoke the agent programmatically with structured input/output."""
    if _forge_agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    try:
        result = await _forge_agent.run_structured(
            intent=request.intent,
            params=request.params,
            output_schema=request.output_schema,
        )
        return InvokeResponse(
            result=result,
            session_id=request.session_id,
        )
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
