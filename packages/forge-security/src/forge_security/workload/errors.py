"""Workload-plane (SPIFFE/OPA) authentication/authorization errors.

Mirrors the ``forge_security.oidc.errors.AuthError`` style: every
rejection on the ``:8443`` mTLS/A2A plane carries an HTTP status and a
stable, machine-readable code. As with the human resolver, there is no
code path in ``forge_security.workload`` that swallows a verification or
authorization failure and returns a principal, a decision, or a plane
anyway (ADR-0004 SS8, "fail closed").
"""

from __future__ import annotations


class WorkloadAuthError(Exception):
    """Base class for every failure on the workload (SPIFFE/OPA) plane.

    Parameters
    ----------
    status:
        The HTTP status the ``:8443`` listener should surface.
    code:
        A stable, machine-readable error code suitable for logs, audit
        events, and metrics/alerting.
    headers:
        Optional extra HTTP headers the caller should set on the error
        response.
    """

    def __init__(
        self,
        status: int,
        code: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.headers: dict[str, str] = headers or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status!r}, code={self.code!r})"


class WorkloadUnauthenticated(WorkloadAuthError):
    """401 -- the peer did not present a usable, known SPIFFE identity.

    Raised when the (already mTLS-verified) peer certificate carries no
    ``spiffe://`` URI SAN at all, or an empty/malformed one. Per
    ADR-0004 SS4, identity extraction is deny-by-default: an unknown
    identity is never allowed to proceed as a request.
    """

    def __init__(self, code: str = "unknown_workload_identity") -> None:
        super().__init__(401, code)


class WorkloadForbidden(WorkloadAuthError):
    """403 -- OPA denied the action, including the fail-closed default
    deny applied when the authorization provider itself is unreachable
    or raises (ADR-0004 SS5, SS8)."""

    def __init__(self, code: str = "opa_denied", *, reason: str = "") -> None:
        super().__init__(403, code)
        self.reason = reason


class WorkloadUnavailable(WorkloadAuthError):
    """503 -- the workload-plane identity infrastructure (SPIRE, or the
    ``agentweave``/``spiffe`` SDK layer itself) could not be initialized.

    Per ADR-0004 SS8, this must never fall back to "allow" or to a
    half-built plane: the workload plane (the ``:8443`` mTLS listener)
    simply does not come up. The human plane on ``:8000`` is unaffected,
    because this module never touches ``forge_security.oidc``.
    """

    def __init__(self, code: str = "workload_identity_unavailable") -> None:
        super().__init__(503, code)
