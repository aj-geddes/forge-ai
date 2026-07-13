"""Conversation store abstraction for Forge Agent (ADR-0003 WS-7).

``ConversationContext`` (``agent/context.py``) is a synchronous, in-process
``defaultdict`` -- fine for a single dev replica, but it cannot survive a
pod restart or be shared across gateway replicas, even though the docs
(docs/developer/architecture.md, docs/technical/performance.md) promise
durable, cross-replica session storage backed by Redis.

This module raises the code to that spec: :class:`ConversationStore` is the
async interface ``ForgeAgent`` depends on, with two implementations --
:class:`InMemoryConversationStore` (the default/dev fallback, an async
facade over the existing ``ConversationContext``) and
:class:`RedisConversationStore` (durable, cross-replica, config-selected).
:func:`build_conversation_store` builds the right one from
``ConversationStoreConfig``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import redis.asyncio as redis_asyncio
from forge_config.schema import ConversationStoreBackend, ConversationStoreConfig
from forge_config.secret_resolver import SecretResolver
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from forge_agent.agent.context import ConversationContext

# Mirrors ConversationStoreConfig.key_prefix's schema default -- kept in
# sync there, not re-derived here, so a RedisConversationStore constructed
# directly (outside the config factory, e.g. in tests) still gets a
# sensible, collision-resistant default.
_DEFAULT_KEY_PREFIX = "forge:session:"


@runtime_checkable
class ConversationStore(Protocol):
    """Async interface for per-session conversation message storage.

    ``ForgeAgent`` depends only on this protocol, never on a concrete
    implementation -- see :class:`InMemoryConversationStore` (default/dev)
    and :class:`RedisConversationStore` (durable, cross-replica, ADR-0003
    WS-7). Both implement sliding-window semantics: once a session exceeds
    ``max_messages``, the oldest messages are dropped so memory (or Redis
    key size) stays bounded.
    """

    @property
    def max_messages(self) -> int:
        """The maximum number of messages retained per session."""
        ...

    async def add_messages(self, session_id: str, messages: list[ModelMessage]) -> None:
        """Append messages to a session, enforcing the sliding window.

        Args:
            session_id: The session identifier.
            messages: The messages to append, in chronological order.
        """
        ...

    async def get_messages(self, session_id: str) -> list[ModelMessage]:
        """Return all messages stored for a session, in chronological order.

        Args:
            session_id: The session identifier.

        Returns:
            The session's messages, oldest first. Empty list if the
            session has no messages (or does not exist).
        """
        ...

    async def clear_session(self, session_id: str) -> None:
        """Delete all messages for a session.

        Args:
            session_id: The session identifier.
        """
        ...

    async def session_count(self) -> int:
        """Return the number of active sessions."""
        ...

    async def message_count(self, session_id: str) -> int:
        """Return the number of messages stored for a session.

        Args:
            session_id: The session identifier.
        """
        ...

    async def session_ids(self) -> list[str]:
        """Return the identifiers of all active sessions."""
        ...

    async def close(self) -> None:
        """Release any resources held by the store (e.g. a Redis client).

        Must be safe to call even if the store holds no such resources
        (the in-memory store's ``close`` is a no-op).
        """
        ...


class InMemoryConversationStore:
    """Async facade over :class:`ConversationContext` -- the default/dev store.

    Ephemeral and single-process: messages are lost on restart and are not
    shared across replicas. This is the ``backend: memory`` selection (the
    schema default), so existing ``forge.yaml`` configs and callers that
    construct :class:`~forge_agent.agent.core.ForgeAgent` without wiring a
    store explicitly get exactly today's behavior, unchanged.

    Args:
        max_messages: Maximum number of messages to retain per session.
    """

    def __init__(self, max_messages: int = 50) -> None:
        self._context = ConversationContext(max_messages=max_messages)

    @property
    def max_messages(self) -> int:
        return self._context.max_messages

    async def add_messages(self, session_id: str, messages: list[ModelMessage]) -> None:
        self._context.add_messages(session_id, messages)

    async def get_messages(self, session_id: str) -> list[ModelMessage]:
        return self._context.get_messages(session_id)

    async def clear_session(self, session_id: str) -> None:
        self._context.clear_session(session_id)

    async def session_count(self) -> int:
        return self._context.session_count()

    async def message_count(self, session_id: str) -> int:
        return self._context.message_count(session_id)

    async def session_ids(self) -> list[str]:
        return self._context.session_ids()

    async def close(self) -> None:
        """No-op: the in-memory store holds no external resources."""
        return None


class RedisConversationStore:
    """Redis-backed conversation store (ADR-0003 WS-7).

    Each session's messages live in a Redis LIST at ``{key_prefix}{session_id}``,
    one JSON-encoded ``ModelMessage`` per element. Encoding/decoding goes
    through PydanticAI's own ``ModelMessagesTypeAdapter`` -- never pickle or
    a naive ``json.dumps`` -- so round-tripping a real ``ModelRequest`` /
    ``ModelResponse`` preserves its exact type and fields.

    Every append is a single pipelined RPUSH + LTRIM + (optional) EXPIRE:
    LTRIM enforces the sliding window server-side (keeping only the most
    recent ``max_messages`` entries), and EXPIRE refreshes the key's TTL so
    idle sessions age out naturally when ``ttl_seconds`` is configured.
    This makes the store safe to share across gateway replicas -- any
    replica reading a session sees the same durable history.

    Args:
        client: An already-constructed ``redis.asyncio.Redis`` client.
        max_messages: Maximum number of messages to retain per session.
        key_prefix: Prefix for every session's Redis key.
        ttl_seconds: Optional idle-session TTL, refreshed on every write.
            ``None`` (the default) means sessions never expire.
    """

    def __init__(
        self,
        client: redis_asyncio.Redis,
        *,
        max_messages: int = 50,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
        ttl_seconds: int | None = None,
    ) -> None:
        self._client = client
        self._max_messages = max_messages
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    @property
    def max_messages(self) -> int:
        return self._max_messages

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    @staticmethod
    def _encode(message: ModelMessage) -> bytes:
        """Encode a single ModelMessage as one Redis list element.

        ``ModelMessagesTypeAdapter`` adapts *lists* of messages (it is a
        discriminated union over ``ModelRequest``/``ModelResponse``), so a
        single message is wrapped in (and later unwrapped from) a
        one-element list to reuse PydanticAI's own serialization exactly.
        """
        return ModelMessagesTypeAdapter.dump_json([message])

    @staticmethod
    def _decode(raw: bytes | str) -> ModelMessage:
        return ModelMessagesTypeAdapter.validate_json(raw)[0]

    async def add_messages(self, session_id: str, messages: list[ModelMessage]) -> None:
        if not messages:
            return

        key = self._key(session_id)
        encoded = [self._encode(message) for message in messages]

        pipe = self._client.pipeline()
        pipe.rpush(key, *encoded)
        # Keep only the last `max_messages` elements -- negative indices
        # count from the tail, so this drops the oldest entries once the
        # list grows past the window, mirroring ConversationContext's
        # sliding-window semantics.
        pipe.ltrim(key, -self._max_messages, -1)
        if self._ttl_seconds is not None:
            pipe.expire(key, self._ttl_seconds)
        await pipe.execute()

    async def get_messages(self, session_id: str) -> list[ModelMessage]:
        raw_messages = await self._client.lrange(self._key(session_id), 0, -1)
        return [self._decode(raw) for raw in raw_messages]

    async def clear_session(self, session_id: str) -> None:
        await self._client.delete(self._key(session_id))

    async def message_count(self, session_id: str) -> int:
        count = await self._client.llen(self._key(session_id))
        return int(count)

    async def session_ids(self) -> list[str]:
        ids: list[str] = []
        async for raw_key in self._client.scan_iter(match=f"{self._key_prefix}*"):
            key_str = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
            ids.append(key_str[len(self._key_prefix) :])
        return ids

    async def session_count(self) -> int:
        return len(await self.session_ids())

    async def close(self) -> None:
        await self._client.aclose()


def build_conversation_store(
    config: ConversationStoreConfig,
    secret_resolver: SecretResolver,
) -> ConversationStore:
    """Build the ``ConversationStore`` selected by *config* (ADR-0003 WS-7).

    Args:
        config: The session/conversation store configuration
            (``ForgeConfig.conversation_store``).
        secret_resolver: Resolver used to resolve ``config.redis_url``
            when ``backend`` is ``redis``.

    Returns:
        An ``InMemoryConversationStore`` for ``backend: memory`` (the
        default), or a ``RedisConversationStore`` for ``backend: redis``.

    Raises:
        ValueError: If ``backend`` is ``redis`` but ``redis_url`` is not
            set. The schema's own validator already rejects this
            combination at config-load time; this is defence-in-depth for
            a ``ConversationStoreConfig`` built by other means (e.g.
            ``model_construct``, which bypasses validation).
    """
    if config.backend == ConversationStoreBackend.MEMORY:
        return InMemoryConversationStore(max_messages=config.max_messages)

    if config.redis_url is None:
        msg = "conversation_store.redis_url is required for the redis backend"
        raise ValueError(msg)

    redis_url = secret_resolver.resolve(config.redis_url)
    client = redis_asyncio.from_url(redis_url)
    return RedisConversationStore(
        client,
        max_messages=config.max_messages,
        key_prefix=config.key_prefix,
        ttl_seconds=config.ttl_seconds,
    )
