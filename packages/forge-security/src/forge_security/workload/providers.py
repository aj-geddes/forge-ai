"""Construct the workload plane and expose ``WorkloadPlane`` (ADR-0004 SS7.2).

::

    plane = await build_workload_plane(config.agentweave, test_mode=False)

Production wires the real agentweave SDK: ``SPIFFEIdentityProvider``
(SPIRE Workload API) + ``OPAProvider`` (``default_deny=True``, fail
closed) + ``AuditTrail``. ``test_mode=True`` (unit tests, or any
environment without real SPIRE/OPA) injects agentweave's own test
doubles (``agentweave.testing.mocks.MockIdentityProvider`` /
``MockAuthorizationProvider``) behind the exact same
:class:`WorkloadPlane` interface, so callers never branch on which mode
built it.

.. note::
   ``agentweave.testing.mocks.MockIdentityProvider``/``MockAuthorizationProvider``
   predate the ``IdentityProvider``/``AuthorizationProvider`` abstract
   interfaces ``SPIFFEIdentityProvider``/``OPAProvider`` implement (e.g.
   the mock exposes a sync ``get_spiffe_id()`` and no
   ``create_tls_context()``; ``check_outbound(caller, callee, action,
   context)`` instead of ``check(caller_id, resource, action,
   context)``). ``_MockIdentityAdapter``/``_MockAuthorizationAdapter``
   below adapt them to the real interfaces so ``WorkloadPlane`` never
   has to know which mode built it.

.. note::
   ``SPIFFEIdentityProvider`` (``agentweave.identity.spiffe``) is
   imported defensively: the installed ``spiffe`` (py-spiffe) release
   defines ``PySpiffeError``, not the ``SpiffeError`` name
   ``agentweave.identity.spiffe`` imports -- an upstream
   agentweave/py-spiffe version incompatibility, not a forge-security
   bug (there is no released ``spiffe`` version that ever exported
   ``SpiffeError``). If that import fails, ``SPIFFEIdentityProvider`` is
   left ``None`` and production-mode construction fails closed with
   :class:`~forge_security.workload.errors.WorkloadUnavailable` --
   consistent with ADR-0004 SS8's "SPIRE unreachable -> workload plane
   does not start, human plane unaffected." This also keeps
   ``SPIFFEIdentityProvider`` a plain, monkeypatchable module attribute
   for tests, whichever way the import resolves.
"""

from __future__ import annotations

import logging
import os
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentweave.authz.opa import OPAProvider
from agentweave.observability.audit import AuditTrail
from agentweave.testing.mocks import AuthzDecision as MockAuthzDecision
from agentweave.testing.mocks import MockAuthorizationProvider, MockIdentityProvider

from forge_security.oidc.principal import Principal
from forge_security.workload.audit import build_audit_trail
from forge_security.workload.authz import authorize_workload
from forge_security.workload.errors import WorkloadUnauthenticated, WorkloadUnavailable
from forge_security.workload.mtls import server_ssl_context
from forge_security.workload.resolver import resolve_workload_principal

try:
    from agentweave.identity.spiffe import SPIFFEIdentityProvider
except ImportError:  # pragma: no cover - upstream agentweave/py-spiffe incompatibility
    SPIFFEIdentityProvider: Any = None  # type: ignore[no-redef]

logger = logging.getLogger("forge.security.workload.providers")

_DEFAULT_POLICY_PATH = "agentweave/authz/allow"
_DEFAULT_SPIFFE_ENDPOINT_ENV = "SPIFFE_ENDPOINT_SOCKET"
_DEFAULT_OPA_ENDPOINT_ENV = "OPA_ENDPOINT"
_MOCK_KEY_FILE_MODE = 0o600


class _MockIdentityAdapter:
    """Adapts ``agentweave.testing.mocks.MockIdentityProvider`` to the
    ``IdentityProvider`` contract (``get_identity()``/``create_tls_context()``)
    that ``SPIFFEIdentityProvider`` implements. Builds a real, working
    ``SSLContext`` from the mock's self-signed SVID material -- written to
    a private temp dir, mirroring ``SPIFFEIdentityProvider``'s own
    approach -- so a ``test_mode`` ``WorkloadPlane`` is genuinely usable,
    not a stub that silently drops peer verification."""

    def __init__(self, mock: MockIdentityProvider) -> None:
        self._mock = mock
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_workload_mock_")

    async def get_identity(self) -> str:
        spiffe_id: str = self._mock.get_spiffe_id()
        return spiffe_id

    async def create_tls_context(self, server: bool = False) -> ssl.SSLContext:
        svid = await self._mock.get_svid()
        cert_path = Path(self._temp_dir.name) / "cert.pem"
        key_path = Path(self._temp_dir.name) / "key.pem"
        cert_path.write_bytes(svid.cert_chain)
        key_path.write_bytes(svid.private_key)
        os.chmod(key_path, _MOCK_KEY_FILE_MODE)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = False
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        # The mock SVID is self-signed; it is its own trust anchor here.
        ctx.load_verify_locations(cafile=str(cert_path))
        return ctx

    async def health_check(self) -> bool:
        return True


class _MockAuthorizationAdapter:
    """Adapts ``agentweave.testing.mocks.MockAuthorizationProvider`` --
    whose ``check_outbound``/``check_inbound`` API predates the
    ``AuthorizationProvider.check(caller_id, resource, action, context)``
    contract ``OPAProvider`` exposes -- to that uniform contract, so
    ``WorkloadPlane``/``authorize_workload`` never need to know which
    mode built them."""

    def __init__(self, mock: MockAuthorizationProvider) -> None:
        self._mock = mock

    async def check(
        self,
        caller_id: str,
        resource: str,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> MockAuthzDecision:
        decision: MockAuthzDecision = await self._mock.check_outbound(
            caller_id, resource, action, context
        )
        return decision

    async def health_check(self) -> bool:
        return True


@dataclass
class WorkloadPlane:
    """The single object forge-gateway consumes for the workload
    (SPIFFE + OPA + mTLS + audit) plane (ADR-0004 SS7.2).

    ``identity``/``authz`` hold either the real agentweave providers
    (``SPIFFEIdentityProvider``/``OPAProvider``) or, in ``test_mode``,
    agentweave's test doubles adapted to the same interface -- callers
    never branch on which.
    """

    identity: Any
    authz: Any
    audit: AuditTrail

    async def my_spiffe_id(self) -> str:
        """Return this agent's own SPIFFE ID."""
        spiffe_id: str = await self.identity.get_identity()
        return spiffe_id

    async def server_ssl_context(self) -> Any:
        """Server-side mTLS ``SSLContext`` for the ``:8443`` a2a-mtls
        listener (``CERT_REQUIRED``, backed by the current SVID)."""
        return await server_ssl_context(self.identity)

    async def verify_peer(self, peer_spiffe_id: str | None) -> Principal:
        """Turn an already mTLS-verified peer SPIFFE ID into a
        ``kind="workload"`` :class:`Principal`, auditing the outcome
        either way (ADR-0004 SS4, SS7.1)."""
        try:
            principal = resolve_workload_principal(peer_spiffe_id)
        except WorkloadUnauthenticated as exc:
            await self.audit.record_peer_verification(
                peer_id=peer_spiffe_id or "unknown",
                status="failure",
                reason=exc.code,
            )
            raise
        await self.audit.record_peer_verification(
            peer_id=principal.spiffe_id or "", status="success"
        )
        return principal

    async def authorize(
        self,
        principal: Principal,
        action: str,
        resource: str,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Authorize ``principal`` to perform ``action`` on ``resource``
        via OPA, raising :class:`~forge_security.workload.errors.WorkloadForbidden`
        on deny -- including a fail-closed deny if the authorization
        provider itself is unreachable or raises (ADR-0004 SS5, SS8)."""
        context = context or {}
        return await authorize_workload(
            principal,
            action,
            resource,
            authz=self.authz,
            audit=self.audit,
            task_type=context.get("task_type"),
            tool=context.get("tool"),
            peer_trust_level=context.get("peer_trust_level"),
        )

    async def health(self) -> dict[str, str]:
        """Component-level health for the workload plane (ADR-0004 SS8).

        Deliberately **not** the process's overall readiness: a
        SPIRE/OPA outage must gate agent-to-agent traffic, not the human
        ``:8000`` plane's readiness probe.
        """
        identity_ok = await self._component_health(self.identity)
        authz_ok = await self._component_health(self.authz)
        status = "healthy" if identity_ok and authz_ok else "unhealthy"
        return {
            "status": status,
            "identity": "up" if identity_ok else "down",
            "authz": "up" if authz_ok else "down",
        }

    @staticmethod
    async def _component_health(component: Any) -> bool:
        health_check = getattr(component, "health_check", None)
        if health_check is None:
            return True
        try:
            result: bool = await health_check()
            return result
        except Exception:
            return False


async def build_workload_plane(
    agentweave_config: Any,
    *,
    test_mode: bool = False,
    agent_name: str = "forge-agent",
) -> WorkloadPlane:
    """Build the :class:`WorkloadPlane` (ADR-0004 SS7.2).

    ``test_mode=True`` constructs with **no** real SPIRE/OPA -- only
    ``agentweave.testing.mocks`` test doubles, so unit tests (and local
    dev without a SPIRE agent) never touch the network or a Workload API
    socket.

    Production (``test_mode=False``) builds the real
    ``SPIFFEIdentityProvider`` -- reading the Workload API socket from
    ``SPIFFE_ENDPOINT_SOCKET`` (env) or ``agentweave_config.spiffe_endpoint``
    -- and calls ``await identity.initialize()``. Per ADR-0004 SS8, if
    that fails (SPIRE unreachable, or the identity provider is
    unavailable in this environment -- see the module docstring), this
    raises :class:`~forge_security.workload.errors.WorkloadUnavailable`
    rather than returning a half-built plane; the caller (forge-gateway)
    simply does not start the ``:8443`` listener. This function never
    imports or touches ``forge_security.oidc`` -- a workload-plane
    failure cannot affect the human plane.
    """
    if test_mode:
        identity: Any = _MockIdentityAdapter(
            MockIdentityProvider(
                spiffe_id=f"spiffe://{agentweave_config.trust_domain}/agent/{agent_name}"
            )
        )
        authz: Any = _MockAuthorizationAdapter(MockAuthorizationProvider(default_allow=False))
    else:
        if SPIFFEIdentityProvider is None:
            raise WorkloadUnavailable("spiffe_identity_provider_unavailable")

        spiffe_endpoint = (
            os.environ.get(_DEFAULT_SPIFFE_ENDPOINT_ENV) or agentweave_config.spiffe_endpoint
        )
        identity = SPIFFEIdentityProvider(endpoint=spiffe_endpoint)
        try:
            await identity.initialize()
        except Exception as exc:
            logger.critical(
                "workload plane: SPIFFE identity provider failed to initialize; "
                "the :8443 workload listener will not start (human plane unaffected)",
                exc_info=exc,
            )
            raise WorkloadUnavailable("spiffe_identity_unavailable") from exc

        opa_endpoint = os.environ.get(_DEFAULT_OPA_ENDPOINT_ENV) or agentweave_config.opa_endpoint
        authz = OPAProvider(
            endpoint=opa_endpoint,
            policy_path=getattr(agentweave_config, "policy_path", None) or _DEFAULT_POLICY_PATH,
            default_deny=True,
        )

    audit = build_audit_trail(agentweave_config, agent_name=agent_name)
    return WorkloadPlane(identity=identity, authz=authz, audit=audit)
