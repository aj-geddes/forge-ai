"""BFF session-cookie codec (ADR-0001 SS4.1).

The session cookie is a stateless, authenticated-encrypted blob
(``MultiFernet`` -- AES-128-CBC + HMAC-SHA256, with a built-in signature)
containing only identity claims copied out of a validated ID token at
login time. There is deliberately no access token, refresh token, or ID
token in the blob: the ``id_token`` is verified once at ``/auth/callback``
and then discarded (ADR-0001 SS4.1).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from forge_security.oidc.errors import AuthError

_SESSION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SessionData:
    """The decoded contents of a session cookie -- identity claims only."""

    v: int
    sub: str
    email: str | None
    name: str | None
    preferred_username: str | None
    groups: list[str] = field(default_factory=list)
    # Security-review finding #2 (HIGH): whether the ID token's
    # email_verified claim was True at login time (/auth/callback). The
    # session-cookie authorization path re-derives roles from this stored
    # snapshot on every request (Authorizer.roles_for is claims-driven, not
    # a one-time decision) -- see resolver.py's session-cookie branch.
    # Defaults False (fail-safe) for cookies minted before this field
    # existed.
    email_verified: bool = False
    iat: int = 0
    exp: int = 0
    sid: str = ""


class SessionCodec:
    """Encrypts/decrypts the ``forge_session`` cookie payload.

    Parameters
    ----------
    key:
        The primary Fernet key (urlsafe-base64, 32 raw bytes). New
        sessions are always encrypted with this key.
    previous_key:
        An optional prior Fernet key. Sessions encrypted under it still
        decrypt successfully, enabling zero-downtime key rotation
        (ADR-0001 SS8.5): deploy with ``previous_key`` set to the old
        key, wait out the session lifetime, then drop it.
    lifetime_seconds:
        Absolute session lifetime, enforced against the embedded ``iat``.
    idle_timeout_seconds:
        Sliding idle timeout, enforced against the embedded ``iat`` (the
        caller is expected to re-issue the cookie with a fresh ``iat`` on
        each authenticated request to implement the sliding window).
    clock:
        Injectable clock (seconds since epoch) for deterministic tests.
    """

    def __init__(
        self,
        *,
        key: bytes,
        previous_key: bytes | None = None,
        lifetime_seconds: int = 28800,
        idle_timeout_seconds: int = 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        keys = [Fernet(key)]
        if previous_key is not None:
            keys.append(Fernet(previous_key))
        self._fernet = MultiFernet(keys)
        self._lifetime_seconds = lifetime_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._clock = clock

    def encode(
        self,
        *,
        sub: str,
        email: str | None,
        name: str | None,
        preferred_username: str | None,
        groups: list[str],
        email_verified: bool = False,
    ) -> tuple[str, str]:
        """Encrypt a new session and return ``(cookie_value, sid)``.

        *email_verified* is the ID token's ``email_verified`` claim at
        login time (ADR-0001 finding #2) -- it gates whether *email* may
        later be used to match an email-based role binding. Defaults
        ``False`` (fail-safe): callers must explicitly assert a verified
        email.
        """
        now = int(self._clock())
        sid = str(uuid.uuid4())
        payload = {
            "v": _SESSION_SCHEMA_VERSION,
            "sub": sub,
            "email": email,
            "name": name,
            "preferred_username": preferred_username,
            "groups": groups,
            "email_verified": email_verified,
            "iat": now,
            "exp": now + self._lifetime_seconds,
            "sid": sid,
        }
        token = self._fernet.encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")
        return token, sid

    def decode(self, cookie_value: str) -> SessionData:
        """Decrypt and validate *cookie_value*, or raise ``AuthError``.

        Raises
        ------
        AuthError
            ``401 invalid_session`` if the ciphertext fails to decrypt or
            authenticate (tampered, wrong key, garbage input); ``401
            session_expired`` if the absolute lifetime or idle timeout has
            elapsed.
        """
        try:
            raw = self._fernet.decrypt(cookie_value.encode("ascii"))
        except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
            raise AuthError(401, "invalid_session") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthError(401, "invalid_session") from exc

        try:
            data = SessionData(
                v=payload["v"],
                sub=payload["sub"],
                email=payload.get("email"),
                name=payload.get("name"),
                preferred_username=payload.get("preferred_username"),
                groups=list(payload.get("groups", [])),
                email_verified=bool(payload.get("email_verified", False)),
                iat=payload["iat"],
                exp=payload["exp"],
                sid=payload.get("sid", ""),
            )
        except (KeyError, TypeError) as exc:
            raise AuthError(401, "invalid_session") from exc

        now = int(self._clock())
        if now > data.exp:
            raise AuthError(401, "session_expired")
        if now - data.iat > self._idle_timeout_seconds:
            raise AuthError(401, "session_expired")

        return data
