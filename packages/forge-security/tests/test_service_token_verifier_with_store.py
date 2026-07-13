"""Tests for ServiceTokenVerifier's optional dynamic UserTokenStore path
(ADR-0002 SS4.4): static tokens are checked first (unchanged), then an
O(1) digest lookup against the dynamic store.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from forge_config.schema import ServiceToken
from forge_security.oidc.errors import AuthError
from forge_security.oidc.service_tokens import ServiceTokenVerifier
from forge_security.oidc.user_tokens import UserTokenRecord


class FakeUserTokenStore:
    """A minimal in-memory test double for the store's read path -- avoids
    filesystem I/O in verifier-level tests."""

    def __init__(self, records: dict[str, UserTokenRecord]) -> None:
        self._records = records

    def get_by_digest(self, digest: str) -> UserTokenRecord | None:
        return self._records.get(digest)


def _make_user_token_record(
    raw_token: str,
    *,
    token_id: str,
    owner_sub: str = "user_123",
    owner_email: str | None = None,
    label: str = "test token",
    roles: list[str] | None = None,
    created_at: datetime,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    revoked_by: str | None = None,
) -> UserTokenRecord:
    if roles is None:
        roles = ["read"]
    secret_sha256 = hashlib.sha256(raw_token.encode()).hexdigest()
    return UserTokenRecord(
        id=token_id,
        secret_sha256=secret_sha256,
        owner_sub=owner_sub,
        owner_email=owner_email,
        label=label,
        roles=roles,
        created_at=created_at,
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_by=revoked_by,
    )


def _now() -> datetime:
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def static_token() -> ServiceToken:
    raw = "forge_sk_static-ci_AAAA"
    return ServiceToken(
        id="static-token-1",
        secret_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        roles=["admin"],
        expires_at=None,
    )


@pytest.fixture
def dynamic_token_raw() -> str:
    return "forge_sk_u_dyn1_BBBB"


@pytest.fixture
def dynamic_token_record(dynamic_token_raw: str) -> UserTokenRecord:
    return _make_user_token_record(
        raw_token=dynamic_token_raw,
        token_id="dynamic-token-1",
        owner_sub="user_456",
        label="dynamic test token",
        roles=["write", "read"],
        created_at=_now(),
        expires_at=_now().replace(year=2030),
    )


def test_verifier_accepts_both_static_and_dynamic_tokens(
    static_token: ServiceToken,
    dynamic_token_raw: str,
    dynamic_token_record: UserTokenRecord,
) -> None:
    store = FakeUserTokenStore(
        {hashlib.sha256(dynamic_token_raw.encode()).hexdigest(): dynamic_token_record}
    )
    verifier = ServiceTokenVerifier(
        tokens=[static_token],
        store=store,
        clock=lambda: _now(),
    )

    static_raw = "forge_sk_static-ci_AAAA"
    principal = verifier.verify(static_raw)
    assert principal.token_id == "static-token-1"
    assert principal.roles == ["admin"]

    principal = verifier.verify(dynamic_token_raw)
    assert principal.token_id == "dynamic-token-1"
    assert principal.roles == ["write", "read"]


def test_static_token_still_verified_when_store_present(
    static_token: ServiceToken,
) -> None:
    store = FakeUserTokenStore({})
    verifier = ServiceTokenVerifier(
        tokens=[static_token],
        store=store,
        clock=lambda: _now(),
    )
    static_raw = "forge_sk_static-ci_AAAA"
    principal = verifier.verify(static_raw)
    assert principal.token_id == "static-token-1"
    assert principal.roles == ["admin"]


def test_dynamic_revoked_token_rejected_401_invalid_token(
    dynamic_token_raw: str,
) -> None:
    revoked_at = _now()
    record = _make_user_token_record(
        raw_token=dynamic_token_raw,
        token_id="revoked-token",
        owner_sub="user_789",
        label="revoked token",
        created_at=_now(),
        expires_at=_now().replace(year=2030),
        revoked_at=revoked_at,
        revoked_by="admin_user",
    )
    store = FakeUserTokenStore({hashlib.sha256(dynamic_token_raw.encode()).hexdigest(): record})
    verifier = ServiceTokenVerifier(tokens=[], store=store, clock=lambda: _now())

    with pytest.raises(AuthError) as exc_info:
        verifier.verify(dynamic_token_raw)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "invalid_token"


def test_dynamic_expired_token_rejected_401_token_expired(
    dynamic_token_raw: str,
) -> None:
    expired_at = _now().replace(year=2020)
    record = _make_user_token_record(
        raw_token=dynamic_token_raw,
        token_id="expired-token",
        owner_sub="user_111",
        label="expired token",
        created_at=_now(),
        expires_at=expired_at,
    )
    store = FakeUserTokenStore({hashlib.sha256(dynamic_token_raw.encode()).hexdigest(): record})
    verifier = ServiceTokenVerifier(tokens=[], store=store, clock=lambda: _now())

    with pytest.raises(AuthError) as exc_info:
        verifier.verify(dynamic_token_raw)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "token_expired"


def test_dynamic_token_resolves_to_correct_token_id_and_roles(
    dynamic_token_raw: str,
) -> None:
    record = _make_user_token_record(
        raw_token=dynamic_token_raw,
        token_id="valid-token",
        owner_sub="user_222",
        label="valid token",
        roles=["read", "write", "delete"],
        created_at=_now(),
        expires_at=_now().replace(year=2030),
    )
    store = FakeUserTokenStore({hashlib.sha256(dynamic_token_raw.encode()).hexdigest(): record})
    verifier = ServiceTokenVerifier(tokens=[], store=store, clock=lambda: _now())

    principal = verifier.verify(dynamic_token_raw)
    assert principal.token_id == "valid-token"
    assert principal.roles == ["read", "write", "delete"]


def test_verifier_without_store_behaves_exactly_as_before() -> None:
    verifier = ServiceTokenVerifier(tokens=[], store=None, clock=lambda: _now())
    unknown_token = "forge_sk_unknown_XXXX"

    with pytest.raises(AuthError) as exc_info:
        verifier.verify(unknown_token)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "invalid_token"


def test_unknown_digest_with_store_present_still_401_invalid_token(
    dynamic_token_raw: str,
) -> None:
    record = _make_user_token_record(
        raw_token=dynamic_token_raw,
        token_id="some-token",
        owner_sub="user_333",
        label="some token",
        created_at=_now(),
        expires_at=_now().replace(year=2030),
    )
    store = FakeUserTokenStore({hashlib.sha256(dynamic_token_raw.encode()).hexdigest(): record})
    verifier = ServiceTokenVerifier(tokens=[], store=store, clock=lambda: _now())
    unknown_token = "forge_sk_unknown_YYYY"

    with pytest.raises(AuthError) as exc_info:
        verifier.verify(unknown_token)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "invalid_token"
