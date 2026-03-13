"""Shared persona resolution for route modules.

Provides a single ``resolve_persona`` function used by both the
conversational and programmatic route modules, eliminating the
duplicated ``_resolve_persona`` helpers.
"""

from __future__ import annotations

from fastapi import HTTPException
from forge_config.schema import AgentDef, ForgeConfig


def resolve_persona(
    agent_name: str | None,
    config: ForgeConfig | None,
) -> AgentDef | None:
    """Resolve an agent persona name to its AgentDef from config.

    Returns None when no persona is specified (or the name is empty),
    indicating the default agent behaviour should be used.

    Args:
        agent_name: The persona name from the request, or None/empty.
        config: The loaded ForgeConfig, or None if not yet loaded.

    Returns:
        The matching AgentDef, or None for default behaviour.

    Raises:
        HTTPException: 404 when a non-empty name does not match any
            configured persona or when config is not loaded.
    """
    if not agent_name:
        return None

    if config is None:
        raise HTTPException(status_code=404, detail="Unknown agent persona")

    for agent_def in config.agents.agents:
        if agent_def.name == agent_name:
            return agent_def

    raise HTTPException(status_code=404, detail="Unknown agent persona")
