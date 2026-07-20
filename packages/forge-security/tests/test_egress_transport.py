"""Tests for the connect-time pinned-IP transport plane (ADR-0006).

No real network is used: a fake ``httpcore`` backend/stream stands in for the
socket layer so we can assert the pinned IP, TLS SNI, Host header, and rebind
refusal precisely.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable
from typing import Any

import httpcore
import httpx
import pytest
from forge_config.schema import EgressPolicy
from forge_security.egress import transport as transport_mod
from forge_security.egress.transport import (
    GuardedBackend,
    SSRFConnectBlocked,
    SSRFGuardedTransport,
    make_guarded_client,
)


def _ip(addr: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(addr)


def _http_response(body: bytes = b"{}", status: int = 200, extra: bytes = b"") -> bytes:
    return (
        b"HTTP/1.1 %d OK\r\ncontent-length: %d\r\nconnection: close\r\n%s\r\n"
        % (status, len(body), extra)
        + body
    )


class FakeStream(httpcore.AsyncNetworkStream):
    """A canned HTTP/1.1 response stream that records writes and TLS SNI."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.written = bytearray()
        self.tls_server_hostname: str | None = None

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written += buffer

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.tls_server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class FakeBackend(httpcore.AsyncNetworkBackend):
    """Records connect targets and hands back a FakeStream per host."""

    def __init__(self, response_for: Callable[[str], bytes] | bytes) -> None:
        self._response_for = response_for
        self.connect_calls: list[tuple[str, int]] = []
        self.last_stream: FakeStream | None = None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connect_calls.append((host, port))
        resp = self._response_for(host) if callable(self._response_for) else self._response_for
        stream = FakeStream([resp])
        self.last_stream = stream
        return stream

    async def connect_unix_socket(self, *a: Any, **k: Any) -> httpcore.AsyncNetworkStream:
        raise NotImplementedError

    async def sleep(self, seconds: float) -> None:
        return None


def _transport_with_fake(fake: FakeBackend, **kw: Any) -> SSRFGuardedTransport:
    t = SSRFGuardedTransport(**kw)
    # Replace the pinned-IP backend's INNER socket layer with the fake.
    t._pool._network_backend._inner = fake  # type: ignore[attr-defined]
    return t


class TestBackendSmoke:
    def test_backend_invoked_smoke(self) -> None:
        """Fails loudly if a future httpcore refactor renames the private
        ``_pool._network_backend`` seam we depend on."""
        t = SSRFGuardedTransport(policy=None)
        assert isinstance(t._pool._network_backend, GuardedBackend)

    def test_make_guarded_client_defaults(self) -> None:
        client = make_guarded_client()
        assert client.follow_redirects is False
        assert isinstance(client._transport, SSRFGuardedTransport)


class TestPinningAndRebind:
    async def test_guarded_backend_pins_literal_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        fake = FakeBackend(_http_response())
        backend = GuardedBackend(policy=None, inner=fake)
        await backend.connect_tcp("example.com", 443)
        # Connected to the validated literal IP, not the hostname.
        assert fake.connect_calls == [("93.184.216.34", 443)]

    async def test_rebind_public_then_private_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case the pre-existing suite could not express: PUBLIC at
        validate, PRIVATE at connect -> refused at the socket."""
        from forge_security.egress import classify

        # 1. First-line validate sees a PUBLIC address -> passes.
        monkeypatch.setattr(classify, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        assert classify.validate_endpoint("https://rebind.example/") is True

        # 2. At connect time the SAME name now resolves to metadata -> blocked.
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("169.254.169.254")])
        backend = GuardedBackend(policy=None, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked):
            await backend.connect_tcp("rebind.example", 443)

    async def test_internal_ip_literal_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("10.0.0.5")])
        backend = GuardedBackend(policy=None, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked):
            await backend.connect_tcp("internal.example", 443)

    async def test_unresolvable_host_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [])
        backend = GuardedBackend(policy=None, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked):
            await backend.connect_tcp("nope.invalid", 443)

    async def test_unix_socket_rejected(self) -> None:
        backend = GuardedBackend(policy=None, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked):
            await backend.connect_unix_socket("/var/run/x.sock")

    async def test_blocked_hostname_short_circuits(self) -> None:
        backend = GuardedBackend(policy=None, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked):
            await backend.connect_tcp("metadata.google.internal", 80)


class TestEnabledPolicyAllowList:
    """The authoritative plane with an ENABLED policy carrying an allow-list.

    Every other test in this module passes ``policy=None``, so without these
    the whole ``policy.enabled + allowed_hosts`` arm of ``connect_tcp`` -- and
    the config->transport policy plumbing feeding it -- is unexercised.
    """

    async def test_allow_listed_host_connects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        fake = FakeBackend(_http_response())
        policy = EgressPolicy(enabled=True, allowed_hosts=["api.example.com"])
        backend = GuardedBackend(policy=policy, inner=fake)
        await backend.connect_tcp("api.example.com", 443)
        # Pinned to the validated literal IP, not the hostname.
        assert fake.connect_calls == [("93.184.216.34", 443)]

    async def test_host_not_in_allow_list_is_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        policy = EgressPolicy(enabled=True, allowed_hosts=["api.example.com"])
        backend = GuardedBackend(policy=policy, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked, match="allow-list"):
            await backend.connect_tcp("evil.example", 443)

    async def test_wildcard_allow_list_admits_subdomain_and_apex(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        fake = FakeBackend(_http_response())
        policy = EgressPolicy(enabled=True, allowed_hosts=["*.example.com"])
        backend = GuardedBackend(policy=policy, inner=fake)
        await backend.connect_tcp("a.example.com", 443)
        await backend.connect_tcp("example.com", 443)
        assert fake.connect_calls == [("93.184.216.34", 443), ("93.184.216.34", 443)]

    async def test_internal_ip_blocked_even_when_host_allow_listed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defense in depth: the allow-list is a permit, never an override of
        the IP-layer deny. An allow-listed name that rebinds to metadata is
        still refused at the socket."""
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("169.254.169.254")])
        policy = EgressPolicy(enabled=True, allowed_hosts=["api.example.com"])
        backend = GuardedBackend(policy=policy, inner=FakeBackend(_http_response()))
        with pytest.raises(SSRFConnectBlocked, match="internal address"):
            await backend.connect_tcp("api.example.com", 443)

    async def test_disabled_policy_does_not_impose_allow_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        fake = FakeBackend(_http_response())
        policy = EgressPolicy(enabled=False, allowed_hosts=["api.example.com"])
        backend = GuardedBackend(policy=policy, inner=fake)
        await backend.connect_tcp("other.example", 443)
        assert fake.connect_calls == [("93.184.216.34", 443)]

    async def test_make_guarded_client_plumbs_policy_to_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The config->transport seam: a policy handed to ``make_guarded_client``
        must actually reach the backend that enforces it."""
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        policy = EgressPolicy(enabled=True, allowed_hosts=["api.example.com"])
        client = make_guarded_client(policy=policy)
        backend = client._transport._pool._network_backend  # type: ignore[attr-defined]
        backend._inner = FakeBackend(_http_response())
        async with client:
            with pytest.raises(httpx.ConnectError, match="allow-list"):
                await client.get("https://evil.example/x")


class TestSniHostAndRedirect:
    async def test_sni_and_host_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transport_mod, "candidate_ips", lambda h: [_ip("93.184.216.34")])
        fake = FakeBackend(_http_response(b'{"ok":true}'))
        t = _transport_with_fake(fake, policy=None)
        async with httpx.AsyncClient(transport=t) as client:
            r = await client.get("https://example.com/path")
        assert r.status_code == 200
        # Socket target is the pinned IP...
        assert fake.connect_calls == [("93.184.216.34", 443)]
        # ...but TLS SNI and Host stay bound to the original hostname.
        assert fake.last_stream is not None
        assert fake.last_stream.tls_server_hostname == "example.com"
        assert b"host: example.com" in bytes(fake.last_stream.written).lower()

    async def test_redirect_hop_revalidated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 302 to an internal host is refused when the hop is followed --
        each new origin re-enters the guarded backend."""

        def candidates(host: str) -> list:
            return [_ip("169.254.169.254")] if host == "evil.internal" else [_ip("93.184.216.34")]

        monkeypatch.setattr(transport_mod, "candidate_ips", candidates)

        def response_for(host: str) -> bytes:
            if host == "93.184.216.34":
                return _http_response(b"", status=302, extra=b"location: http://evil.internal/\r\n")
            return _http_response(b"secret")

        fake = FakeBackend(response_for)
        t = _transport_with_fake(fake, policy=None)
        async with httpx.AsyncClient(transport=t, follow_redirects=True) as client:
            with pytest.raises(httpx.ConnectError) as exc:
                await client.get("https://good.example/")
        # The mapped httpx error carries our SSRF message.
        assert "internal" in str(exc.value).lower()
