from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forge_security.oidc.user_tokens import (
    USER_TOKEN_ID_PREFIX,
    EscalationDeniedError,
    FileUserTokenStore,
    MintedToken,
    UserTokenRecord,
    UserTokenStoreUnavailableError,
    mint_user_token,
)


def _make_record(
    *,
    secret: bytes,
    owner_sub: str = "owner-sub",
    owner_email: str | None = None,
    label: str = "test-token",
    roles: list[str] | None = None,
    created_at: datetime,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    revoked_by: str | None = None,
) -> UserTokenRecord:
    return UserTokenRecord(
        id=f"{USER_TOKEN_ID_PREFIX}{hashlib.sha256(secret).hexdigest()[:24]}",
        secret_sha256=hashlib.sha256(secret).hexdigest(),
        owner_sub=owner_sub,
        owner_email=owner_email,
        label=label,
        roles=roles or [],
        created_at=created_at,
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_by=revoked_by,
    )


def _make_digest(secret: bytes) -> str:
    return hashlib.sha256(secret).hexdigest()


class TestUserTokenRecordValidation:
    def test_secret_sha256_must_be_64_char_hex_digest(self) -> None:
        with pytest.raises(ValueError, match="64-char hex SHA-256 digest"):
            UserTokenRecord(
                id=f"{USER_TOKEN_ID_PREFIX}badrecord",
                secret_sha256="not-a-valid-digest",
                owner_sub="owner-sub",
                label="bad-token",
                roles=[],
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )


class TestAddAndGet:
    async def test_add_then_get_by_digest_returns_record(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        secret = b"test-secret-1"
        record = _make_record(
            secret=secret,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        await store.add(record)

        digest = _make_digest(secret)
        retrieved = store.get_by_digest(digest)
        assert retrieved is not None
        assert retrieved.id == record.id
        assert retrieved.secret_sha256 == record.secret_sha256
        assert retrieved.owner_sub == record.owner_sub

    async def test_minted_token_persists_across_store_reload(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        now = datetime.now(UTC)

        # First store instance: add a record.
        store1 = FileUserTokenStore(store_path)
        await store1.load()

        secret = b"persist-test-secret"
        record = _make_record(
            secret=secret,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        await store1.add(record)

        digest = _make_digest(secret)

        # Second store instance, same path -- simulates a pod restart.
        store2 = FileUserTokenStore(store_path)
        await store2.load()

        assert store2.available
        retrieved = store2.get_by_digest(digest)
        assert retrieved is not None
        assert retrieved.id == record.id
        assert retrieved.secret_sha256 == record.secret_sha256


class TestRevoke:
    async def test_revoked_token_is_rejected(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        secret = b"revoke-test-secret"
        record = _make_record(
            secret=secret,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        await store.add(record)

        digest = _make_digest(secret)
        retrieved_before = store.get_by_digest(digest)
        assert retrieved_before is not None
        assert not retrieved_before.is_revoked

        result = await store.revoke(retrieved_before.id, by_owner="admin-sub")
        assert result is True

        retrieved_after = store.get_by_digest(digest)
        assert retrieved_after is not None
        assert retrieved_after.is_revoked
        assert retrieved_after.revoked_by == "admin-sub"

    async def test_revoke_unknown_id_returns_false(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        result = await store.revoke("unknown-id", by_owner="admin-sub")
        assert result is False

    async def test_revoke_already_revoked_returns_false_second_time(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        record = _make_record(
            secret=b"revoke-idempotent-secret",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        await store.add(record)

        result1 = await store.revoke(record.id, by_owner="admin-sub")
        assert result1 is True

        result2 = await store.revoke(record.id, by_owner="admin-sub")
        assert result2 is False


class TestExpiration:
    async def test_expired_record_is_rejected(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        past = now - timedelta(hours=1)
        future = now + timedelta(hours=1)

        record_past = _make_record(
            secret=b"expired-secret",
            created_at=past,
            expires_at=past,
        )
        assert record_past.is_expired(now)

        record_future = _make_record(
            secret=b"valid-secret",
            created_at=now,
            expires_at=future,
        )
        assert not record_future.is_expired(now)


class TestAtomicWrite:
    async def test_atomic_write_leaves_no_partial_file_on_simulated_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        record = _make_record(
            secret=b"atomic-test-secret",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        def mock_replace(src: str, dst: str) -> None:
            raise OSError("simulated crash during os.replace")

        monkeypatch.setattr(os, "replace", mock_replace)

        with pytest.raises(OSError):
            await store.add(record)

        # No partial/leftover temp file, and the target was never created.
        assert not store_path.exists()
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

        monkeypatch.undo()
        # A failed write is a transient I/O error, not corruption -- the
        # store must not be marked permanently unavailable by it.
        assert store.available


class TestConcurrency:
    async def test_concurrent_mint_and_revoke_do_not_corrupt_store(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)

        async def add_record(i: int) -> None:
            record = _make_record(
                secret=f"concurrent-secret-{i}".encode(),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
            await store.add(record)

        await asyncio.gather(*(add_record(i) for i in range(10)))

        all_records = store.list_all()
        assert len(all_records) == 10

        store2 = FileUserTokenStore(store_path)
        await store2.load()

        all_records2 = store2.list_all()
        assert len(all_records2) == 10

        for i in range(10):
            digest = _make_digest(f"concurrent-secret-{i}".encode())
            assert store2.get_by_digest(digest) is not None

    async def test_digest_index_swap_is_atomic_for_concurrent_readers(self, tmp_path: Path) -> None:
        store = FileUserTokenStore(tmp_path / "user_tokens.json")
        await store.load()

        digest = hashlib.sha256(b"swap-test-secret").hexdigest()
        now = datetime.now(UTC)
        record = UserTokenRecord(
            id=f"{USER_TOKEN_ID_PREFIX}swaptest",
            owner_sub="test-owner",
            label="swap-test",
            secret_sha256=digest,
            roles=["role1"],
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        results: list[UserTokenRecord | None] = []

        async def reader_task() -> None:
            for _ in range(200):
                result = store.get_by_digest(digest)
                results.append(result)
                await asyncio.sleep(0)

        reader = asyncio.create_task(reader_task())
        await store.add(record)
        await reader

        assert all(r is None or r.id == record.id for r in results)
        assert results[-1] is not None
        assert results[-1].id == record.id


class TestCorruptStore:
    async def test_corrupt_store_file_marks_unavailable_and_does_not_delete_it(
        self, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        garbage = b"not valid json at all \x00\x01\x02"
        store_path.write_bytes(garbage)

        store = FileUserTokenStore(store_path)
        await store.load()

        assert not store.available
        assert store_path.read_bytes() == garbage
        assert store.list_all() == []


class TestMissingStore:
    async def test_missing_store_file_starts_empty(self, tmp_path: Path) -> None:
        store_path = tmp_path / "nonexistent.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        assert store.available
        assert store.list_all() == []


class TestMintUserToken:
    async def test_mint_user_token_returns_raw_token_once_and_persists_only_digest(
        self, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        minted = await mint_user_token(
            store,
            owner_sub="owner-sub",
            owner_email="owner@example.com",
            label="test-mint",
            requested_roles=["read"],
            requested_permissions=frozenset({"config:read"}),
            minter_permissions=frozenset({"config:read", "config:write"}),
            ttl_seconds=3600,
            clock=lambda: now,
        )

        assert isinstance(minted, MintedToken)
        assert minted.token.startswith("forge_sk_")
        assert minted.id.startswith(USER_TOKEN_ID_PREFIX)

        file_content = store_path.read_bytes().decode()
        assert "forge_sk_" not in file_content
        assert minted.token not in file_content

        digest = hashlib.sha256(minted.token.encode()).hexdigest()
        assert digest in file_content

    async def test_mint_user_token_denies_escalation_when_requested_exceeds_minter(
        self, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        with pytest.raises(EscalationDeniedError) as exc_info:
            await mint_user_token(
                store,
                owner_sub="owner-sub",
                owner_email="owner@example.com",
                label="escalation-test",
                requested_roles=["admin"],
                requested_permissions=frozenset({"agent:invoke", "config:write"}),
                minter_permissions=frozenset({"agent:invoke"}),
                ttl_seconds=3600,
                clock=lambda: now,
            )

        assert exc_info.value.requested == frozenset({"agent:invoke", "config:write"})
        assert exc_info.value.allowed == frozenset({"agent:invoke"})

        # No partial record written on denial.
        assert store.list_all() == []

    async def test_mint_user_token_allows_subset_of_minter_permissions(
        self, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        minted = await mint_user_token(
            store,
            owner_sub="owner-sub",
            owner_email="owner@example.com",
            label="subset-test",
            requested_roles=["viewer"],
            requested_permissions=frozenset({"config:read"}),
            minter_permissions=frozenset({"config:read", "config:write"}),
            ttl_seconds=3600,
            clock=lambda: now,
        )

        assert minted.roles == ["viewer"]
        assert minted.label == "subset-test"

        retrieved = store.get_by_id(minted.id)
        assert retrieved is not None
        assert retrieved.owner_sub == "owner-sub"


class TestListForOwner:
    async def test_list_for_owner_scopes_by_owner_sub(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)

        record1 = _make_record(
            secret=b"owner1-secret",
            owner_sub="owner1",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        record2 = _make_record(
            secret=b"owner2-secret",
            owner_sub="owner2",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        await store.add(record1)
        await store.add(record2)

        owner1_records = store.list_for_owner("owner1")
        assert len(owner1_records) == 1
        assert owner1_records[0].owner_sub == "owner1"

        owner2_records = store.list_for_owner("owner2")
        assert len(owner2_records) == 1
        assert owner2_records[0].owner_sub == "owner2"

        owner3_records = store.list_for_owner("owner3")
        assert len(owner3_records) == 0

    async def test_list_all_returns_every_record(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store = FileUserTokenStore(store_path)
        await store.load()

        now = datetime.now(UTC)
        for i in range(3):
            record = _make_record(
                secret=f"list-all-secret-{i}".encode(),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
            await store.add(record)

        assert len(store.list_all()) == 3


class TestUnavailableStore:
    async def test_add_raises_unavailable_error_when_store_is_corrupt(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        store_path.write_bytes(b"garbage, not json")

        store = FileUserTokenStore(store_path)
        await store.load()
        assert not store.available

        now = datetime.now(UTC)
        record = _make_record(
            secret=b"unavailable-test-secret",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

        with pytest.raises(UserTokenStoreUnavailableError):
            await store.add(record)

    async def test_revoke_raises_unavailable_error_when_store_is_corrupt(
        self, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store_path.write_bytes(b"garbage, not json")

        store = FileUserTokenStore(store_path)
        await store.load()
        assert not store.available

        with pytest.raises(UserTokenStoreUnavailableError):
            await store.revoke("some-id", by_owner="admin-sub")
