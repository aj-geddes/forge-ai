"""The single exception type through which authentication failures flow.

Every rejection in ``forge_security.oidc`` -- expired tokens, wrong issuer,
wrong audience, unreachable JWKS, tampered sessions, unknown service
tokens -- is raised as an :class:`AuthError` carrying the HTTP status and a
machine-readable code (ADR-0001 SS7.3). There is no code path in this
package that swallows a verification failure and returns an identity
anyway.
"""

from __future__ import annotations


class AuthError(Exception):
    """Raised to signal an authentication or infrastructure failure.

    Parameters
    ----------
    status:
        The HTTP status the caller should surface. Per ADR-0001 SS7.3,
        this is **401** for a bad/invalid/expired/wrong-issuer/wrong-
        audience credential, and **503** when the identity provider
        infrastructure itself (JWKS) is unreachable and there is no
        usable cached key material -- infrastructure failure must never
        be reported as a 401, which would falsely blame the caller's
        credential and start a login loop against a service that is
        already down.
    code:
        A stable, machine-readable error code (e.g. ``"invalid_audience"``,
        ``"token_expired"``, ``"identity_provider_unavailable"``) suitable
        for an API error body and for metrics/alerting.
    headers:
        Optional extra HTTP headers the caller should set on the error
        response (e.g. ``{"WWW-Authenticate": "Bearer"}`` for
        ``missing_credentials``).
    """

    def __init__(
        self,
        status: int,
        code: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.headers: dict[str, str] = headers or {}

    def __repr__(self) -> str:
        return f"AuthError(status={self.status!r}, code={self.code!r})"
