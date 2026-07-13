"""Dex OIDC authentication primitives (ADR-0001).

Public API surface consumed by forge-gateway::

    from forge_security.oidc import (
        AuthError,
        JwksCache,
        OIDCTokenVerifier, OIDCVerifierConfig,
        SessionCodec, SessionData,
        ServiceTokenVerifier, ServiceTokenPrincipalInfo, SERVICE_TOKEN_PREFIX,
        Principal, PrincipalKind,
        Authorizer,
        resolve_principal,
        DiscoveryDocument, DiscoveryError,
        fetch_discovery_document, resolve_endpoints,
    )
"""

from __future__ import annotations

from forge_security.oidc.authorizer import Authorizer
from forge_security.oidc.discovery import (
    DiscoveryDocument,
    DiscoveryError,
    fetch_discovery_document,
    resolve_endpoints,
)
from forge_security.oidc.errors import AuthError
from forge_security.oidc.jwks import JwksCache
from forge_security.oidc.principal import Principal, PrincipalKind
from forge_security.oidc.resolver import resolve_principal
from forge_security.oidc.service_tokens import (
    SERVICE_TOKEN_PREFIX,
    ServiceTokenPrincipalInfo,
    ServiceTokenVerifier,
)
from forge_security.oidc.session import SessionCodec, SessionData
from forge_security.oidc.verifier import Claims, OIDCTokenVerifier, OIDCVerifierConfig

__all__ = [
    "SERVICE_TOKEN_PREFIX",
    "AuthError",
    "Authorizer",
    "Claims",
    "DiscoveryDocument",
    "DiscoveryError",
    "JwksCache",
    "OIDCTokenVerifier",
    "OIDCVerifierConfig",
    "Principal",
    "PrincipalKind",
    "ServiceTokenPrincipalInfo",
    "ServiceTokenVerifier",
    "SessionCodec",
    "SessionData",
    "fetch_discovery_document",
    "resolve_endpoints",
    "resolve_principal",
]
