"""SSRF-safe egress guard (ADR-0006).

A single shared, connect-time SSRF-safe egress layer usable by both the tool
builders (forge-agent) and the gateway admin routes (forge-gateway). Three
cooperating planes:

* ``classify`` -- pure host/IP classification (``validate_endpoint``,
  ``is_internal_ip``, ``candidate_ips``) plus ``host_matches``, the single
  ``allowed_hosts`` matcher every allow-list-honouring plane delegates to.
* ``transport`` -- ``GuardedBackend`` / ``SSRFGuardedTransport`` /
  ``make_guarded_client``: connect-time pinned-IP enforcement.
* ``binding`` -- ``BoundCredential`` / ``enforce_binding`` /
  ``credential_binding_from_raw_auth``: secret -> destination binding to stop
  confused-deputy exfiltration.
"""

from __future__ import annotations

from forge_security.egress.binding import (
    BoundCredential,
    EgressViolationError,
    credential_binding_from_raw_auth,
    enforce_binding,
)
from forge_security.egress.classify import (
    candidate_ips,
    host_matches,
    is_blocked_hostname,
    is_internal_ip,
    validate_endpoint,
)
from forge_security.egress.transport import (
    GuardedBackend,
    SSRFConnectBlocked,
    SSRFGuardedTransport,
    make_guarded_client,
)

__all__ = [
    # classify
    "validate_endpoint",
    "is_internal_ip",
    "candidate_ips",
    "is_blocked_hostname",
    "host_matches",
    # transport
    "GuardedBackend",
    "SSRFGuardedTransport",
    "SSRFConnectBlocked",
    "make_guarded_client",
    # binding
    "BoundCredential",
    "EgressViolationError",
    "enforce_binding",
    "credential_binding_from_raw_auth",
]
