"""Tests for forge_security.oidc.discovery (ADR-0001 SS8.1 "startup warm")."""

from __future__ import annotations

import httpx
import pytest
from forge_config.schema import OIDCConfig
from forge_security.oidc.discovery import (
    DiscoveryDocument,
    DiscoveryError,
    fetch_discovery_document,
    resolve_endpoints,
)

ISSUER = "https://dex.hvslocal/dex"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestFetchDiscoveryDocument:
    async def test_fetches_and_parses_discovery_document(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == f"{ISSUER}/.well-known/openid-configuration"
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/auth",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/keys",
                },
            )

        doc = await fetch_discovery_document(_client(handler), ISSUER)

        assert doc.issuer == ISSUER
        assert doc.jwks_uri == f"{ISSUER}/keys"

    async def test_network_failure_raises_discovery_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        with pytest.raises(DiscoveryError):
            await fetch_discovery_document(_client(handler), ISSUER)

    async def test_missing_required_field_raises_discovery_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"issuer": ISSUER})  # missing jwks_uri etc.

        with pytest.raises(DiscoveryError):
            await fetch_discovery_document(_client(handler), ISSUER)


class TestResolveEndpoints:
    def test_explicit_config_overrides_take_precedence_over_discovery(self):
        config = OIDCConfig(
            jwks_uri="https://pinned.example.com/keys",
            authorization_endpoint=None,
            token_endpoint=None,
        )
        discovery = DiscoveryDocument(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}/auth",
            token_endpoint=f"{ISSUER}/token",
            jwks_uri=f"{ISSUER}/keys",
        )

        resolved = resolve_endpoints(config, discovery)

        assert resolved.jwks_uri == "https://pinned.example.com/keys"  # config wins
        assert resolved.authorization_endpoint == f"{ISSUER}/auth"  # falls back to discovery
        assert resolved.token_endpoint == f"{ISSUER}/token"

    def test_no_discovery_document_uses_config_only(self):
        config = OIDCConfig()

        resolved = resolve_endpoints(config, None)

        assert resolved.jwks_uri == config.jwks_uri
