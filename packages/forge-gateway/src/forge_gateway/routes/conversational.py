"""Conversational endpoint and MCP mount."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from forge_agent.agent.core import ToolCallRecord
from forge_config.schema import AgentDef, ForgeConfig

from forge_gateway import metrics_registry, security
from forge_gateway.models import (
    ConversationRequest,
    ConversationResponse,
    ErrorResponse,
    ToolCallInfo,
)
from forge_gateway.routes.persona import resolve_persona

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/chat",
    tags=["conversational"],
    dependencies=[Depends(security.require_permission("agent:invoke"))],
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


def _tool_call_payload(record: ToolCallRecord, session_id: str) -> str:
    """Build the JSON payload for a ``tool_call`` SSE frame."""
    return json.dumps(
        {
            "tool_call": {
                "name": record.name,
                "arguments": record.arguments,
                "result": record.result,
            },
            "session_id": session_id,
        },
        default=str,
    )


async def _sse_generator(
    chunks: AsyncIterator[str | ToolCallRecord],
    session_id: str,
    *,
    start_time: float,
) -> AsyncIterator[str]:
    """Wrap agent stream items as SSE events.

    Text chunks are sent as ``data: {"chunk": "...", "session_id": "..."}\n\n``.
    ``ToolCallRecord`` items are sent as a distinct frame type:
    ``data: {"tool_call": {"name", "arguments", "result"}, "session_id": "..."}\n\n``.

    Sends ``data: [DONE]\n\n`` as the final sentinel.
    """
    status = "success"
    try:
        async for item in chunks:
            if isinstance(item, ToolCallRecord):
                metrics_registry.record_tool_invocation(item.name)
                payload = _tool_call_payload(item, session_id)
            else:
                payload = json.dumps({"chunk": item, "session_id": session_id})
            yield f"data: {payload}\n\n"
    except Exception:
        status = "error"
        logger.exception("Error during streaming response")
        error_payload = json.dumps({"error": "Internal server error", "session_id": session_id})
        yield f"data: {error_payload}\n\n"
    finally:
        metrics_registry.record_agent_invocation(
            "chat_stream", status, time.monotonic() - start_time
        )
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
    start_time = time.monotonic()
    status = "success"
    try:
        persona_tools = (persona.tools or None) if persona else None
        run_result = await _forge_agent.run_conversational(
            message=request.message,
            session_id=session_id,
            system_prompt_override=(persona.system_prompt if persona else None),
            model_name_override=persona.model if persona else None,
            max_turns_override=(persona.max_turns if persona else None),
            tool_names_filter=persona_tools,
        )
        for tc in run_result.tool_calls:
            metrics_registry.record_tool_invocation(tc.name)
        return ConversationResponse(
            message=run_result.output,
            session_id=session_id,
            tools_used=run_result.tools_used,
            model=run_result.model_name,
            tool_calls=[
                ToolCallInfo(name=tc.name, arguments=tc.arguments, result=tc.result)
                for tc in run_result.tool_calls
            ],
        )
    except HTTPException:
        status = "error"
        raise
    except Exception as e:
        status = "error"
        logger.exception("Conversational request failed")
        raise HTTPException(status_code=500, detail="Internal server error") from e
    finally:
        metrics_registry.record_agent_invocation("chat", status, time.monotonic() - start_time)


async def _handle_streaming(
    request: ConversationRequest,
    session_id: str,
    persona: AgentDef | None = None,
) -> StreamingResponse:
    """Handle a streaming chat request, returning SSE."""
    start = time.monotonic()
    try:
        persona_tools = (persona.tools or None) if persona else None
        chunks: AsyncIterator[str | ToolCallRecord] = await _forge_agent.run_conversational(
            message=request.message,
            session_id=session_id,
            stream=True,
            system_prompt_override=(persona.system_prompt if persona else None),
            model_name_override=persona.model if persona else None,
            max_turns_override=(persona.max_turns if persona else None),
            tool_names_filter=persona_tools,
        )
    except Exception as e:
        logger.exception("Failed to start streaming response")
        raise HTTPException(status_code=500, detail="Internal server error") from e

    return StreamingResponse(
        _sse_generator(chunks, session_id, start_time=start),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
