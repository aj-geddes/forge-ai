"""Programmatic API endpoint: POST /v1/agent/invoke."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from forge_config.schema import AgentDef, ForgeConfig
from pydantic import BaseModel

from forge_gateway.models import ErrorResponse, InvokeRequest, InvokeResponse
from forge_gateway.routes.persona import resolve_persona
from forge_gateway.schema import json_schema_to_model
from forge_gateway.security import security_dependency

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agent",
    tags=["programmatic"],
    dependencies=[Depends(security_dependency)],
)

# Set by app lifespan
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


def _resolve_output_schema(
    raw_schema: dict[str, Any] | None,
) -> type[BaseModel] | None:
    """Convert a JSON Schema dict from the request into a Pydantic model class.

    Returns None if no schema was provided, allowing the agent to fall back
    to its default unstructured response.
    """
    if raw_schema is None:
        return None
    return json_schema_to_model(raw_schema)


@router.post(
    "/invoke",
    response_model=InvokeResponse,
    responses={500: {"model": ErrorResponse}},
)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    """Invoke the agent programmatically with structured input/output."""
    if _forge_agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    persona = _resolve_persona(request.agent)

    try:
        output_schema = _resolve_output_schema(request.output_schema)
        run_result = await _forge_agent.run_structured(
            intent=request.intent,
            params=request.params,
            output_schema=output_schema,
            system_prompt_override=(persona.system_prompt if persona else None),
            model_name_override=persona.model if persona else None,
            max_turns_override=(persona.max_turns if persona else None),
        )
        return InvokeResponse(
            result=run_result.output,
            session_id=request.session_id,
            tools_used=run_result.tools_used,
            model=run_result.model_name,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail="Internal server error") from e
