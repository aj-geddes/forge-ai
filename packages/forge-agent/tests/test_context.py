"""Tests for ConversationContext."""

from __future__ import annotations

from forge_agent.agent.context import ConversationContext


class TestConversationContext:
    """Tests for the sliding window conversation context manager."""

    def test_initial_state(self) -> None:
        ctx = ConversationContext()
        assert ctx.max_messages == 50
        assert ctx.session_count() == 0

    def test_custom_max_messages(self) -> None:
        ctx = ConversationContext(max_messages=10)
        assert ctx.max_messages == 10

    def test_add_and_get_messages(self) -> None:
        ctx = ConversationContext()
        ctx.add_message("s1", {"role": "user", "content": "hello"})
        ctx.add_message("s1", {"role": "assistant", "content": "hi"})

        messages = ctx.get_messages("s1")
        assert len(messages) == 2
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "hi"

    def test_separate_sessions(self) -> None:
        ctx = ConversationContext()
        ctx.add_message("s1", "msg1")
        ctx.add_message("s2", "msg2")

        assert ctx.get_messages("s1") == ["msg1"]
        assert ctx.get_messages("s2") == ["msg2"]
        assert ctx.session_count() == 2

    def test_sliding_window_drops_oldest(self) -> None:
        ctx = ConversationContext(max_messages=3)

        ctx.add_message("s1", "a")
        ctx.add_message("s1", "b")
        ctx.add_message("s1", "c")
        ctx.add_message("s1", "d")

        messages = ctx.get_messages("s1")
        assert messages == ["b", "c", "d"]

    def test_sliding_window_with_exact_limit(self) -> None:
        ctx = ConversationContext(max_messages=2)

        ctx.add_message("s1", "a")
        ctx.add_message("s1", "b")

        messages = ctx.get_messages("s1")
        assert messages == ["a", "b"]
        assert ctx.message_count("s1") == 2

    def test_add_messages_bulk(self) -> None:
        ctx = ConversationContext(max_messages=3)
        ctx.add_messages("s1", ["a", "b", "c", "d", "e"])

        messages = ctx.get_messages("s1")
        assert messages == ["c", "d", "e"]

    def test_clear_session(self) -> None:
        ctx = ConversationContext()
        ctx.add_message("s1", "msg1")
        ctx.add_message("s2", "msg2")

        ctx.clear_session("s1")
        assert ctx.get_messages("s1") == []
        assert ctx.get_messages("s2") == ["msg2"]

    def test_clear_nonexistent_session(self) -> None:
        ctx = ConversationContext()
        ctx.clear_session("nonexistent")  # Should not raise

    def test_message_count(self) -> None:
        ctx = ConversationContext()
        assert ctx.message_count("s1") == 0

        ctx.add_message("s1", "a")
        ctx.add_message("s1", "b")
        assert ctx.message_count("s1") == 2

    def test_get_messages_returns_copy(self) -> None:
        ctx = ConversationContext()
        ctx.add_message("s1", "msg1")

        messages = ctx.get_messages("s1")
        messages.clear()
        assert ctx.message_count("s1") == 1

    def test_empty_session_returns_empty_list(self) -> None:
        ctx = ConversationContext()
        messages = ctx.get_messages("nonexistent")
        assert messages == []
