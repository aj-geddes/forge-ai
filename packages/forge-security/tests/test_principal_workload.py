"""Tests that the ``Principal``/``PrincipalKind`` extension for the
workload plane (ADR-0004 SS4) is additive and leaves the human resolver
(ADR-0001 SS5.1) byte-for-byte unaffected.
"""

from __future__ import annotations

import hashlib
import inspect

import pytest
from cryptography.fernet import Fernet
from forge_config.schema import AuthorizationConfig, ServiceToken
from forge_security.oidc import AuthError, resolve_principal
from forge_security.oidc.authorizer import Authorizer
from forge_security.oidc.principal import Principal
from forge_security.oidc.service_tokens import ServiceTokenVerifier
from forge_security.oidc.session import SessionCodec
from forge_security.workload.resolver import resolve_workload_principal

SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/default"


class TestPrincipalIsAdditive:
    def test_human_kinds_default_spiffe_id_to_none(self):
        principal = Principal(kind="user", sub="alice")

        assert principal.spiffe_id is None

    def test_existing_positional_and_keyword_construction_still_works(self):
        # The exact construction shape used throughout the pre-existing
        # oidc test suite (see test_authorizer.py) must keep working
        # unchanged now that a new trailing field exists.
        principal = Principal(kind="service", sub="svc:x", roles=["admin"], permissions=frozenset())

        assert principal.kind == "service"
        assert principal.spiffe_id is None

    def test_workload_kind_is_a_valid_literal_member(self):
        principal = Principal(kind="workload", sub=SPIFFE_ID, spiffe_id=SPIFFE_ID)

        assert principal.kind == "workload"
        assert principal.spiffe_id == SPIFFE_ID


class TestWorkloadPrincipalShape:
    def test_workload_principal_has_spiffe_id_and_kind_workload(self):
        principal = resolve_workload_principal(SPIFFE_ID)

        assert principal.kind == "workload"
        assert principal.spiffe_id == SPIFFE_ID
        assert principal.sub == SPIFFE_ID


class TestHumanResolverCannotProduceWorkloadPrincipal:
    def test_resolve_principal_signature_has_no_workload_input(self):
        """resolve_principal's signature is the entire attack surface for
        what credentials it can honor. There must be no header/body/query
        parameter through which a caller could ever request
        kind="workload" -- the workload resolver is a completely separate
        function, only ever invoked from the :8443 mTLS listener."""
        params = set(inspect.signature(resolve_principal).parameters)

        assert params == {
            "session_cookie",
            "authorization_header",
            "session_codec",
            "service_token_verifier",
            "oidc_verifier",
            "authorizer",
        }

    def test_resolve_principal_source_never_constructs_a_workload_principal(self):
        import forge_security.oidc.resolver as resolver_module

        source = inspect.getsource(resolver_module)
        assert '"workload"' not in source
        assert "'workload'" not in source

    async def test_no_credentials_raises_401_never_falls_back_to_a_principal(self):
        with pytest.raises(AuthError) as exc_info:
            await resolve_principal(
                session_cookie=None,
                authorization_header=None,
                session_codec=SessionCodec(key=Fernet.generate_key()),
                service_token_verifier=ServiceTokenVerifier([]),
                oidc_verifier=None,
                authorizer=Authorizer(AuthorizationConfig()),
            )

        assert exc_info.value.status == 401
        assert exc_info.value.code == "missing_credentials"

    async def test_service_token_path_yields_kind_service_not_workload(self):
        secret = "forge_sk_test_secret_value"
        digest = hashlib.sha256(secret.encode()).hexdigest()
        verifier = ServiceTokenVerifier([ServiceToken(id="t1", secret_sha256=digest, roles=[])])

        principal = await resolve_principal(
            session_cookie=None,
            authorization_header=f"Bearer {secret}",
            session_codec=SessionCodec(key=Fernet.generate_key()),
            service_token_verifier=verifier,
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig()),
        )

        assert principal.kind == "service"
        assert principal.kind != "workload"
        assert principal.spiffe_id is None
