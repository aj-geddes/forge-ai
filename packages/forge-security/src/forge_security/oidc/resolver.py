"""The single bypass-free credential resolver (ADR-0001 SS5.1).

::

    resolve_principal(...) -> Principal        # raises AuthError(401/...)

      1. If a session cookie is present  -> session path      (kind="user")
      2. Elif Authorization: Bearer <t>:
           a. t startswith "forge_sk_"                -> service-token path (kind="service")
           b. t has 3 dot-separated segments (a JWS)   -> OIDC bearer path   (kind="user")
           c. otherwise                                -> 401 invalid_credential_format
      3. Else -> 401 missing_credentials

There is no fallthrough branch. ``X-Caller-ID`` and a ``caller_id`` query
parameter are deleted as authentication inputs by construction: this
function's signature has no parameter that could carry them (ADR-0001
SS5.1, SS7.4 #4). Callers (forge-gateway) are responsible for extracting
``session_cookie`` and ``authorization_header`` from the framework
request; this module stays framework-agnostic.
"""

from __future__ import annotations

from forge_security.oidc.authorizer import Authorizer
from forge_security.oidc.errors import AuthError
from forge_security.oidc.principal import Principal
from forge_security.oidc.service_tokens import SERVICE_TOKEN_PREFIX, ServiceTokenVerifier
from forge_security.oidc.session import SessionCodec
from forge_security.oidc.verifier import OIDCTokenVerifier

_JWS_SEGMENT_COUNT = 3  # header.payload.signature


async def resolve_principal(
    *,
    session_cookie: str | None,
    authorization_header: str | None,
    session_codec: SessionCodec,
    service_token_verifier: ServiceTokenVerifier,
    oidc_verifier: OIDCTokenVerifier | None,
    authorizer: Authorizer,
) -> Principal:
    """Resolve a request's credentials to a verified :class:`Principal`.

    Raises
    ------
    AuthError
        Always -- there is no return path for an unverified credential.
        ``401 missing_credentials`` (with ``WWW-Authenticate: Bearer``) if
        neither a session cookie nor an ``Authorization`` header is
        present; ``401 invalid_credential_format`` if an ``Authorization``
        header is present but is neither a recognised service token nor a
        JWS-shaped bearer token; otherwise whatever the matched
        verifier/codec raises (401s for bad credentials, 503 for an
        unreachable identity provider with a cold cache).
    """
    if session_cookie:
        data = session_codec.decode(session_cookie)
        roles = authorizer.roles_for(groups=data.groups, email=data.email, sub=data.sub)
        permissions = authorizer.permissions_for_roles(roles)
        return Principal(
            kind="user",
            sub=data.sub,
            email=data.email,
            name=data.name,
            groups=data.groups,
            roles=roles,
            permissions=permissions,
        )

    if authorization_header:
        scheme, _, credential = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            raise AuthError(401, "invalid_credential_format")

        if credential.startswith(SERVICE_TOKEN_PREFIX):
            info = service_token_verifier.verify(credential)
            permissions = authorizer.permissions_for_roles(info.roles)
            return Principal(
                kind="service",
                sub=f"svc:{info.token_id}",
                roles=info.roles,
                permissions=permissions,
                token_id=info.token_id,
            )

        if credential.count(".") == _JWS_SEGMENT_COUNT - 1:
            if oidc_verifier is None:
                raise AuthError(401, "invalid_credential_format")
            claims = await oidc_verifier.verify(credential)
            groups = list(claims.get("groups", []))
            email = claims.get("email")
            sub = claims["sub"]
            roles = authorizer.roles_for(groups=groups, email=email, sub=sub)
            permissions = authorizer.permissions_for_roles(roles)
            return Principal(
                kind="user",
                sub=sub,
                email=email,
                name=claims.get("name"),
                groups=groups,
                roles=roles,
                permissions=permissions,
            )

        raise AuthError(401, "invalid_credential_format")

    raise AuthError(401, "missing_credentials", headers={"WWW-Authenticate": "Bearer"})
