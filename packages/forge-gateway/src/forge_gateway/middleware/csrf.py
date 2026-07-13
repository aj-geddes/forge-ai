"""CSRF protection for cookie-authenticated requests (ADR-0001 SS4.2).

Three layers of defence apply to every cookie-authenticated, state-changing
(``POST``/``PUT``/``PATCH``/``DELETE``) request:

1. ``SameSite=Lax`` on the session cookie (set where the cookie is issued,
   not here) blocks the request from ever arriving with the cookie on a
   plain cross-site navigation.
2. **Origin check** -- the ``Origin`` (or ``Referer``) header must name an
   allowed origin.
3. **Double-submit token** -- the ``X-CSRF-Token`` header must match the
   non-``httpOnly`` ``forge_csrf`` cookie, compared with
   ``hmac.compare_digest``.

Requests authenticated with a ``Authorization`` header (service tokens,
OIDC bearer tokens) carry no ambient credential, so there is nothing to
forge -- they are exempt entirely.
"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_HEADER = "x-csrf-token"


def _origin_root(value: str) -> str:
    """Reduce an ``Origin`` or ``Referer`` header value to ``scheme://host[:port]``."""
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    return f"{parts.scheme}://{parts.netloc}"


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforces the double-submit-token + Origin check on cookie-authenticated
    state-changing requests."""

    def __init__(
        self,
        app: object,
        *,
        session_cookie_name: str = "forge_session",
        csrf_cookie_name: str = "forge_csrf",
        allowed_origins: list[str] | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._session_cookie_name = session_cookie_name
        self._csrf_cookie_name = csrf_cookie_name
        self._allowed_origins = set(allowed_origins or [])

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._requires_check(request) and not self._passes(request):
            return JSONResponse({"error": "csrf_failed"}, status_code=403)
        return await call_next(request)

    def _requires_check(self, request: Request) -> bool:
        if request.method not in _UNSAFE_METHODS:
            return False
        if request.headers.get("authorization"):
            # Bearer/service-token requests are CSRF-exempt (no ambient credential).
            return False
        return self._session_cookie_name in request.cookies

    def _passes(self, request: Request) -> bool:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if not origin:
            return False
        if self._allowed_origins and _origin_root(origin) not in self._allowed_origins:
            return False

        header_token = request.headers.get(_CSRF_HEADER)
        cookie_token = request.cookies.get(self._csrf_cookie_name)
        if not header_token or not cookie_token:
            return False
        return hmac.compare_digest(header_token, cookie_token)
