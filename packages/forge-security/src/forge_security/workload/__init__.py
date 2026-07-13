"""AgentWeave workload-plane security (SPIFFE identity + OPA authorization
+ mTLS + audit) for AI-agent-to-agent (A2A) traffic (ADR-0004).

This is the **workload** plane -- east-west, agent-to-agent, verified by
mutual TLS against a SPIRE trust bundle and authorized per-request by
OPA. It is physically and logically separate from
``forge_security.oidc`` (the human plane: north-south, Dex OIDC,
cookies/service-tokens/JWTs on ``:8000``). The two never share a
resolver, a listener, or an authorization mechanism; they share only the
:class:`~forge_security.oidc.principal.Principal` type, extended
additively with ``kind="workload"`` and ``spiffe_id``.

Public API consumed by forge-gateway::

    from forge_security.workload import (
        WorkloadPlane,
        build_workload_plane,
        resolve_workload_principal,
        authorize_workload,
        build_authz_context,
        build_audit_trail,
        extract_spiffe_id_from_cert,
        server_ssl_context,
        register_rotation_callback,
        WorkloadAuthError, WorkloadUnauthenticated, WorkloadForbidden, WorkloadUnavailable,
    )
"""

from __future__ import annotations

from forge_security.workload.audit import build_audit_trail
from forge_security.workload.authz import authorize_workload, build_authz_context
from forge_security.workload.errors import (
    WorkloadAuthError,
    WorkloadForbidden,
    WorkloadUnauthenticated,
    WorkloadUnavailable,
)
from forge_security.workload.mtls import (
    extract_spiffe_id_from_cert,
    register_rotation_callback,
    server_ssl_context,
)
from forge_security.workload.providers import WorkloadPlane, build_workload_plane
from forge_security.workload.resolver import resolve_workload_principal

__all__ = [
    "WorkloadAuthError",
    "WorkloadForbidden",
    "WorkloadPlane",
    "WorkloadUnauthenticated",
    "WorkloadUnavailable",
    "authorize_workload",
    "build_audit_trail",
    "build_authz_context",
    "build_workload_plane",
    "extract_spiffe_id_from_cert",
    "register_rotation_callback",
    "resolve_workload_principal",
    "server_ssl_context",
]
