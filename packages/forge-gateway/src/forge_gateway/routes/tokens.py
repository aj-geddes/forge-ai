"""User-issued (self-service) API key endpoints (ADR-0002 SS5).

A logged-in Dex user mints, lists, and revokes their own ``forge_sk_...``
service tokens here. All three routes resolve the caller through the same
bypass-free :func:`forge_gateway.security.get_principal` dependency every
other protected route uses -- there is no separate credential path.

Enforcement this module owns (the core in
``forge_security.oidc.user_tokens`` deliberately does not, per its module
docstring):

* **No chaining** (ADR-0002 SS6.2): only ``principal.kind == "user"`` (a
  session cookie or an OIDC bearer JWT) may mint. A service-token
  principal minting another token would let a leaked token outlive its
  own revocation.
* **Unbound-caller guard**: a principal with zero roles/permissions is
  authenticated but not authorized for anything -- minting would only
  ever produce a useless token, so it is rejected outright rather than
  silently succeeding.
* **Anti-escalation** (ADR-0002 SS6.1): requested roles are expanded to
  permissions via the *live* :class:`~forge_security.oidc.Authorizer`
  (resolving the ``admin`` wildcard correctly) and handed to
  :func:`~forge_security.oidc.user_tokens.mint_user_token` as
  ``requested_permissions``, which raises
  :class:`~forge_security.oidc.user_tokens.EscalationDeniedError` unless
  they are a subset of the *minter's own current* permissions.
* **Unknown-role rejection**, **TTL bounds**, and **label validation** --
  request-shape concerns the core has no request/config context for.
* **Ownership**: list defaults to the caller's own tokens; revoke only
  the caller's own token. An admin (holds every permission in the closed
  set) may pass ``?all=true`` to list across owners, or revoke any
  token. A non-owner, non-admin revoke -- or an unknown id -- returns
  ``404`` (never ``403``), so token ids cannot be probed by enumeration
  (ADR-0002 SS5.3).
* **Fail closed**: the feature being disabled
  (``security.service_tokens.user_tokens.enabled: false``) makes every
  route respond ``404`` as if it did not exist; a corrupt/unavailable
  store (ADR-0002 SS4.7) makes every route respond
  ``503 token_store_unavailable`` rather than minting, listing, or
  revoking against a store that failed to load safely.
* **Audit**: ``token_minted``/``token_revoked`` are logged at INFO with
  the owner's ``sub`` and the token's ``id`` -- never the raw secret or
  its digest (the gateway has no other audit sink wired in as of
  ADR-0001; see the module docstring of ``forge_gateway.security``).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from forge_config.schema import Permission
from forge_security.oidc.user_tokens import (
    EscalationDeniedError,
    UserTokenStoreUnavailableError,
    mint_user_token,
)

from forge_gateway import security
from forge_gateway.models import (
    MintTokenRequest,
    MintTokenResponse,
    TokenListItem,
    TokenListResponse,
)

if TYPE_CHECKING:
    from forge_security.oidc import Principal
    from forge_security.oidc.user_tokens import UserTokenRecord, UserTokenStore

logger = logging.getLogger("forge.gateway.routes.tokens")

router = APIRouter(tags=["tokens"])

# ADR-0002 SS7.2: the hard floor on a user-minted token's TTL -- mirrors
# forge_config.schema's private _USER_TOKEN_MIN_TTL_SECONDS. Duplicated
# (rather than imported) because that name is a module-private constant
# of forge-config, not part of its public schema surface.
_MIN_TTL_SECONDS = 3600

# label: 1-64 chars, printable ASCII (space through '~'). Deliberately
# excludes control characters (including newlines) -- a label is
# rendered as-is in the UI's token list and in audit log lines.
_LABEL_RE = re.compile(r"^[\x20-\x7e]{1,64}$")

_ALL_PERMISSIONS = frozenset(p.value for p in Permission)


def _is_admin(principal: Principal) -> bool:
    """ADR-0002 SS6.3: admin-scope operations (``?all=true``, revoking
    another owner's token) are gated on holding the *full* permission
    set -- a role-name-independent check, not a check for a role literally
    named ``"admin"``."""
    return principal.permissions >= _ALL_PERMISSIONS


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def _store_unavailable() -> JSONResponse:
    return JSONResponse({"error": "token_store_unavailable"}, status_code=503)


def _feature_store(request: Request) -> tuple[UserTokenStore | None, JSONResponse | None]:  # noqa: ARG001
    """Resolve the live store for this request, or the error response to
    return instead.

    Order matters: the feature being disabled must behave as if the
    route does not exist at all (``404``, checked *before* resolving the
    caller's identity -- ADR-0002 SS5's "so local/PVC-less deploys don't
    expose a broken endpoint"); a corrupt/unavailable store is a
    ``503`` -- the feature exists but cannot currently serve requests.
    """
    config = security.get_user_token_config()
    if config is None or not config.enabled:
        return None, _not_found()
    store = security.get_user_token_store()
    if store is None or not store.available:
        return None, _store_unavailable()
    return store, None


def _active_token_count(store: UserTokenStore, owner_sub: str) -> int:
    """Count *owner_sub*'s ACTIVE tokens -- neither revoked nor expired as
    of now. Revoked/expired tombstones are excluded so a user who revokes
    stale keys can always mint fresh ones (ADR-0002's cap is on live
    exposure, not lifetime mint count); this is the same predicate a
    future tombstone-compaction pass would use, just not persisted here."""
    now = datetime.now(UTC)
    return sum(
        1
        for record in store.list_for_owner(owner_sub)
        if not record.is_revoked and not record.is_expired(now)
    )


def _serialize_record(record: UserTokenRecord, *, include_owner_email: bool) -> TokenListItem:
    return TokenListItem(
        id=record.id,
        label=record.label,
        roles=list(record.roles),
        created_at=record.created_at.isoformat(),
        expires_at=record.expires_at.isoformat(),
        revoked_at=record.revoked_at.isoformat() if record.revoked_at else None,
        owner_email=record.owner_email if include_owner_email else None,
    )


@router.post("/v1/auth/tokens", status_code=201, response_model=None)
async def mint_token(request: Request, body: MintTokenRequest) -> JSONResponse:
    """Mint a new user-issued API key (ADR-0002 SS5.1).

    Returns the raw ``forge_sk_...`` token exactly once, in the ``201``
    body -- it is not recoverable afterward by any endpoint.
    """
    store, error = _feature_store(request)
    if error is not None:
        return error
    assert store is not None  # noqa: S101 -- narrowed by _feature_store

    principal = await security.get_principal(request)

    if principal.kind != "user":
        # ADR-0002 SS6.2: no chaining -- a leaked service token must never
        # be able to mint a fresh, independently-lived replacement.
        return JSONResponse({"error": "service_tokens_cannot_mint"}, status_code=403)

    if not principal.permissions:
        # ADR-0002 SS6.1: an authenticated-but-unbound caller (zero roles)
        # can only ever mint a useless token; reject outright.
        return JSONResponse({"error": "forbidden"}, status_code=403)

    label = body.label
    if not _LABEL_RE.fullmatch(label):
        return JSONResponse({"error": "invalid_label"}, status_code=400)

    user_token_config = security.get_user_token_config()
    assert user_token_config is not None  # noqa: S101 -- narrowed by _feature_store
    ttl_seconds = (
        body.ttl_seconds if body.ttl_seconds is not None else user_token_config.default_ttl_seconds
    )
    if not (_MIN_TTL_SECONDS <= ttl_seconds <= user_token_config.max_ttl_seconds):
        return JSONResponse({"error": "ttl_out_of_range"}, status_code=400)

    requested_roles = list(body.roles) if body.roles is not None else list(principal.roles)
    authorization_config = security.get_authorization_config()
    known_roles = set(authorization_config.roles) if authorization_config is not None else set()
    for role in requested_roles:
        if role not in known_roles:
            return JSONResponse({"error": "unknown_role"}, status_code=400)

    authorizer = security.get_authorizer()
    assert authorizer is not None  # noqa: S101 -- invariant: set whenever get_principal succeeded
    requested_permissions = authorizer.permissions_for_roles(requested_roles)

    if _active_token_count(store, principal.sub) >= user_token_config.max_tokens_per_owner:
        # DoS guard (security review finding): a single owner minting
        # unbounded tokens fills the shared PVC store and slows the
        # single-global-lock rewrite-the-whole-document write path for
        # every caller. Applies uniformly, including to admins -- there is
        # no exemption, so the limit is simple and always honored.
        return JSONResponse(
            {
                "error": "too_many_tokens",
                "detail": (
                    "you already have the maximum of "
                    f"{user_token_config.max_tokens_per_owner} active token(s); "
                    "revoke one before minting another"
                ),
            },
            status_code=403,
        )

    try:
        minted = await mint_user_token(
            store,
            owner_sub=principal.sub,
            owner_email=principal.email,
            label=label,
            requested_roles=requested_roles,
            requested_permissions=requested_permissions,
            minter_permissions=principal.permissions,
            ttl_seconds=ttl_seconds,
        )
    except EscalationDeniedError as exc:
        return JSONResponse({"error": "escalation_denied", "detail": str(exc)}, status_code=403)
    except UserTokenStoreUnavailableError:
        return _store_unavailable()

    logger.info(
        "token_minted owner_sub=%s token_id=%s label=%s roles=%s expires_at=%s",
        principal.sub,
        minted.id,
        minted.label,
        minted.roles,
        minted.expires_at.isoformat(),
    )

    response = MintTokenResponse(
        id=minted.id,
        token=minted.token,
        label=minted.label,
        roles=minted.roles,
        created_at=minted.created_at.isoformat(),
        expires_at=minted.expires_at.isoformat(),
    )
    return JSONResponse(response.model_dump(), status_code=201)


@router.get("/v1/auth/tokens", response_model=None)
async def list_tokens(request: Request, all: bool = False) -> JSONResponse:  # noqa: A002
    """List the caller's own user-issued API keys, metadata only -- the
    secret/digest is never present in any field (ADR-0002 SS5.2).

    ``?all=true`` additionally requires the caller to hold every
    permission in the closed set (ADR-0002 SS6.3); a non-admin passing
    ``all=true`` is silently scoped back to their own tokens rather than
    erroring, so the parameter can never be used to probe admin status.
    """
    store, error = _feature_store(request)
    if error is not None:
        return error
    assert store is not None  # noqa: S101 -- narrowed by _feature_store

    principal = await security.get_principal(request)

    admin_view = all and _is_admin(principal)
    records = store.list_all() if admin_view else store.list_for_owner(principal.sub)

    response = TokenListResponse(
        tokens=[_serialize_record(r, include_owner_email=admin_view) for r in records]
    )
    return JSONResponse(response.model_dump())


@router.delete("/v1/auth/tokens/{token_id}", status_code=204, response_model=None)
async def revoke_token(token_id: str, request: Request) -> Response:
    """Revoke a user-issued API key, effective immediately on the very
    next request against the live store (ADR-0002 SS7.3).

    Owner or admin only. A non-owner, non-admin caller, or an unknown
    ``token_id``, gets ``404`` -- never ``403`` -- so ids cannot be
    probed by enumeration (ADR-0002 SS5.3).
    """
    store, error = _feature_store(request)
    if error is not None:
        return error
    assert store is not None  # noqa: S101 -- narrowed by _feature_store

    principal = await security.get_principal(request)

    record = store.get_by_id(token_id)
    if record is None:
        return _not_found()
    if record.owner_sub != principal.sub and not _is_admin(principal):
        return _not_found()

    try:
        revoked = await store.revoke(token_id, by_owner=principal.sub)
    except UserTokenStoreUnavailableError:
        return _store_unavailable()

    if not revoked:
        # Already revoked (or vanished) between the lookup above and this
        # call -- idempotent-ish, no information leak (ADR-0002 SS5.3).
        return _not_found()

    logger.info(
        "token_revoked token_id=%s revoked_by=%s owner_sub=%s",
        token_id,
        principal.sub,
        record.owner_sub,
    )
    return Response(status_code=204)
