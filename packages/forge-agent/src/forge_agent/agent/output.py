"""Output formatting for Forge Agent.

Provides output wrapper types for conversational (plain string)
and structured (Pydantic model) agent responses.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ConversationalOutput(BaseModel):
    """Wraps a plain string response from the agent.

    Attributes:
        message: The agent's text response.
        session_id: The session this response belongs to.
        tools_used: Names of tools invoked during this interaction.
        model: The model name used for this interaction.
    """

    message: str
    session_id: str | None = None
    tools_used: list[str] = []
    model: str | None = None


class StructuredOutput(BaseModel):
    """Wraps a structured response from the agent.

    Attributes:
        data: The structured output data (dict or Pydantic model serialized).
        schema_name: The name of the output schema used.
        session_id: The session this response belongs to.
        tools_used: Names of tools invoked during this interaction.
        model: The model name used for this interaction.
    """

    data: dict[str, Any]
    schema_name: str | None = None
    session_id: str | None = None
    tools_used: list[str] = []
    model: str | None = None
