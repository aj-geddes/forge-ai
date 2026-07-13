"""The workload-plane identity resolver (ADR-0004 SS4).

::

    resolve_workload_principal(peer_spiffe_id) -> Principal   # raises WorkloadUnauthenticated(401)

Deliberately narrow and separate from ``forge_security.oidc.resolver``:
this function's *only* input is the peer SPIFFE ID already extracted
(via ``forge_security.workload.mtls.extract_spiffe_id_from_cert``) from a
client certificate that the ``:8443`` listener's TLS handshake has
**already verified** against the SPIRE trust bundle (``CERT_REQUIRED`` --
a forged or foreign-CA certificate is rejected at the handshake itself,
before any Python code runs). This function's job is narrower still:
does the now-trusted certificate carry a ``spiffe://`` identity at all?
If not -- no SAN, an empty value, or a value that isn't shaped like a
SPIFFE ID -- the identity is unknown, and this must never be allowed
through as if it were a real peer (deny-by-default).

There is no input to ``forge_security.oidc.resolve_principal`` that can
ever route into this module or produce ``kind="workload"``: the two
resolvers live on physically separate listeners (``:8000`` vs ``:8443``)
and are never invoked from the same code path.
"""

from __future__ import annotations

from forge_security.oidc.principal import Principal
from forge_security.workload.errors import WorkloadUnauthenticated

_SPIFFE_SCHEME = "spiffe://"


def resolve_workload_principal(peer_spiffe_id: str | None) -> Principal:
    """Turn an already-verified peer SPIFFE ID into a ``kind="workload"``
    :class:`~forge_security.oidc.principal.Principal`.

    Raises
    ------
    WorkloadUnauthenticated
        (401) if ``peer_spiffe_id`` is ``None``, empty, or does not start
        with ``spiffe://`` -- i.e. the mTLS handshake succeeded but the
        certificate carried no usable SPIFFE identity. There is no
        fallback identity and no path that returns a Principal in this
        case.
    """
    if not peer_spiffe_id or not peer_spiffe_id.startswith(_SPIFFE_SCHEME):
        raise WorkloadUnauthenticated("unknown_workload_identity")

    return Principal(kind="workload", sub=peer_spiffe_id, spiffe_id=peer_spiffe_id)
