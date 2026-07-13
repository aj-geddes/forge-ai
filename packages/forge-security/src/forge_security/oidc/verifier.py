"""RS256 ID-token / bearer-token verification (ADR-0001 SS8.2, normative).

Every rejection path raises :class:`~forge_security.oidc.errors.AuthError`.
There is no branch in :meth:`OIDCTokenVerifier.verify` that returns claims
it did not cryptographically verify -- that is the entire difference from
the retired ``jwt.decode(..., algorithms=["HS256"], options={"verify_aud":
False})`` code this replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jwt

from forge_security.oidc.errors import AuthError
from forge_security.oidc.jwks import JwksCache

Claims = dict[str, Any]

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


@dataclass(frozen=True)
class OIDCVerifierConfig:
    """Static verification parameters, resolved once from ``OIDCConfig``."""

    issuer: str
    audience: str
    client_id: str
    allowed_algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    clock_skew_seconds: int = 60


class OIDCTokenVerifier:
    """Verifies a Dex-issued JWT (ID token or bearer access token).

    Parameters
    ----------
    jwks_cache:
        The async JWKS cache used to resolve the token's ``kid`` to a
        verification key.
    config:
        Static issuer/audience/algorithm parameters.
    """

    def __init__(self, *, jwks_cache: JwksCache, config: OIDCVerifierConfig) -> None:
        self._jwks_cache = jwks_cache
        self._config = config

    async def verify(self, token: str, *, nonce: str | None = None) -> Claims:
        """Verify *token* and return its claims, or raise ``AuthError``.

        Parameters
        ----------
        token:
            The compact JWS to verify.
        nonce:
            The expected ``nonce`` claim. Only supplied for the auth-code
            callback flow (ADR-0001 SS8.2 step 7); bearer-token
            verification passes ``None`` -- there is no transaction to
            bind a bearer token to.
        """
        header = self._parse_header(token)
        self._check_algorithm(header)
        kid = self._require_kid(header)

        # May raise AuthError(503, "identity_provider_unavailable") or
        # AuthError(401, "unknown_key") -- both propagate unchanged.
        key = await self._jwks_cache.get_key(kid)

        claims = self._decode(token, key)
        self._check_azp(claims)
        self._check_nonce(claims, nonce)
        return claims

    # -- steps (ADR-0001 SS8.2) ------------------------------------------

    @staticmethod
    def _parse_header(token: str) -> dict[str, Any]:
        # Step 1: parse the JOSE header WITHOUT verifying.
        try:
            header: dict[str, Any] = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(401, "invalid_token") from exc
        return header

    def _check_algorithm(self, header: dict[str, Any]) -> None:
        # Step 2: alg allow-list, enforced BEFORE any key lookup. This is
        # what actually blocks `alg: none` and HS-vs-RS confusion attacks
        # -- a token never gets to choose the algorithm used to verify it.
        alg = header.get("alg")
        if alg not in self._config.allowed_algorithms:
            raise AuthError(401, "invalid_token")

    @staticmethod
    def _require_kid(header: dict[str, Any]) -> str:
        # Step 3: kid MUST be present.
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError(401, "invalid_token")
        return kid

    def _decode(self, token: str, key: jwt.PyJWK) -> Claims:
        # Step 5: strict claim verification. algorithms= is an allow-list
        # (not header-driven) and audience/issuer verification is always
        # on -- this is the direct fix for the retired verify_aud=False bug.
        try:
            claims: Claims = jwt.decode(
                token,
                key=key,
                algorithms=self._config.allowed_algorithms,
                issuer=self._config.issuer,
                audience=self._config.audience,
                leeway=self._config.clock_skew_seconds,
                options={
                    "require": _REQUIRED_CLAIMS,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError(401, "token_expired") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError(401, "invalid_issuer") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError(401, "invalid_audience") from exc
        except jwt.PyJWTError as exc:
            # Signature mismatch, missing required claim, malformed
            # payload, or anything else -- there is no branch here that
            # returns an identity it did not verify.
            raise AuthError(401, "invalid_token") from exc
        return claims

    def _check_azp(self, claims: Claims) -> None:
        # Step 6: when aud is a list, azp (if present) must equal our
        # client_id.
        aud = claims.get("aud")
        if isinstance(aud, list):
            azp = claims.get("azp")
            if azp is not None and azp != self._config.client_id:
                raise AuthError(401, "invalid_audience")

    @staticmethod
    def _check_nonce(claims: Claims, expected_nonce: str | None) -> None:
        # Step 7: auth-code flow only -- not applicable to bearer-token
        # verification, where expected_nonce is None.
        if expected_nonce is None:
            return
        if claims.get("nonce") != expected_nonce:
            raise AuthError(401, "invalid_nonce")
