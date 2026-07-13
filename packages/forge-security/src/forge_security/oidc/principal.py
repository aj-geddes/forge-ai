"""The verified identity produced by principal resolution (ADR-0001 SS5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PrincipalKind = Literal["user", "service", "dev", "workload"]


@dataclass(frozen=True)
class Principal:
    """A cryptographically verified caller identity plus its resolved
    authorization (roles/permissions).

    ``kind`` distinguishes the credential paths: the three human paths
    (ADR-0001 SS5.1) -- ``"user"`` for a session cookie or a Dex bearer
    JWT, ``"service"`` for a ``forge_sk_`` service token, and ``"dev"``
    for the (opt-in, doubly-gated) ``dev_insecure`` mode -- plus
    ``"workload"`` (ADR-0004 SS4) for an mTLS-verified AI-workload peer on
    the SPIFFE/OPA plane. ``forge_security.oidc.resolve_principal`` (the
    human resolver) never produces ``kind="workload"``: it has no input
    that could -- the workload kind is only ever constructed by
    ``forge_security.workload.resolver.resolve_workload_principal`` from
    an already-verified mTLS peer certificate on the separate ``:8443``
    listener (ADR-0004 SS3/SS4).
    """

    kind: PrincipalKind
    sub: str
    email: str | None = None
    name: str | None = None
    groups: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    permissions: frozenset[str] = field(default_factory=frozenset)
    token_id: str | None = None
    spiffe_id: str | None = None
    """The peer's SPIFFE ID (set only when ``kind="workload"``). Always
    ``None`` for the human kinds (``user``/``service``/``dev``)."""
