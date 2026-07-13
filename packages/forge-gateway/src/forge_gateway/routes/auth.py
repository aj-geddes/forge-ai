"""OIDC browser login routes (ADR-0001 SS9.1): the BFF authorization-code +
PKCE flow. No OAuth token ever reaches the browser -- the gateway performs
the code exchange server-side and issues its own encrypted, httpOnly
session cookie.

**Sanctioned deviation from the ADR sketch (documented, per the developer
brief this module was built from):** the registered Dex client
``forge-ai`` is a **public** client -- verified live, no ``client_secret``
-- not the confidential client the ADR SS9.1 step 6c sketch assumes.
HTTP Basic client authentication at the token endpoint does not apply.
The token exchange is instead authenticated with the PKCE
``code_verifier`` alone (RFC 7636 SS4.5): ``client_id`` and
``code_verifier`` are sent in the form body, no ``Authorization`` header
and no client secret. This is exactly as strong as PKCE is designed to
be for a public client, and is the only option this client registration
supports.

``/auth/login`` and ``/auth/callback`` also carry an IP-keyed rate limit
(:func:`forge_gateway.rate_limit.enforce_auth_flow_rate_limit`) -- a
security-review finding: this pair is the documented abuse vector for
hammering ``/auth/callback`` with garbage authorization codes, and no
principal exists yet at this point in the flow to key an identity-based
limit on. ``/auth/logout`` and ``/v1/auth/me`` are not limited here:
``logout`` only clears local cookies (negligible cost, no upstream calls),
and ``/v1/auth/me`` already goes through the identity-keyed limiter via
``get_principal``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections import OrderedDict
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from forge_security.oidc import AuthError

from forge_gateway import rate_limit, security

logger = logging.getLogger("forge.gateway.routes.auth")

router = APIRouter(tags=["auth"])

_TX_COOKIE_NAME = "forge_oidc_tx"
_TX_COOKIE_PATH = "/auth"
_TX_COOKIE_MAX_AGE = 600  # 10 minutes (ADR-0001 SS9.1 step 4c)
_NEXT_PATH_RE = re.compile(r"^/[A-Za-z0-9/_-]*$")
_CODE_VERIFIER_ENTROPY_BYTES = 32  # -> 43-char urlsafe-b64, within RFC 7636's 43-128 range

# Same-pod single-use guard for the transaction cookie (ADR-0001 SS9.1 step
# 6h: "single-use"). This is a bounded, in-process mitigation only -- it
# does not stop replay across replicas, which would require a shared store
# the ADR explicitly declines to introduce (SS3 decision driver #3). A
# stolen tx cookie is only useful with a still-valid Dex authorization code
# for the *same* state, which Dex itself single-uses server-side.
_MAX_TRACKED_TX_IDS = 4096
_used_tx_ids: OrderedDict[str, float] = OrderedDict()


def _remember_tx_id(tx_id: str) -> bool:
    """Return True if *tx_id* is new (and record it); False if already seen."""
    if tx_id in _used_tx_ids:
        return False
    _used_tx_ids[tx_id] = time.time()
    if len(_used_tx_ids) > _MAX_TRACKED_TX_IDS:
        _used_tx_ids.popitem(last=False)
    return True


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` per RFC 7636."""
    verifier = _b64url(secrets.token_bytes(_CODE_VERIFIER_ENTROPY_BYTES))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _safe_next_path(raw: str | None) -> str:
    """Only ever return a same-origin relative path (open-redirect defence,
    ADR-0001 SS9.1 step 4a)."""
    if raw and not raw.startswith("//") and _NEXT_PATH_RE.fullmatch(raw):
        return raw
    oidc_config = security.get_oidc_config()
    return oidc_config.post_login_default_path if oidc_config is not None else "/"


def _oidc_unavailable() -> JSONResponse:
    return JSONResponse({"error": "oidc_not_configured"}, status_code=503)


def _encrypt_tx(
    fernet: Fernet,
    *,
    tx_id: str,
    state: str,
    nonce: str,
    code_verifier: str,
    next_path: str,
) -> str:
    payload = {
        "tx_id": tx_id,
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "next": next_path,
    }
    return fernet.encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")


def _decrypt_tx(fernet: Fernet, cookie_value: str) -> dict[str, str]:
    try:
        raw = fernet.decrypt(cookie_value.encode("ascii"), ttl=_TX_COOKIE_MAX_AGE)
        data: dict[str, str] = json.loads(raw)
    except (InvalidToken, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthError(400, "invalid_transaction") from exc
    for key in ("tx_id", "state", "nonce", "code_verifier", "next"):
        if key not in data:
            raise AuthError(400, "invalid_transaction")
    return data


def _clear_cookie(response: RedirectResponse | JSONResponse, name: str, *, path: str) -> None:
    response.delete_cookie(name, path=path)


@router.get(
    "/auth/login",
    response_model=None,
    dependencies=[Depends(rate_limit.enforce_auth_flow_rate_limit)],
)
async def login(request: Request) -> RedirectResponse | JSONResponse:
    """Start the authorization-code + PKCE flow: redirect to Dex."""
    oidc_config = security.get_oidc_config()
    endpoints = security.get_endpoints()
    tx_key = security.get_tx_key()
    if (
        oidc_config is None
        or endpoints is None
        or not endpoints.authorization_endpoint
        or tx_key is None
    ):
        return _oidc_unavailable()
    assert endpoints is not None  # noqa: S101 -- narrowed by the check above, for mypy

    next_path = _safe_next_path(request.query_params.get("next"))
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce_pair()
    tx_id = secrets.token_urlsafe(16)

    fernet = Fernet(tx_key)
    tx_cookie_value = _encrypt_tx(
        fernet,
        tx_id=tx_id,
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        next_path=next_path,
    )

    query = {
        "response_type": "code",
        "client_id": oidc_config.client_id,
        "redirect_uri": oidc_config.redirect_uri,
        "scope": " ".join(oidc_config.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{endpoints.authorization_endpoint}?{urlencode(query)}"

    response = RedirectResponse(url=authorize_url, status_code=302)
    response.set_cookie(
        _TX_COOKIE_NAME,
        tx_cookie_value,
        max_age=_TX_COOKIE_MAX_AGE,
        path=_TX_COOKIE_PATH,
        httponly=True,
        secure=oidc_config.session.secure,
        samesite="lax",
    )
    return response


async def _exchange_code(
    *,
    http_client: httpx.AsyncClient,
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str,
) -> dict[str, object]:
    """Exchange *code* at Dex's token endpoint.

    Authenticated with the PKCE ``code_verifier`` alone -- ``forge-ai`` is
    a public client, so there is no ``client_secret`` to send and no HTTP
    Basic auth header (see module docstring).
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    try:
        response = await http_client.post(token_endpoint, data=form, timeout=10.0)
        response.raise_for_status()
        result: dict[str, object] = response.json()
    except Exception as exc:
        raise AuthError(502, "token_exchange_failed") from exc
    return result


@router.get(
    "/auth/callback",
    response_model=None,
    dependencies=[Depends(rate_limit.enforce_auth_flow_rate_limit)],
)
async def callback(request: Request) -> RedirectResponse | JSONResponse:
    """Complete the authorization-code + PKCE flow: exchange the code,
    verify the ``id_token``, and mint the session cookie."""
    oidc_config = security.get_oidc_config()
    endpoints = security.get_endpoints()
    tx_key = security.get_tx_key()
    http_client = security.get_http_client()
    oidc_verifier = security.get_oidc_verifier()
    session_codec = security.get_session_codec()
    authorizer = security.get_authorizer()

    if (
        oidc_config is None
        or endpoints is None
        or not endpoints.token_endpoint
        or tx_key is None
        or http_client is None
        or oidc_verifier is None
        or session_codec is None
        or authorizer is None
    ):
        return _oidc_unavailable()

    tx_cookie_value = request.cookies.get(_TX_COOKIE_NAME)
    if not tx_cookie_value:
        return JSONResponse({"error": "invalid_transaction"}, status_code=400)

    fernet = Fernet(tx_key)
    try:
        tx = _decrypt_tx(fernet, tx_cookie_value)
    except AuthError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    if not _remember_tx_id(tx["tx_id"]):
        return JSONResponse({"error": "transaction_replayed"}, status_code=400)

    query_state = request.query_params.get("state", "")
    if not hmac.compare_digest(query_state, tx["state"]):
        return JSONResponse({"error": "state_mismatch"}, status_code=400)

    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error": "missing_code"}, status_code=400)

    try:
        token_response = await _exchange_code(
            http_client=http_client,
            token_endpoint=endpoints.token_endpoint,
            code=code,
            redirect_uri=oidc_config.redirect_uri,
            code_verifier=tx["code_verifier"],
            client_id=oidc_config.client_id,
        )
    except AuthError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    id_token = token_response.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        return JSONResponse({"error": "token_exchange_failed"}, status_code=502)

    try:
        claims = await oidc_verifier.verify(id_token, nonce=tx["nonce"])
    except AuthError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    sub = claims["sub"]
    email = claims.get("email")
    name = claims.get("name")
    preferred_username = claims.get("preferred_username")
    groups = list(claims.get("groups", []))

    roles = authorizer.roles_for(groups=groups, email=email, sub=sub)
    if not roles:
        forbidden_response = JSONResponse({"error": "forbidden"}, status_code=403)
        _clear_cookie(forbidden_response, _TX_COOKIE_NAME, path=_TX_COOKIE_PATH)
        return forbidden_response

    session_cookie_value, _sid = session_codec.encode(
        sub=sub,
        email=email,
        name=name,
        preferred_username=preferred_username,
        groups=groups,
    )
    csrf_token = secrets.token_urlsafe(32)

    response: RedirectResponse | JSONResponse = RedirectResponse(url=tx["next"], status_code=302)
    response.set_cookie(
        oidc_config.session.cookie_name,
        session_cookie_value,
        max_age=oidc_config.session.lifetime_seconds,
        path="/",
        httponly=True,
        secure=oidc_config.session.secure,
        samesite="lax",
    )
    response.set_cookie(
        oidc_config.session.csrf_cookie_name,
        csrf_token,
        max_age=oidc_config.session.lifetime_seconds,
        path="/",
        httponly=False,
        secure=oidc_config.session.secure,
        samesite="lax",
    )
    _clear_cookie(response, _TX_COOKIE_NAME, path=_TX_COOKIE_PATH)
    return response


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the local Forge session. Dex's own SSO session is untouched --
    Dex exposes no ``end_session_endpoint`` (ADR-0001 SS4.1)."""
    oidc_config = security.get_oidc_config()
    cookie_name = oidc_config.session.cookie_name if oidc_config else security.get_cookie_name()
    csrf_cookie_name = oidc_config.session.csrf_cookie_name if oidc_config else "forge_csrf"

    response = JSONResponse(
        {
            "status": "logged_out",
            "message": "Signed out of Forge (your Dex SSO session persists).",
        }
    )
    _clear_cookie(response, cookie_name, path="/")
    _clear_cookie(response, csrf_cookie_name, path="/")
    return response


@router.get("/v1/auth/me")
async def me(request: Request) -> JSONResponse:
    """Return the resolved caller's claims, roles, and permissions
    (ADR-0001 SS6.4) -- used at rollout to empirically discover the
    GitHub connector's ``groups`` claim format."""
    from forge_gateway.security import get_principal

    principal = await get_principal(request)
    return JSONResponse(
        {
            "kind": principal.kind,
            "sub": principal.sub,
            "email": principal.email,
            "name": principal.name,
            "groups": principal.groups,
            "roles": principal.roles,
            "permissions": sorted(principal.permissions),
            "token_id": principal.token_id,
        }
    )
