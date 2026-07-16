"""SSRF protection for outbound peer-endpoint calls.

The SSRF classifier that used to live here has been hoisted down into
``forge_security.egress`` (ADR-0006) so the tool builders (forge-agent) and
the gateway admin routes share a single blocklist with no drift.
``validate_peer_endpoint`` is now a thin re-export with unchanged public
behavior -- it delegates to ``forge_security.egress.validate_endpoint`` with
no policy, which is behaviorally identical to the previous implementation
(canonical/integer/hex/octal/short-dotted/IPv4-mapped encodings and
resolve-then-classify all preserved, plus the added multi-cloud metadata
blocklist).
"""

from __future__ import annotations

from forge_security.egress.classify import candidate_ips as _candidate_ips
from forge_security.egress.classify import is_internal_ip as _is_internal_ip
from forge_security.egress.classify import validate_endpoint

__all__ = ["validate_peer_endpoint", "validate_endpoint"]

# Back-compat aliases for any caller that imported the private helpers from
# this module before the ADR-0006 hoist.
_candidate_ips = _candidate_ips
_is_internal_ip = _is_internal_ip


def validate_peer_endpoint(endpoint: str) -> bool:
    """Validate that a peer endpoint URL is not targeting private/internal IPs.

    Returns ``True`` if the endpoint appears safe, ``False`` if it targets a
    private/internal network in ANY IP encoding (canonical, integer, hex,
    octal, short-dotted, or IPv4-mapped IPv6), or via a hostname that resolves
    to such an address. Thin re-export of
    ``forge_security.egress.validate_endpoint`` (ADR-0006); public behavior
    unchanged.
    """
    return validate_endpoint(endpoint)
