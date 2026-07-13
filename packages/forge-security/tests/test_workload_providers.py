"""Tests for forge_security.workload.providers.build_workload_plane (ADR-0004 SS7.2, SS8).

Two modes, tested separately:

- ``test_mode=True`` must construct with **no** real SPIRE/OPA -- only
  agentweave's in-memory test doubles (``MockIdentityProvider``,
  ``MockAuthorizationProvider``).
- production mode builds the real ``SPIFFEIdentityProvider``/``OPAProvider``
  wired to the configured endpoints, and -- the fail-closed rollout
  guarantee (ADR-0004 SS8) -- if the SPIFFE Workload API is unreachable at
  startup, ``initialize()`` failing must raise ``WorkloadUnavailable``
  rather than silently returning a half-built (or worse, ID-less) plane.
  This can never affect the human ``:8000`` plane, which this module does
  not touch at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from agentweave.testing.mocks import MockAuthorizationProvider, MockIdentityProvider
from cryptography.fernet import Fernet
from forge_config.schema import AgentWeaveConfig, AuthorizationConfig
from forge_security.oidc import AuthError, resolve_principal
from forge_security.oidc.authorizer import Authorizer
from forge_security.oidc.service_tokens import ServiceTokenVerifier
from forge_security.oidc.session import SessionCodec
from forge_security.workload import providers as providers_module
from forge_security.workload.errors import WorkloadUnavailable
from forge_security.workload.providers import WorkloadPlane, build_workload_plane


def _cfg(**overrides: Any) -> AgentWeaveConfig:
    defaults: dict[str, Any] = {
        "trust_domain": "hvslocal",
        "spiffe_endpoint": "unix:///spiffe-workload-api/spire-agent.sock",
        "opa_endpoint": "http://opa.opa.svc.cluster.local:8181",
    }
    defaults.update(overrides)
    return AgentWeaveConfig(**defaults)


class TestBuildWorkloadPlaneTestMode:
    async def test_constructs_with_only_agentweave_test_doubles(self):
        plane = await build_workload_plane(_cfg(), test_mode=True)

        assert isinstance(plane, WorkloadPlane)
        # Both sides are adapted (agentweave's mocks predate the real
        # provider interfaces), but must still wrap the real mocks, not a
        # live SPIRE/OPA client.
        assert isinstance(plane.identity._mock, MockIdentityProvider)  # noqa: SLF001
        assert isinstance(plane.authz._mock, MockAuthorizationProvider)  # noqa: SLF001

    async def test_my_spiffe_id_reflects_configured_trust_domain(self):
        plane = await build_workload_plane(_cfg(trust_domain="hvslocal"), test_mode=True)

        spiffe_id = await plane.my_spiffe_id()

        assert spiffe_id.startswith("spiffe://hvslocal/")

    async def test_no_real_network_client_constructed(self, monkeypatch: pytest.MonkeyPatch):
        """Even with garbage endpoints, test_mode must never attempt to
        build a real OPAProvider (which eagerly opens an httpx client)."""

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("real OPAProvider must not be constructed in test_mode")

        monkeypatch.setattr(providers_module, "OPAProvider", _boom)
        monkeypatch.setattr(providers_module, "SPIFFEIdentityProvider", _boom)

        plane = await build_workload_plane(
            _cfg(opa_endpoint="http://unroutable.invalid:1"), test_mode=True
        )

        assert isinstance(plane.identity._mock, MockIdentityProvider)  # noqa: SLF001


class TestBuildWorkloadPlaneProductionMode:
    async def test_wires_real_providers_to_configured_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        identity_calls: list[dict[str, Any]] = []
        opa_calls: list[dict[str, Any]] = []

        class _FakeIdentity:
            def __init__(self, **kwargs: Any) -> None:
                identity_calls.append(kwargs)

            async def initialize(self) -> None:
                pass

        class _FakeOPA:
            def __init__(self, **kwargs: Any) -> None:
                opa_calls.append(kwargs)

        monkeypatch.setattr(providers_module, "SPIFFEIdentityProvider", _FakeIdentity)
        monkeypatch.setattr(providers_module, "OPAProvider", _FakeOPA)
        monkeypatch.delenv("SPIFFE_ENDPOINT_SOCKET", raising=False)
        monkeypatch.delenv("OPA_ENDPOINT", raising=False)

        plane = await build_workload_plane(_cfg(), test_mode=False)

        assert isinstance(plane, WorkloadPlane)
        assert identity_calls[0]["endpoint"] == "unix:///spiffe-workload-api/spire-agent.sock"
        assert opa_calls[0]["endpoint"] == "http://opa.opa.svc.cluster.local:8181"
        # Fail-closed default, never overridable to False from config.
        assert opa_calls[0]["default_deny"] is True

    async def test_env_vars_take_precedence_over_config(self, monkeypatch: pytest.MonkeyPatch):
        identity_calls: list[dict[str, Any]] = []
        opa_calls: list[dict[str, Any]] = []

        class _FakeIdentity:
            def __init__(self, **kwargs: Any) -> None:
                identity_calls.append(kwargs)

            async def initialize(self) -> None:
                pass

        class _FakeOPA:
            def __init__(self, **kwargs: Any) -> None:
                opa_calls.append(kwargs)

        monkeypatch.setattr(providers_module, "SPIFFEIdentityProvider", _FakeIdentity)
        monkeypatch.setattr(providers_module, "OPAProvider", _FakeOPA)
        monkeypatch.setenv("SPIFFE_ENDPOINT_SOCKET", "unix:///env-override.sock")
        monkeypatch.setenv("OPA_ENDPOINT", "http://opa-env.example:8181")

        await build_workload_plane(_cfg(), test_mode=False)

        assert identity_calls[0]["endpoint"] == "unix:///env-override.sock"
        assert opa_calls[0]["endpoint"] == "http://opa-env.example:8181"


class TestBuildWorkloadPlaneFailsClosedOnUnreachableSpire:
    async def test_spire_initialize_failure_raises_workload_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        class _FakeIdentity:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def initialize(self) -> None:
                raise ConnectionError("cannot connect to SPIFFE Workload API")

        monkeypatch.setattr(providers_module, "SPIFFEIdentityProvider", _FakeIdentity)

        with pytest.raises(WorkloadUnavailable) as exc_info:
            await build_workload_plane(_cfg(), test_mode=False)

        assert exc_info.value.status == 503

    async def test_human_plane_is_unaffected_by_workload_plane_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The workload plane failing to come up must be a purely local
        failure of this function -- it must not touch, patch, or disable
        anything in forge_security.oidc, which remains fully functional
        immediately afterwards."""

        class _FakeIdentity:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def initialize(self) -> None:
                raise ConnectionError("spire down")

        monkeypatch.setattr(providers_module, "SPIFFEIdentityProvider", _FakeIdentity)

        with pytest.raises(WorkloadUnavailable):
            await build_workload_plane(_cfg(), test_mode=False)

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
