"""Conversation context management for Forge Agent.

Provides a simple sliding window context manager that stores messages
per session_id with a configurable maximum message count.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class ConversationContext:
    """Sliding window conversation context manager.

    Stores conversation messages per session_id and maintains a
    configurable maximum window size. When the window is exceeded,
    oldest messages are dropped.

    Args:
        max_messages: Maximum number of messages to retain per session.
    """

    def __init__(self, max_messages: int = 50) -> None:
        self._max_messages = max_messages
        self._sessions: dict[str, list[Any]] = defaultdict(list)

    @property
    def max_messages(self) -> int:
        """The maximum number of messages retained per session."""
        return self._max_messages

    def add_message(self, session_id: str, message: Any) -> None:
        """Add a message to a session's context.

        If the session exceeds max_messages, the oldest message is dropped.

        Args:
            session_id: The session identifier.
            message: The message to add (any PydanticAI ModelMessage).
        """
        messages = self._sessions[session_id]
        messages.append(message)
        if len(messages) > self._max_messages:
            self._sessions[session_id] = messages[-self._max_messages :]

    def add_messages(self, session_id: str, messages: list[Any]) -> None:
        """Add multiple messages to a session's context.

        Args:
            session_id: The session identifier.
            messages: The messages to add.
        """
        for msg in messages:
            self.add_message(session_id, msg)

    def get_messages(self, session_id: str) -> list[Any]:
        """Get all messages for a session.

        Args:
            session_id: The session identifier.

        Returns:
            List of messages in chronological order.
        """
        return list(self._sessions[session_id])

    def clear_session(self, session_id: str) -> None:
        """Clear all messages for a session.

        Args:
            session_id: The session identifier.
        """
        self._sessions.pop(session_id, None)

    def session_count(self) -> int:
        """Return the number of active sessions."""
        return len(self._sessions)

    def message_count(self, session_id: str) -> int:
        """Return the number of messages in a session.

        Args:
            session_id: The session identifier.

        Returns:
            Number of messages stored for this session.
        """
        return len(self._sessions[session_id])
