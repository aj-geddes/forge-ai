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

    When ``agent_name`` is not given (None/empty), the configured
    ``config.agents.default`` persona is applied instead, if it names an
    entry that actually exists in ``config.agents.agents``. This is an
    implicit fallback (not a user request), so an unset or unmatched
    default (e.g. the schema default ``"assistant"`` with no such
    persona defined) silently resolves to the base agent -- it never
    raises 404.

    Args:
        agent_name: The persona name from the request, or None/empty.
        config: The loaded ForgeConfig, or None if not yet loaded.

    Returns:
        The matching AgentDef, or None for base-agent behaviour.

    Raises:
        HTTPException: 404 when a non-empty, explicitly requested name
            does not match any configured persona or when config is not
            loaded.
    """
    if not agent_name:
        return _find_persona(config, _default_persona_name(config))

    if config is None:
        raise HTTPException(status_code=404, detail="Unknown agent persona")

    persona = _find_persona(config, agent_name)
    if persona is None:
        raise HTTPException(status_code=404, detail="Unknown agent persona")
    return persona


def _default_persona_name(config: ForgeConfig | None) -> str | None:
    """The configured default persona name, or None if config is missing."""
    return config.agents.default if config is not None else None


def _find_persona(config: ForgeConfig | None, name: str | None) -> AgentDef | None:
    """Look up *name* in ``config.agents.agents``, or None if unmatched."""
    if config is None or not name:
        return None
    for agent_def in config.agents.agents:
        if agent_def.name == name:
            return agent_def
    return None
