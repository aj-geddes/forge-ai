"""API key authentication dependencies for FastAPI routes.

Provides a reusable ``require_admin_key`` dependency that validates
Bearer tokens or X-API-Key headers against the configured admin API keys.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from forge_config.schema import APIKeyConfig

logger = logging.getLogger("forge.gateway.auth")

# --- Module-level state wired at startup ---

_api_key_config: APIKeyConfig | None = None
_resolved_keys: list[str] = []


def set_api_key_config(config: APIKeyConfig | None) -> None:
    """Wire API key config and resolve the key values."""
    global _api_key_config, _resolved_keys
    _api_key_config = config
    _resolved_keys = _resolve_keys(config) if config else []


def _resolve_keys(config: APIKeyConfig) -> list[str]:
    """Resolve SecretRef list to plaintext API key strings."""
    from forge_config.secret_resolver import CompositeSecretResolver

    resolver = CompositeSecretResolver()
    keys: list[str] = []
    for ref in config.keys:
        try:
            keys.append(resolver.resolve(ref))
        except Exception:
            logger.warning("Failed to resolve API key secret: %s", ref.name)
    return keys


# --- Security schemes ---

_bearer_scheme = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# --- Authentication dependency ---


async def require_admin_key(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),  # noqa: B008
    api_key: str | None = Depends(_api_key_header),  # noqa: B008
) -> str:
    """FastAPI dependency that enforces admin API key authentication.

    Accepts credentials via:
    - ``Authorization: Bearer <key>``
    - ``X-API-Key: <key>``

    Returns the validated key on success.
    Raises 401 if no credentials are provided or the key is invalid.
    Raises 403 if API key auth is not configured (no admin keys defined).
    """
    # If API key auth is disabled or not configured, deny by default
    if _api_key_config is None or not _api_key_config.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin API key authentication is not configured",
        )

    if not _resolved_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No admin API keys are configured",
        )

    # Extract the token from either source
    token = _extract_token(bearer, api_key)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate against configured keys using constant-time comparison
    if not _validate_key(token):
        client_host = request.client.host if request.client else "unknown"
        logger.warning("Invalid admin API key attempt from %s", client_host)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def _extract_token(
    bearer: HTTPAuthorizationCredentials | None,
    api_key: str | None,
) -> str | None:
    """Extract the API key from Bearer header or X-API-Key header."""
    if bearer is not None:
        return bearer.credentials
    if api_key is not None:
        return api_key
    return None


def _validate_key(token: str) -> bool:
    """Check token against resolved keys using constant-time comparison."""
    token_bytes = token.encode("utf-8")
    for key in _resolved_keys:
        key_bytes = key.encode("utf-8")
        if hmac.compare_digest(token_bytes, key_bytes):
            return True
    return False


# --- SSRF protection ---


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def validate_peer_endpoint(endpoint: str) -> bool:
    """Validate that a peer endpoint URL is not targeting private/internal IPs.

    Returns True if the endpoint appears safe, False if it targets a
    private/internal network.
    """
    parsed = urlparse(endpoint)
    hostname = parsed.hostname
    if hostname is None:
        return False

    try:
        addr = ipaddress.ip_address(hostname)
        return not any(addr in network for network in _PRIVATE_NETWORKS)
    except ValueError:
        # It's a hostname, not an IP — allow it (DNS resolution happens later)
        # but block obvious internal hostnames
        lower = hostname.lower()
        blocked_suffixes = (".local", ".internal", ".localhost")
        return lower != "localhost" and not lower.endswith(blocked_suffixes)
