"""User-issued (self-service) API keys: persistence, mint, and lifecycle
(ADR-0002).

A minted token is a ``forge_sk_<id>_<43-char base64url secret>`` credential
-- structurally identical to a static ADR-0001 service token -- backed by a
Longhorn-PVC JSON file instead of ``forge.yaml``. This module owns:

* :class:`UserTokenRecord` -- the persisted, digest-only record shape.
* :class:`FileUserTokenStore` -- load-at-startup, atomic-write,
  lock-free-read storage behind the narrow protocol described in ADR-0002
  SS4.6 (``get_by_digest``, ``get_by_id``, ``list_for_owner``,
  ``list_all``, ``add``, ``revoke``, ``load``), so a future Redis-backed
  implementation is a drop-in swap.
* :func:`mint_user_token` -- entropy generation and the load-bearing
  anti-escalation check (ADR-0002 SS6.1).

Security-critical properties enforced here, not just documented:

* The raw token is generated with :func:`secrets.token_urlsafe` (a CSPRNG)
  and returned exactly once, by value, from :func:`mint_user_token`. It is
  never written to disk, logged, or reconstructable from what *is* written
  -- only its SHA-256 digest is persisted (see ``UserTokenRecord``).
* :func:`mint_user_token` refuses to mint (and never calls
  ``store.add``) unless ``requested_permissions`` is a subset of
  ``minter_permissions`` -- the check happens before any token material is
  generated.
* Every mutation (:meth:`FileUserTokenStore.add`,
  :meth:`FileUserTokenStore.revoke`) is serialised through a single
  ``asyncio.Lock`` and persisted via temp-file + ``fsync`` + ``os.replace``
  (atomic rename on POSIX) so a crash mid-write can never leave a torn
  file. Readers (``get_by_digest`` and friends) never take the lock --
  they read whichever dict reference is currently assigned, which is
  always fully built before assignment (ADR-0002 SS4.5).

What this module deliberately does NOT enforce (left to forge-gateway,
which holds the request/config context this module does not):

* That only ``kind="user"`` principals may call :func:`mint_user_token`
  (ADR-0002 SS6.2, "no chaining") -- this module has no ``Principal``.
* Unknown-role rejection, TTL bounds (``UserTokenConfig``), and label
  validation -- all config/request-shape concerns.
* Owner-vs-admin authorization for who may list/revoke which records --
  the store answers "what is record X" and "whose are these", not "is the
  caller allowed to act on it".
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field, field_validator

from forge_security.oidc.service_tokens import SERVICE_TOKEN_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("forge.security.oidc.user_tokens")

#: Reserved id namespace for dynamically-minted tokens (ADR-0002 SS7.1).
#: ``ServiceTokenConfig`` (forge-config) rejects any statically configured
#: token id that collides with this prefix, so the two namespaces are
#: disjoint by construction.
USER_TOKEN_ID_PREFIX = "u_"  # noqa: S105 -- a public prefix, not a secret

_STORE_VERSION = 1
_TOKEN_SECRET_BYTES = 32  # 256 bits -> secrets.token_urlsafe(32) == 43 chars
_TOKEN_ID_RANDOM_BYTES = 10  # 20 hex chars of id entropy, beyond the prefix
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class UserTokenRecord(BaseModel):
    """The persisted shape of one user-minted token (ADR-0002 SS4.3).

    Only ``secret_sha256`` is stored -- never the raw token. A SHA-256
    digest of a 256-bit random value is not brute-forceable and carries no
    exploitable material, the same property that lets static token digests
    live in ``forge.yaml`` in git (ADR-0001 SS5).
    """

    id: str
    secret_sha256: str
    owner_sub: str
    owner_email: str | None = None
    label: str
    roles: list[str] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @field_validator("secret_sha256")
    @classmethod
    def validate_sha256_hex(cls, v: str) -> str:
        if not _SHA256_HEX_RE.fullmatch(v):
            msg = f"UserTokenRecord.secret_sha256 must be a 64-char hex SHA-256 digest, got {v!r}"
            raise ValueError(msg)
        return v

    @property
    def is_revoked(self) -> bool:
        """``True`` once a tombstone (``revoked_at``) has been written."""
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        """``True`` if *now* is past this record's ``expires_at``."""
        return now > self.expires_at


class _UserTokenStoreDocument(BaseModel):
    """The on-disk JSON document shape: ``{"version": 1, "tokens": [...]}``."""

    version: int = _STORE_VERSION
    tokens: list[UserTokenRecord] = Field(default_factory=list)


class UserTokenStore(Protocol):
    """The narrow storage-backend contract (ADR-0002 SS4.6).

    :class:`FileUserTokenStore` is the only implementation today; a future
    ``RedisUserTokenStore`` (the documented multi-replica escape hatch,
    TD-006) implements this same protocol with no change required to
    :class:`~forge_security.oidc.service_tokens.ServiceTokenVerifier` or
    to :func:`mint_user_token`.
    """

    async def load(self) -> None: ...
    def get_by_digest(self, digest: str) -> UserTokenRecord | None: ...
    def get_by_id(self, token_id: str) -> UserTokenRecord | None: ...
    def list_for_owner(self, owner_sub: str) -> list[UserTokenRecord]: ...
    def list_all(self) -> list[UserTokenRecord]: ...
    async def add(self, record: UserTokenRecord) -> None: ...
    async def revoke(self, token_id: str, *, by_owner: str) -> bool: ...

    @property
    def available(self) -> bool: ...


class UserTokenStoreUnavailableError(Exception):
    """Raised by a mutating store operation when the on-disk file was
    found corrupt/unparseable at :meth:`FileUserTokenStore.load` time
    (ADR-0002 SS4.7). The caller (forge-gateway) should surface this as
    ``503 token_store_unavailable`` -- static tokens and OIDC are
    unaffected; only the dynamic-store feature is degraded."""


class EscalationDeniedError(Exception):
    """Raised by :func:`mint_user_token` when the requested permissions
    are not a subset of the minter's current permissions (ADR-0002 SS6.1,
    load-bearing). Raised *before* any token material is generated or
    persisted -- a denied mint leaves the store untouched."""

    def __init__(self, requested: frozenset[str], allowed: frozenset[str]) -> None:
        self.requested = requested
        self.allowed = allowed
        super().__init__(
            "requested permissions exceed the minter's permissions: "
            f"requested={sorted(requested)} allowed={sorted(allowed)}"
        )


class FileUserTokenStore:
    """Longhorn-PVC-backed JSON file store for user-minted tokens
    (ADR-0002 SS4).

    Parameters
    ----------
    store_path:
        Path to the JSON document, e.g. ``/app/data/user_tokens.json``
        (``security.service_tokens.user_tokens.store_path``).
    clock:
        Injectable UTC-``datetime`` clock, for deterministic tests.
    """

    def __init__(
        self,
        store_path: str | Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = Path(store_path)
        self._clock = clock
        self._lock = asyncio.Lock()
        # Readers consult these dicts without the lock: every mutation
        # builds a brand-new dict and only then reassigns the attribute,
        # so a reader always observes a fully-built old or new mapping,
        # never a partially-mutated one (ADR-0002 SS4.5).
        self._index: dict[str, UserTokenRecord] = {}
        self._by_id: dict[str, UserTokenRecord] = {}
        self._available = True

    @property
    def available(self) -> bool:
        """``False`` if :meth:`load` found the on-disk file corrupt.
        Mutating calls (``add``/``revoke``) raise
        :class:`UserTokenStoreUnavailableError` while this is ``False``."""
        return self._available

    async def load(self) -> None:
        """Read the store file at startup.

        A **missing** file is not an error -- the store starts empty (the
        first mint creates the file). A **corrupt/unparseable** file fails
        safe: logs at ERROR, leaves the file untouched for forensics, and
        marks the store unavailable. This method never raises and never
        deletes or rewrites a file it could not parse (ADR-0002 SS4.7).
        """

        def _read() -> _UserTokenStoreDocument | None:
            if not self._path.exists():
                return None
            raw = self._path.read_text(encoding="utf-8")
            return _UserTokenStoreDocument.model_validate_json(raw)

        try:
            doc = await asyncio.to_thread(_read)
        except (OSError, ValueError) as exc:
            logger.error(
                "user token store at %s is corrupt or unreadable (%s) -- leaving the "
                "file untouched for forensics; the store is now unavailable "
                "(mint/list/revoke will fail closed; static tokens and OIDC are "
                "unaffected)",
                self._path,
                exc,
            )
            self._index = {}
            self._by_id = {}
            self._available = False
            return

        if doc is None:
            logger.info("user token store file %s does not exist yet -- starting empty", self._path)
            doc = _UserTokenStoreDocument()

        self._index = {r.secret_sha256: r for r in doc.tokens}
        self._by_id = {r.id: r for r in doc.tokens}
        self._available = True

    def get_by_digest(self, digest: str) -> UserTokenRecord | None:
        """O(1) lookup by SHA-256 digest (lock-free read).

        Safe without a constant-time comparison: the lookup key is a
        digest, which is non-reversible, so a timing side-channel that
        reveals "this digest is present" leaks nothing an attacker can use
        (ADR-0002 SS4.4).
        """
        return self._index.get(digest)

    def get_by_id(self, token_id: str) -> UserTokenRecord | None:
        """Lookup by store id (lock-free read)."""
        return self._by_id.get(token_id)

    def list_for_owner(self, owner_sub: str) -> list[UserTokenRecord]:
        """All records (including revoked/expired tombstones) owned by
        *owner_sub*."""
        return [r for r in self._by_id.values() if r.owner_sub == owner_sub]

    def list_all(self) -> list[UserTokenRecord]:
        """Every record in the store (an admin-scoped view, per caller)."""
        return list(self._by_id.values())

    async def add(self, record: UserTokenRecord) -> None:
        """Persist *record* atomically and publish it to readers.

        Raises
        ------
        UserTokenStoreUnavailableError
            If :attr:`available` is ``False`` (the on-disk file was found
            corrupt at :meth:`load`).
        """
        if not self._available:
            raise UserTokenStoreUnavailableError(str(self._path))
        async with self._lock:
            new_by_id = {**self._by_id, record.id: record}
            new_index = {**self._index, record.secret_sha256: record}
            await self._write_atomic(list(new_by_id.values()))
            self._by_id = new_by_id
            self._index = new_index

    async def revoke(self, token_id: str, *, by_owner: str) -> bool:
        """Tombstone the record with *token_id*.

        Idempotent: returns ``False`` (no error) if *token_id* is unknown
        or already revoked. Returns ``True`` and persists
        ``revoked_at``/``revoked_by`` otherwise. *by_owner* is the acting
        principal's ``sub`` (owner self-revoke or admin), recorded for
        audit -- this method does not itself check who is allowed to
        revoke *token_id*; that owner-vs-admin authorization decision
        belongs to the caller (forge-gateway), which returns ``404`` (not
        ``403``) for an unauthorized attempt to avoid id enumeration
        (ADR-0002 SS5.3).

        Raises
        ------
        UserTokenStoreUnavailableError
            If :attr:`available` is ``False``.
        """
        if not self._available:
            raise UserTokenStoreUnavailableError(str(self._path))
        async with self._lock:
            record = self._by_id.get(token_id)
            if record is None or record.is_revoked:
                return False
            updated = record.model_copy(
                update={"revoked_at": self._clock(), "revoked_by": by_owner}
            )
            new_by_id = {**self._by_id, token_id: updated}
            new_index = {**self._index, updated.secret_sha256: updated}
            await self._write_atomic(list(new_by_id.values()))
            self._by_id = new_by_id
            self._index = new_index
            return True

    async def _write_atomic(self, records: list[UserTokenRecord]) -> None:
        """Serialise *records* to the store file atomically.

        Writes to a temp file in the *same directory*, ``flush()`` +
        ``os.fsync()``, then ``os.replace`` (atomic rename on POSIX). A
        crash or exception mid-write leaves either the previous complete
        file or the new complete file -- never a torn one -- and the temp
        file is removed on any failure. Must be called while holding
        ``self._lock`` (ADR-0002 SS4.5).
        """
        document = _UserTokenStoreDocument(version=_STORE_VERSION, tokens=records)
        payload = document.model_dump_json(indent=2)
        directory = self._path.parent
        target = self._path

        def _write() -> None:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".user_tokens-", suffix=".tmp", dir=str(directory)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, target)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise

        await asyncio.to_thread(_write)


@dataclass(frozen=True)
class MintedToken:
    """The one-time result of :func:`mint_user_token`.

    ``token`` is the raw bearer credential (``forge_sk_<id>_<secret>``)
    and is never recoverable after this call returns -- only its digest
    was persisted to the store.
    """

    id: str
    token: str
    label: str
    roles: list[str]
    created_at: datetime
    expires_at: datetime


async def mint_user_token(
    store: UserTokenStore,
    *,
    owner_sub: str,
    owner_email: str | None,
    label: str,
    requested_roles: list[str],
    requested_permissions: frozenset[str],
    minter_permissions: frozenset[str],
    ttl_seconds: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MintedToken:
    """Mint a new user-issued service token (ADR-0002 SS6, SS7.1).

    Anti-escalation (SS6.1, load-bearing): raises
    :class:`EscalationDeniedError` unless ``requested_permissions`` is a
    subset of ``minter_permissions`` -- checked *before* any token
    material is generated or ``store.add`` is called, so a denied request
    never touches the store. ``requested_permissions`` is expected to
    already be the wildcard-resolved expansion of ``requested_roles``
    (i.e. the caller ran them through
    :meth:`~forge_security.oidc.authorizer.Authorizer.permissions_for_roles`);
    this function does not itself know about role -> permission mappings.

    The caller (forge-gateway) is responsible for everything this
    function does *not* check:

    * That the minter is a ``kind="user"`` principal (ADR-0002 SS6.2, "no
      chaining" -- a service token must never mint another).
    * Rejecting unknown role names (``400 unknown_role``).
    * ``ttl_seconds`` within the configured min/default/max bounds
      (``400 ttl_out_of_range``) -- this function applies whatever
      ``ttl_seconds`` it is given verbatim.
    * ``label`` length/charset validation (``400 invalid_label``).
    """
    if not requested_permissions <= minter_permissions:
        raise EscalationDeniedError(requested_permissions, minter_permissions)

    now = clock()
    token_id = f"{USER_TOKEN_ID_PREFIX}{secrets.token_hex(_TOKEN_ID_RANDOM_BYTES)}"
    secret = secrets.token_urlsafe(_TOKEN_SECRET_BYTES)
    raw_token = f"{SERVICE_TOKEN_PREFIX}{token_id}_{secret}"
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = now + timedelta(seconds=ttl_seconds)
    roles = list(requested_roles)

    record = UserTokenRecord(
        id=token_id,
        secret_sha256=digest,
        owner_sub=owner_sub,
        owner_email=owner_email,
        label=label,
        roles=roles,
        created_at=now,
        expires_at=expires_at,
    )
    await store.add(record)

    return MintedToken(
        id=token_id,
        token=raw_token,
        label=label,
        roles=roles,
        created_at=now,
        expires_at=expires_at,
    )
