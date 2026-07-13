"""Tests for forge_security.workload.resolver (ADR-0004 SS4).

``resolve_workload_principal`` takes the peer SPIFFE ID already extracted
from a **verified** mTLS client certificate (the TLS handshake itself --
``CERT_REQUIRED`` against the SPIRE trust bundle -- happens one layer
below, at the listener) and turns it into a ``kind="workload"``
Principal. Identity extraction here is deny-by-default: no
``spiffe://`` SAN (or a value that isn't shaped like one) means an
unknown identity, and this must never be allowed through as if it were
a real peer.
"""

from __future__ import annotations

import pytest
from forge_security.workload.errors import WorkloadUnauthenticated
from forge_security.workload.resolver import resolve_workload_principal

SPIFFE_ID = "spiffe://hvslocal/ns/dev-aj-geddes/sa/default"


class TestResolveWorkloadPrincipal:
    def test_valid_peer_yields_workload_principal_with_spiffe_id(self):
        principal = resolve_workload_principal(SPIFFE_ID)

        assert principal.kind == "workload"
        assert principal.spiffe_id == SPIFFE_ID
        assert principal.sub == SPIFFE_ID

    def test_none_identity_is_rejected(self):
        with pytest.raises(WorkloadUnauthenticated) as exc_info:
            resolve_workload_principal(None)

        assert exc_info.value.status == 401

    def test_empty_string_identity_is_rejected(self):
        with pytest.raises(WorkloadUnauthenticated) as exc_info:
            resolve_workload_principal("")

        assert exc_info.value.status == 401

    def test_fake_non_spiffe_identity_is_rejected(self):
        """A value that doesn't even look like a SPIFFE ID (e.g. a forged
        or malformed SAN) must be rejected, never coerced into a
        Principal."""
        with pytest.raises(WorkloadUnauthenticated) as exc_info:
            resolve_workload_principal("not-a-spiffe-id")

        assert exc_info.value.status == 401
        assert exc_info.value.code == "unknown_workload_identity"
