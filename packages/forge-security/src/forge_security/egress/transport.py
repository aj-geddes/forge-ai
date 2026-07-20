"""Connect-time pinned-IP SSRF enforcement for httpx/httpcore (ADR-0006).

This is the authoritative plane. A custom ``httpcore.AsyncNetworkBackend``
(``GuardedBackend``) is swapped into ``httpx.AsyncHTTPTransport``'s pool. Inside
``connect_tcp`` it resolves the host, validates every candidate address, and
connects the inner backend to a LITERAL pinned IP in one shot -- so the IP that
was validated IS the IP that is connected. The DNS-rebind / TOCTOU window
between "validate" and "connect" is eliminated, not merely narrowed.

SNI, the ``Host`` header, and certificate verification are preserved: httpcore
derives the TLS ``server_hostname`` and httpx derives ``Host`` from the request
ORIGIN, independently of the literal IP we hand to ``connect_tcp`` (verified
against httpcore 1.0.9). Redirects are safe by construction -- each new origin
opens a fresh pooled connection through the same backend and is re-validated --
and ``follow_redirects`` defaults to ``False`` as belt-and-braces.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import anyio
import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from forge_security.egress.classify import (
    candidate_ips,
    host_matches,
    is_blocked_hostname,
    is_internal_ip,
)

if TYPE_CHECKING:
    from forge_config.schema import EgressPolicy


class SSRFConnectBlocked(httpcore.ConnectError):
    """Raised at connect time when the resolved/pinned IP is internal or the
    host is denied by policy. A subclass of ``httpcore.ConnectError`` so it
    surfaces through httpx as a connection failure."""


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """An ``AsyncNetworkBackend`` that validates-then-pins the target IP.

    Resolution runs off the event loop via ``anyio.to_thread.run_sync`` so the
    blocking ``getaddrinfo`` in ``candidate_ips`` does not stall the loop.
    """

    def __init__(
        self,
        policy: EgressPolicy | None = None,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = policy
        self._inner = inner if inner is not None else AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if is_blocked_hostname(host):
            raise SSRFConnectBlocked(f"blocked internal hostname: {host!r}")

        ips = await anyio.to_thread.run_sync(candidate_ips, host)
        if not ips:
            raise SSRFConnectBlocked(f"could not resolve host to any address: {host!r}")
        if any(is_internal_ip(ip) for ip in ips):
            raise SSRFConnectBlocked(
                f"refusing to connect to internal address for host {host!r}: "
                f"{[str(i) for i in ips]}"
            )
        if (
            self._policy is not None
            and getattr(self._policy, "enabled", False)
            and self._policy.allowed_hosts
            and not host_matches(host, frozenset(self._policy.allowed_hosts))
        ):
            raise SSRFConnectBlocked(f"host not in egress allow-list: {host!r}")

        # Pin the validated literal IP: the address validated IS the address
        # connected, so a rebind between validate and connect cannot occur.
        pinned = str(ips[0])
        return await self._inner.connect_tcp(
            pinned,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise SSRFConnectBlocked("unix-socket egress is not permitted through the SSRF guard")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SSRFGuardedTransport(httpx.AsyncHTTPTransport):
    """An ``httpx.AsyncHTTPTransport`` whose network backend is guarded."""

    def __init__(
        self,
        *,
        policy: EgressPolicy | None = None,
        verify: ssl.SSLContext | bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(verify=verify, **kwargs)
        # Private-attr coupling to httpcore's pool internals. Pinned via the
        # httpx/httpcore version constraints and guarded by
        # ``test_backend_invoked_smoke`` so a future rename fails loudly.
        self._pool._network_backend = GuardedBackend(policy)


def make_guarded_client(
    *,
    policy: EgressPolicy | None = None,
    verify: ssl.SSLContext | bool = True,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """The ONLY sanctioned outbound httpx client.

    Defaults ``follow_redirects=False`` (belt-and-braces; hops are re-validated
    anyway) and applies bounded connect/read timeouts. Pass ``verify=<ssl
    context>`` for the mTLS peer path -- SNI and cert verification still bind to
    the original hostname.
    """
    transport = SSRFGuardedTransport(policy=policy, verify=verify)
    kwargs.setdefault("follow_redirects", False)
    kwargs.setdefault("timeout", httpx.Timeout(10.0, connect=5.0))
    return httpx.AsyncClient(transport=transport, **kwargs)
