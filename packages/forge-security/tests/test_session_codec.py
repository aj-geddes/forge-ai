"""Tests for forge_security.oidc.session.SessionCodec (ADR-0001 SS4.1)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from forge_security.oidc.errors import AuthError
from forge_security.oidc.session import SessionCodec, SessionData


class _FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _make_codec(
    *,
    key: bytes | None = None,
    previous_key: bytes | None = None,
    lifetime_seconds: int = 28800,
    idle_timeout_seconds: int = 3600,
    clock: _FakeClock | None = None,
) -> SessionCodec:
    return SessionCodec(
        key=key or Fernet.generate_key(),
        previous_key=previous_key,
        lifetime_seconds=lifetime_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        clock=clock or _FakeClock(),
    )


def _encode(codec: SessionCodec, **overrides: object) -> tuple[str, str]:
    fields: dict[str, object] = {
        "sub": "CgdhamdlZGRlcxIGZ2l0aHVi",
        "email": "ageddes75@gmail.com",
        "name": "AJ Geddes",
        "preferred_username": "aj-geddes",
        "groups": ["hvs-platform:admins"],
    }
    fields.update(overrides)
    return codec.encode(**fields)  # type: ignore[arg-type]


class TestRoundtrip:
    def test_roundtrip_encrypt_decrypt(self):
        codec = _make_codec()
        token, sid = _encode(codec)

        data = codec.decode(token)

        assert data.sub == "CgdhamdlZGRlcxIGZ2l0aHVi"
        assert data.email == "ageddes75@gmail.com"
        assert data.groups == ["hvs-platform:admins"]
        assert data.sid == sid

    def test_session_blob_contains_no_tokens(self):
        """The cookie must never carry an access/refresh/id token -- only
        identity claims (ADR-0001 SS4.1)."""
        codec = _make_codec()
        token, _sid = _encode(codec)

        data = codec.decode(token)

        for forbidden_field in ("access_token", "refresh_token", "id_token"):
            assert not hasattr(data, forbidden_field)


class TestTamperingAndWrongKey:
    def test_tampered_ciphertext_raises_invalid_session(self):
        codec = _make_codec()
        token, _sid = _encode(codec)
        tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]

        with pytest.raises(AuthError) as exc:
            codec.decode(tampered)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_session"

    def test_cookie_from_a_different_key_raises_invalid_session(self):
        codec_a = _make_codec()
        codec_b = _make_codec()  # different random key
        token, _sid = _encode(codec_a)

        with pytest.raises(AuthError) as exc:
            codec_b.decode(token)

        assert exc.value.code == "invalid_session"

    def test_garbage_string_raises_invalid_session(self):
        codec = _make_codec()

        with pytest.raises(AuthError) as exc:
            codec.decode("not-a-valid-fernet-token")

        assert exc.value.code == "invalid_session"


class TestExpiry:
    def test_absolute_expiry_enforced(self):
        clock = _FakeClock()
        codec = _make_codec(lifetime_seconds=100, idle_timeout_seconds=1000, clock=clock)
        token, _sid = _encode(codec)

        clock.advance(101)

        with pytest.raises(AuthError) as exc:
            codec.decode(token)
        assert exc.value.code == "session_expired"

    def test_idle_timeout_enforced(self):
        clock = _FakeClock()
        codec = _make_codec(lifetime_seconds=100_000, idle_timeout_seconds=60, clock=clock)
        token, _sid = _encode(codec)

        clock.advance(61)  # past idle timeout, well within absolute lifetime

        with pytest.raises(AuthError) as exc:
            codec.decode(token)
        assert exc.value.code == "session_expired"

    def test_within_both_windows_is_valid(self):
        clock = _FakeClock()
        codec = _make_codec(lifetime_seconds=28800, idle_timeout_seconds=3600, clock=clock)
        token, _sid = _encode(codec)

        clock.advance(1800)  # 30 min: within both windows

        data = codec.decode(token)
        assert data.sub


class TestKeyRotation:
    def test_previous_key_decrypts_but_new_key_encrypts(self):
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        old_codec = _make_codec(key=old_key)
        token_from_old, _sid = _encode(old_codec)

        rotated_codec = _make_codec(key=new_key, previous_key=old_key)

        # A session minted under the old (now "previous") key still decodes.
        data = rotated_codec.decode(token_from_old)
        assert data.sub

        # But newly-encoded sessions use the new primary key only -- a
        # codec that only knows the old key can no longer read them.
        new_token, _sid2 = _encode(rotated_codec)
        old_only_codec = _make_codec(key=old_key)
        with pytest.raises(AuthError):
            old_only_codec.decode(new_token)


class TestPayloadCorruption:
    def test_decrypted_payload_that_is_not_json_raises_invalid_session(self):
        codec = _make_codec()
        # Encrypt arbitrary non-JSON bytes with the same Fernet key so it
        # decrypts successfully but fails at the JSON-parsing stage.
        garbage_ciphertext = codec._fernet.encrypt(b"not-json-at-all").decode("ascii")

        with pytest.raises(AuthError) as exc:
            codec.decode(garbage_ciphertext)

        assert exc.value.code == "invalid_session"

    def test_decrypted_payload_missing_required_field_raises_invalid_session(self):
        import json

        codec = _make_codec()
        incomplete_payload = json.dumps({"v": 1, "sub": "s"})  # missing iat/exp
        ciphertext = codec._fernet.encrypt(incomplete_payload.encode("utf-8")).decode("ascii")

        with pytest.raises(AuthError) as exc:
            codec.decode(ciphertext)

        assert exc.value.code == "invalid_session"


class TestSessionDataShape:
    def test_session_data_is_frozen_and_typed(self):
        data = SessionData(
            v=1,
            sub="s",
            email="e@example.com",
            name="N",
            preferred_username="u",
            groups=["g"],
            iat=0,
            exp=1,
            sid="sid-1",
        )
        assert data.v == 1
        with pytest.raises(Exception):  # noqa: B017, PT011 -- frozen dataclass
            data.sub = "other"  # type: ignore[misc]
