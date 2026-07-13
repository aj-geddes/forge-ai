"""Tests for the ConversationStore abstraction (ADR-0003 WS-7).

Covers the async in-memory facade, the Redis-backed store (against
fakeredis -- no real Redis server required), and the config-driven
factory that selects between them.
"""

from __future__ import annotations

import datetime

import fakeredis.aioredis as fakeredis_aioredis
import pytest
from forge_agent.agent.store import (
    ConversationStore,
    InMemoryConversationStore,
    RedisConversationStore,
    build_conversation_store,
)
from forge_config.schema import (
    ConversationStoreBackend,
    ConversationStoreConfig,
    SecretRef,
    SecretSource,
)
from forge_config.secret_resolver import SecretResolver
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart


class FakeSecretResolver:
    """A minimal SecretResolver returning predefined values, for testing."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}

    def resolve(self, ref: SecretRef) -> str:
        return self._secrets[ref.name]


_check: SecretResolver = FakeSecretResolver()


def _make_request(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _make_response(content: str) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=content)],
        model_name="test-model",
        timestamp=datetime.datetime.now(datetime.UTC),
    )


class TestInMemoryConversationStore:
    """The default/dev fallback -- an async facade over ConversationContext."""

    async def test_satisfies_conversation_store_protocol(self) -> None:
        store = InMemoryConversationStore()
        assert isinstance(store, ConversationStore)

    async def test_default_max_messages(self) -> None:
        store = InMemoryConversationStore()
        assert store.max_messages == 50

    async def test_custom_max_messages(self) -> None:
        store = InMemoryConversationStore(max_messages=3)
        assert store.max_messages == 3

    async def test_add_and_get_messages_round_trip(self) -> None:
        store = InMemoryConversationStore()
        req = _make_request("hello")
        resp = _make_response("hi there")

        await store.add_messages("s1", [req, resp])

        messages = await store.get_messages("s1")
        assert messages == [req, resp]

    async def test_sliding_window_drops_oldest(self) -> None:
        store = InMemoryConversationStore(max_messages=2)
        await store.add_messages("s1", [_make_request("a")])
        await store.add_messages("s1", [_make_request("b")])
        await store.add_messages("s1", [_make_request("c")])

        messages = await store.get_messages("s1")
        assert len(messages) == 2
        assert messages[0].parts[0].content == "b"  # type: ignore[union-attr]
        assert messages[1].parts[0].content == "c"  # type: ignore[union-attr]

    async def test_clear_session(self) -> None:
        store = InMemoryConversationStore()
        await store.add_messages("s1", [_make_request("a")])

        await store.clear_session("s1")

        assert await store.get_messages("s1") == []

    async def test_message_count(self) -> None:
        store = InMemoryConversationStore()
        await store.add_messages("s1", [_make_request("a"), _make_request("b")])

        assert await store.message_count("s1") == 2

    async def test_session_count(self) -> None:
        store = InMemoryConversationStore()
        await store.add_messages("s1", [_make_request("a")])
        await store.add_messages("s2", [_make_request("b")])

        assert await store.session_count() == 2

    async def test_session_ids(self) -> None:
        store = InMemoryConversationStore()
        await store.add_messages("s1", [_make_request("a")])
        await store.add_messages("s2", [_make_request("b")])

        assert sorted(await store.session_ids()) == ["s1", "s2"]

    async def test_close_is_a_noop(self) -> None:
        store = InMemoryConversationStore()
        await store.close()  # must not raise


class TestRedisConversationStore:
    """Backed by fakeredis's asyncio client -- no real Redis server needed."""

    @pytest.fixture
    def client(self) -> fakeredis_aioredis.FakeRedis:
        return fakeredis_aioredis.FakeRedis()

    async def test_satisfies_conversation_store_protocol(
        self, client: fakeredis_aioredis.FakeRedis
    ) -> None:
        store = RedisConversationStore(client)
        assert isinstance(store, ConversationStore)

    async def test_max_messages(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client, max_messages=7)
        assert store.max_messages == 7

    async def test_add_and_get_messages_round_trip_preserves_real_model_messages(
        self, client: fakeredis_aioredis.FakeRedis
    ) -> None:
        """Messages must round-trip via PydanticAI's ModelMessagesTypeAdapter,
        not pickle or naive json.dumps."""
        store = RedisConversationStore(client)
        req = _make_request("hello")
        resp = _make_response("hi there")

        await store.add_messages("s1", [req, resp])

        messages = await store.get_messages("s1")
        assert messages == [req, resp]
        assert isinstance(messages[0], ModelRequest)
        assert isinstance(messages[1], ModelResponse)

    async def test_sliding_window_enforced_via_ltrim(
        self, client: fakeredis_aioredis.FakeRedis
    ) -> None:
        store = RedisConversationStore(client, max_messages=2)

        await store.add_messages("s1", [_make_request("a")])
        await store.add_messages("s1", [_make_request("b")])
        await store.add_messages("s1", [_make_request("c")])

        messages = await store.get_messages("s1")
        assert len(messages) == 2
        assert messages[0].parts[0].content == "b"  # type: ignore[union-attr]
        assert messages[1].parts[0].content == "c"  # type: ignore[union-attr]
        assert await store.message_count("s1") == 2

    async def test_ttl_is_set_on_write(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client, ttl_seconds=120, key_prefix="forge:session:")

        await store.add_messages("s1", [_make_request("a")])

        ttl = await client.ttl("forge:session:s1")
        assert 0 < ttl <= 120

    async def test_no_ttl_by_default(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client, key_prefix="forge:session:")

        await store.add_messages("s1", [_make_request("a")])

        ttl = await client.ttl("forge:session:s1")
        assert ttl == -1  # no expiry set

    async def test_clear_session(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client)
        await store.add_messages("s1", [_make_request("a")])

        await store.clear_session("s1")

        assert await store.get_messages("s1") == []
        assert await store.message_count("s1") == 0

    async def test_message_count_empty_session(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client)
        assert await store.message_count("nonexistent") == 0

    async def test_session_ids_reflects_key_prefix(
        self, client: fakeredis_aioredis.FakeRedis
    ) -> None:
        store = RedisConversationStore(client, key_prefix="forge:session:")
        await store.add_messages("s1", [_make_request("a")])
        await store.add_messages("s2", [_make_request("b")])

        assert sorted(await store.session_ids()) == ["s1", "s2"]

    async def test_session_count(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client)
        await store.add_messages("s1", [_make_request("a")])
        await store.add_messages("s2", [_make_request("b")])

        assert await store.session_count() == 2

    async def test_add_messages_empty_list_is_a_noop(
        self, client: fakeredis_aioredis.FakeRedis
    ) -> None:
        store = RedisConversationStore(client)
        await store.add_messages("s1", [])
        assert await store.message_count("s1") == 0

    async def test_close_closes_the_client(self, client: fakeredis_aioredis.FakeRedis) -> None:
        store = RedisConversationStore(client)
        await store.close()  # must not raise


class TestBuildConversationStore:
    """The config-driven factory (ADR-0003 WS-7)."""

    def test_memory_backend_returns_in_memory_store(self) -> None:
        config = ConversationStoreConfig(backend=ConversationStoreBackend.MEMORY)

        store = build_conversation_store(config, _check)

        assert isinstance(store, InMemoryConversationStore)

    def test_memory_backend_honors_max_messages(self) -> None:
        config = ConversationStoreConfig(backend=ConversationStoreBackend.MEMORY, max_messages=7)

        store = build_conversation_store(config, _check)

        assert store.max_messages == 7

    def test_redis_backend_returns_redis_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_client = fakeredis_aioredis.FakeRedis()
        monkeypatch.setattr(
            "forge_agent.agent.store.redis_asyncio.from_url", lambda url: fake_client
        )
        config = ConversationStoreConfig(
            backend=ConversationStoreBackend.REDIS,
            redis_url=SecretRef(source=SecretSource.ENV, name="MY_REDIS_URL"),
        )
        resolver = FakeSecretResolver({"MY_REDIS_URL": "redis://localhost:6379/0"})

        store = build_conversation_store(config, resolver)

        assert isinstance(store, RedisConversationStore)

    def test_redis_backend_without_url_raises(self) -> None:
        config = ConversationStoreConfig.model_construct(
            backend=ConversationStoreBackend.REDIS,
            redis_url=None,
            key_prefix="forge:session:",
            ttl_seconds=None,
            max_messages=50,
        )

        with pytest.raises(ValueError, match="redis_url"):
            build_conversation_store(config, _check)
