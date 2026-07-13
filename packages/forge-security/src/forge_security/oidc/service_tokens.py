"""Forge-issued service-token verification (ADR-0001 SS5).

Machine clients (MCP, A2A peers, CI) authenticate with an opaque bearer
token of the form ``forge_sk_<token_id>_<43 chars base64url>``. Only the
SHA-256 hex digest of the full token is ever stored in config; comparison
against the presented token is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from forge_config.schema import ServiceToken

from forge_security.oidc.errors import AuthError

if TYPE_CHECKING:
    from forge_security.oidc.user_tokens import UserTokenStore

SERVICE_TOKEN_PREFIX = "forge_sk_"  # noqa: S105 -- a public prefix, not a secret


@dataclass(frozen=True)
class ServiceTokenPrincipalInfo:
    """The resolved identity of a verified service token."""

    token_id: str
    roles: list[str]


class ServiceTokenVerifier:
    """Verifies ``forge_sk_...`` bearer credentials against configured
    :class:`~forge_config.schema.ServiceToken` entries.

    Parameters
    ----------
    tokens:
        The configured service tokens (``security.service_tokens.tokens``).
    clock:
        Injectable UTC-``datetime`` clock, for deterministic expiry tests.
    store:
        Optional dynamic store of user-minted tokens (ADR-0002 SS4.4). When
        present, a token that does not match any static entry is looked up
        by digest in the store; a live reference means mint/revoke take
        effect on the very next request, with no reload. ``None``
        preserves the exact static-only behaviour of ADR-0001.
    """

    def __init__(
        self,
        tokens: Iterable[ServiceToken],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        store: UserTokenStore | None = None,
    ) -> None:
        self._tokens = list(tokens)
        self._clock = clock
        self._store = store

    def verify(self, presented_token: str) -> ServiceTokenPrincipalInfo:
        """Verify *presented_token* and return its principal info.

        Raises
        ------
        AuthError
            ``401 invalid_credential_format`` if the token does not carry
            the ``forge_sk_`` prefix; ``401 invalid_token`` if it does not
            match any configured token (constant-time comparison against
            every configured digest) nor any non-revoked dynamic record in
            an attached store; ``401 token_expired`` if the matched
            static token's or dynamic record's ``expires_at`` has passed.
            A revoked dynamic record is reported as ``401 invalid_token``
            (not a distinct ``token_revoked`` code -- ADR-0002 SS4.4).
        """
        if not presented_token.startswith(SERVICE_TOKEN_PREFIX):
            raise AuthError(401, "invalid_credential_format")

        presented_digest = hashlib.sha256(presented_token.encode("utf-8")).hexdigest()

        matched: ServiceToken | None = None
        for candidate in self._tokens:
            if hmac.compare_digest(presented_digest, candidate.secret_sha256):
                matched = candidate
                break

        if matched is not None:
            if matched.expires_at is not None and self._clock() > matched.expires_at:
                raise AuthError(401, "token_expired")
            return ServiceTokenPrincipalInfo(token_id=matched.id, roles=list(matched.roles))

        if self._store is not None:
            record = self._store.get_by_digest(presented_digest)
            if record is not None:
                if record.revoked_at is not None:
                    raise AuthError(401, "invalid_token")
                if record.expires_at is not None and self._clock() > record.expires_at:
                    raise AuthError(401, "token_expired")
                return ServiceTokenPrincipalInfo(token_id=record.id, roles=list(record.roles))

        raise AuthError(401, "invalid_token")
