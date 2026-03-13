"""Conversational endpoint and MCP mount."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from forge_config.schema import AgentDef, ForgeConfig

from forge_gateway.models import ConversationRequest, ConversationResponse, ErrorResponse
from forge_gateway.routes.persona import resolve_persona
from forge_gateway.security import security_dependency

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/chat",
    tags=["conversational"],
    dependencies=[Depends(security_dependency)],
)

_forge_agent: Any = None
_config: ForgeConfig | None = None


def set_agent(agent: Any) -> None:
    global _forge_agent
    _forge_agent = agent


def set_config(config: ForgeConfig | None) -> None:
    global _config
    _config = config


def _resolve_persona(agent_name: str | None) -> AgentDef | None:
    """Resolve an agent persona name to its AgentDef from config.

    Delegates to the shared ``resolve_persona`` helper, passing the
    module-level ``_config``.
    """
    return resolve_persona(agent_name, _config)


async def _sse_generator(
    chunks: AsyncIterator[str],
    session_id: str,
) -> AsyncIterator[str]:
    """Wrap agent text chunks as SSE events.

    Yields Server-Sent Events in OpenAI-compatible format:
    ``data: {"chunk": "...", "session_id": "..."}\n\n``

    Sends ``data: [DONE]\n\n`` as the final sentinel.
    """
    try:
        async for text in chunks:
            payload = json.dumps({"chunk": text, "session_id": session_id})
            yield f"data: {payload}\n\n"
    except Exception as exc:
        logger.exception("Error during streaming response")
        error_payload = json.dumps({"error": str(exc), "session_id": session_id})
        yield f"data: {error_payload}\n\n"
    finally:
        yield "data: [DONE]\n\n"


@router.post(
    "/completions",
    response_model=ConversationResponse,
    responses={500: {"model": ErrorResponse}},
)
async def chat(request: ConversationRequest) -> ConversationResponse | StreamingResponse:
    """Send a conversational message to the agent."""
    if _forge_agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    persona = _resolve_persona(request.agent)
    session_id = request.session_id or str(uuid.uuid4())

    if request.stream:
        return await _handle_streaming(request, session_id, persona)

    return await _handle_non_streaming(request, session_id, persona)


async def _handle_non_streaming(
    request: ConversationRequest,
    session_id: str,
    persona: AgentDef | None = None,
) -> ConversationResponse:
    """Handle a standard (non-streaming) chat request."""
    try:
        run_result = await _forge_agent.run_conversational(
            message=request.message,
            session_id=session_id,
            system_prompt_override=(persona.system_prompt if persona else None),
            model_name_override=persona.model if persona else None,
            max_turns_override=(persona.max_turns if persona else None),
        )
        return ConversationResponse(
            message=run_result.output,
            session_id=session_id,
            tools_used=run_result.tools_used,
            model=run_result.model_name,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Conversational request failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _handle_streaming(
    request: ConversationRequest,
    session_id: str,
    persona: AgentDef | None = None,
) -> StreamingResponse:
    """Handle a streaming chat request, returning SSE."""
    try:
        chunks: AsyncIterator[str] = await _forge_agent.run_conversational(
            message=request.message,
            session_id=session_id,
            stream=True,
            system_prompt_override=(persona.system_prompt if persona else None),
            model_name_override=persona.model if persona else None,
            max_turns_override=(persona.max_turns if persona else None),
        )
    except Exception as e:
        logger.exception("Failed to start streaming response")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return StreamingResponse(
        _sse_generator(chunks, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
