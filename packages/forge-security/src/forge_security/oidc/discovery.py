"""OIDC discovery-document fetch and endpoint resolution (ADR-0001 SS8.1).

Explicit config overrides (``OIDCConfig.authorization_endpoint`` etc.)
always take precedence over the discovered ``.well-known`` document, so a
deployment can pin endpoints even when ``discovery: true``.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from forge_config.schema import OIDCConfig

_DISCOVERY_PATH = "/.well-known/openid-configuration"
_REQUIRED_FIELDS = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")


class DiscoveryError(Exception):
    """Raised when the OIDC discovery document cannot be fetched or is
    missing a required field."""


@dataclass(frozen=True)
class DiscoveryDocument:
    """The subset of an OIDC discovery document Forge actually uses."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


async def fetch_discovery_document(
    http_client: httpx.AsyncClient, issuer: str, *, timeout_seconds: float = 10.0
) -> DiscoveryDocument:
    """Fetch and parse ``{issuer}/.well-known/openid-configuration``."""
    url = issuer.rstrip("/") + _DISCOVERY_PATH
    try:
        response = await http_client.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        msg = f"failed to fetch OIDC discovery document from {url}: {exc}"
        raise DiscoveryError(msg) from exc

    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        msg = f"OIDC discovery document at {url} is missing required field(s): {missing}"
        raise DiscoveryError(msg)

    return DiscoveryDocument(
        issuer=data["issuer"],
        authorization_endpoint=data["authorization_endpoint"],
        token_endpoint=data["token_endpoint"],
        jwks_uri=data["jwks_uri"],
    )


def resolve_endpoints(config: OIDCConfig, discovery: DiscoveryDocument | None) -> DiscoveryDocument:
    """Merge explicit ``config`` overrides over a (possibly absent)
    discovery document. Explicit config values always win."""
    authorization_endpoint = config.authorization_endpoint or (
        discovery.authorization_endpoint if discovery else None
    )
    token_endpoint = config.token_endpoint or (discovery.token_endpoint if discovery else None)
    jwks_uri = config.jwks_uri or (discovery.jwks_uri if discovery else None)

    return DiscoveryDocument(
        issuer=config.issuer,
        authorization_endpoint=authorization_endpoint or "",
        token_endpoint=token_endpoint or "",
        jwks_uri=jwks_uri or "",
    )
