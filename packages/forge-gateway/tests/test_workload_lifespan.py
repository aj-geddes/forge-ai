"""Tests for ADR-0004 SS4/SS8 lifespan wiring: build the WorkloadPlane and
start the :8443 listener only when ``security.agentweave.enabled``, never
letting a workload-plane failure (SPIRE unreachable, listener port
conflict, or agentweave disabled outright) affect the human :8000 plane's
startup or readiness.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from forge_config.schema import (
    AgentWeaveConfig,
    AuthMode,
    AuthorizationConfig,
    ForgeConfig,
    OIDCConfig,
    SecurityAuthConfig,
    SecurityConfig,
    ServiceToken,
    ServiceTokenConfig,
)
from forge_gateway import app as app_module
from forge_gateway import security
from forge_gateway.routes import approvals, health
from forge_security.oidc import Authorizer
from forge_security.workload.errors import WorkloadUnavailable

pytestmark = pytest.mark.usefixtures("_reset_workload_state")


@pytest.fixture(autouse=True)
def _reset_workload_state() -> Iterator[None]:
    yield
    health.set_workload_health(None)
    health.set_ready(False)
    health.set_started(False)
    health.reset_components()
    security.reset_auth()


def _no_oidc_config(*, agentweave_enabled: bool, workload_port: int = 0) -> ForgeConfig:
    """A real, minimal ForgeConfig with OIDC off and a static service
    token (so ``validate_enforce_has_mechanism`` is satisfied without
    needing a reachable Dex instance), and agentweave configured for the
    workload plane."""
    return ForgeConfig(
        security=SecurityConfig(
            auth=SecurityAuthConfig(mode=AuthMode.ENFORCE),
            oidc=OIDCConfig(enabled=False),
            service_tokens=ServiceTokenConfig(
                tokens=[ServiceToken(id="t1", secret_sha256="0" * 64, roles=["admin"])]
            ),
            agentweave=AgentWeaveConfig(
                enabled=agentweave_enabled,
                trust_domain="hvslocal",
                workload_listener_port=workload_port,
            ),
        )
    )


class TestWorkloadListenerAbsentWhenDisabled:
    async def test_workload_listener_absent_when_agentweave_disabled(self) -> None:
        config = _no_oidc_config(agentweave_enabled=False)

        handle = await app_module._init_workload_plane(config, agent=None)

        assert handle is None
        assert health.get_workload_health() is None

    async def test_workload_listener_absent_when_config_missing(self) -> None:
        handle = await app_module._init_workload_plane(None, agent=None)

        assert handle is None
        assert health.get_workload_health() is None


class TestWorkloadPlaneStartsInTestMode:
    async def test_starts_a_real_listener_in_test_mode(self) -> None:
        """agentweave.enabled=True + FORGE_WORKLOAD_TEST_MODE=1 builds a
        real (agentweave-mock-backed) WorkloadPlane and starts a real
        listener on an ephemeral port, with no real SPIRE/OPA needed."""
        config = _no_oidc_config(agentweave_enabled=True, workload_port=0)

        with patch.dict("os.environ", {"FORGE_WORKLOAD_TEST_MODE": "1"}):
            handle = await app_module._init_workload_plane(config, agent=None)

        assert handle is not None
        try:
            assert handle.bound_port > 0
            workload_health = health.get_workload_health()
            assert workload_health is not None
            assert workload_health["status"] == "healthy"
        finally:
            await handle.stop()


class TestSpireUnreachableFailsClosedWorkloadOnly:
    """ADR-0004 SS8, the load-bearing test: SPIRE unreachable -> the
    :8443 listener does not come up, but the human plane's startup and
    readiness are completely unaffected."""

    async def test_spire_unreachable_no_listener_but_workload_health_unavailable(self) -> None:
        config = _no_oidc_config(agentweave_enabled=True)

        with patch(
            "forge_gateway.app.build_workload_plane",
            new=AsyncMock(side_effect=WorkloadUnavailable("spiffe_identity_unavailable")),
        ):
            handle = await app_module._init_workload_plane(config, agent=None)

        assert handle is None
        workload_health = health.get_workload_health()
        assert workload_health is not None
        assert workload_health["status"] == "unavailable"

    async def test_workload_health_does_not_gate_human_readiness(self) -> None:
        """The single most important property of ADR-0004: a SPIRE outage
        must never flip /health/ready to NOT READY."""
        config = _no_oidc_config(agentweave_enabled=True)

        with patch(
            "forge_gateway.app.build_workload_plane",
            new=AsyncMock(side_effect=WorkloadUnavailable("spiffe_identity_unavailable")),
        ):
            await app_module._init_workload_plane(config, agent=None)

        # Simulate the rest of a successful human-plane startup.
        health.set_started(True)
        health.set_ready(True)
        security.configure_auth(
            session_codec=None,
            service_token_verifier=None,
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig()),
            dev_insecure=True,
        )
        health.set_auth_healthy(True)

        app = app_module.create_app()
        client = TestClient(app)
        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert response.json()["workload"]["status"] == "unavailable"


class TestWorkloadAuditTrailWiring:
    """ADR-0005 SS6.3 (security review finding #3): approve/reject
    decisions route to the AgentWeave AuditTrail once the WorkloadPlane is
    built -- independent of whether the :8443 listener itself binds."""

    @pytest.fixture(autouse=True)
    def _reset_approvals_audit(self) -> Iterator[None]:
        yield
        approvals.set_audit_trail(None)

    async def test_audit_trail_wired_when_workload_plane_builds(self) -> None:
        config = _no_oidc_config(agentweave_enabled=True, workload_port=0)

        with patch.dict("os.environ", {"FORGE_WORKLOAD_TEST_MODE": "1"}):
            handle = await app_module._init_workload_plane(config, agent=None)

        try:
            assert approvals._audit_trail is not None
        finally:
            if handle is not None:
                await handle.stop()

    async def test_audit_trail_absent_when_agentweave_disabled(self) -> None:
        config = _no_oidc_config(agentweave_enabled=False)

        await app_module._init_workload_plane(config, agent=None)

        assert approvals._audit_trail is None


class TestListenerStartupFailureDoesNotGateHumanPlane:
    async def test_listener_bind_failure_is_handled_like_spire_unavailable(self) -> None:
        config = _no_oidc_config(agentweave_enabled=True, workload_port=0)

        with (
            patch.dict("os.environ", {"FORGE_WORKLOAD_TEST_MODE": "1"}),
            patch(
                "forge_gateway.app.start_workload_listener",
                new=AsyncMock(side_effect=app_module.WorkloadListenerStartupError("port in use")),
            ),
        ):
            handle = await app_module._init_workload_plane(config, agent=None)

        assert handle is None
        workload_health = health.get_workload_health()
        assert workload_health is not None
        assert workload_health["status"] == "unavailable"


class TestHumanPathUnaffected:
    """ADR-0004 SS10 validation criteria: every existing human-plane
    behavior is unaffected whether the workload plane is up or down, and
    no :8000 request can ever produce kind="workload"."""

    async def test_human_path_unaffected_when_workload_plane_disabled(self) -> None:
        health.set_workload_health(None)
        security.configure_auth(
            session_codec=None,
            service_token_verifier=None,
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig()),
            dev_insecure=True,
        )
        health.set_started(True)
        health.set_ready(True)
        health.set_auth_healthy(True)

        app = app_module.create_app()
        client = TestClient(app)

        assert client.get("/health/live").status_code == 200
        me = client.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["kind"] != "workload"

    async def test_human_path_unaffected_when_workload_plane_up(self) -> None:
        """Identical assertions, but with a real (test-mode) workload
        plane actually running on :8443 -- the human plane must behave
        identically either way."""
        config = _no_oidc_config(agentweave_enabled=True, workload_port=0)

        with patch.dict("os.environ", {"FORGE_WORKLOAD_TEST_MODE": "1"}):
            handle = await app_module._init_workload_plane(config, agent=None)
        assert handle is not None

        try:
            security.configure_auth(
                session_codec=None,
                service_token_verifier=None,
                oidc_verifier=None,
                authorizer=Authorizer(AuthorizationConfig()),
                dev_insecure=True,
            )
            health.set_started(True)
            health.set_ready(True)
            health.set_auth_healthy(True)

            app = app_module.create_app()
            client = TestClient(app)

            assert client.get("/health/live").status_code == 200
            me = client.get("/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["kind"] != "workload"
        finally:
            await handle.stop()

    async def test_no_input_to_human_resolver_can_produce_workload_kind(self) -> None:
        """No header, cookie, or query param exists that routes a :8000
        request into ``kind="workload"`` -- the workload resolver is only
        ever reachable from the separate :8443 listener."""
        security.configure_auth(
            session_codec=None,
            service_token_verifier=None,
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig()),
            dev_insecure=True,
        )
        health.set_started(True)
        health.set_ready(True)
        health.set_auth_healthy(True)

        app = app_module.create_app()
        client = TestClient(app)

        response = client.get(
            "/v1/auth/me",
            headers={
                "X-SPIFFE-ID": "spiffe://hvslocal/ns/dev/sa/attacker",
                "X-Peer-Spiffe-Id": "spiffe://hvslocal/ns/dev/sa/attacker",
            },
        )

        assert response.status_code == 200
        assert response.json()["kind"] != "workload"
