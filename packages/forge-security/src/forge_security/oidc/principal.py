"""The verified identity produced by principal resolution (ADR-0001 SS5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PrincipalKind = Literal["user", "service", "dev"]


@dataclass(frozen=True)
class Principal:
    """A cryptographically verified caller identity plus its resolved
    authorization (roles/permissions).

    ``kind`` distinguishes the three credential paths (ADR-0001 SS5.1):
    ``"user"`` for a session cookie or a Dex bearer JWT, ``"service"`` for
    a ``forge_sk_`` service token, and ``"dev"`` for the (opt-in,
    doubly-gated) ``dev_insecure`` mode.
    """

    kind: PrincipalKind
    sub: str
    email: str | None = None
    name: str | None = None
    groups: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    permissions: frozenset[str] = field(default_factory=frozenset)
    token_id: str | None = None
