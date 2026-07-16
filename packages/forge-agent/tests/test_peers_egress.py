"""Tests for the A2A peer egress SSRF guard (ADR-0006, SLICE 5).

PeerCaller routes BOTH its internally managed mTLS client and its
non-mTLS fallback client through ``make_guarded_client`` so every peer
connection gets connect-time pinned-IP internal-address blocking -- the
DNS-rebind window between ``validate_peer_endpoint`` (write time) and the
actual connect is closed. An injected ``http_client`` (test/DI seam) still
bypasses the guard. The mTLS path preserves ``verify=<ssl context>`` so
SNI and certificate verification remain bound to the peer hostname.
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from forge_agent.agent.peers import PeerCaller, PeerCallError
from forge_config.schema import EgressPolicy, PeerAgent, TrustLevel
from forge_security.egress import transport as transport_mod


def _make_peer(
    name: str = "data-forge",
    endpoint: str = "https://peer.example.com",
) -> PeerAgent:
    return PeerAgent(
        name=name,
        endpoint=endpoint,
        trust_level=TrustLevel.HIGH,
        capabilities=["query"],
    )


def _make_success_response() -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {
        "status": "completed",
        "result": {"ok": True},
        "error": None,
    }
    response.raise_for_status = MagicMock()
    return response


class TestFallbackEgressGuard:
    """The non-mTLS fallback path (identity is None) must go through the
    connect-time SSRF guard, not a bare httpx client."""

    @pytest.mark.anyio
    async def test_fallback_blocks_internal_ip_at_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A peer endpoint whose host resolves to an internal IP is refused
        at connect on the fallback path -- proving the fallback client is the
        real guarded client (no mock), blocked at the socket layer."""
        peer = _make_peer()
        caller = PeerCaller([peer], egress_policy=EgressPolicy())

        # At connect time the peer host resolves to an internal address.
        monkeypatch.setattr(
            transport_mod,
            "candidate_ips",
            lambda h: [ipaddress.ip_address("10.0.0.5")],
        )

        try:
            with pytest.raises(PeerCallError) as exc:
                await caller.call_peer("data-forge", "task", {})
            assert "peer.example.com" in str(exc.value)
            assert "internal" in str(exc.value).lower()
        finally:
            await caller.aclose()

    @pytest.mark.anyio
    async def test_fallback_builds_guarded_client_with_policy(self) -> None:
        """The fallback branch builds its client via ``make_guarded_client``
        with the threaded egress policy and NO ``verify`` override."""
        policy = EgressPolicy(allowed_hosts=["peer.example.com"])
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_success_response()

        with patch("forge_agent.agent.peers.make_guarded_client", return_value=mock_client) as mk:
            caller = PeerCaller([_make_peer()], egress_policy=policy)
            result = await caller.call_peer("data-forge", "task", {})

        assert result.status == "completed"
        mk.assert_called_once_with(policy=policy)


class TestMtlsEgressGuard:
    """The mTLS path (identity present) keeps its workload SSLContext but is
    ALSO built through the guard so a peer endpoint rebinding to an internal
    IP at connect is refused, without weakening SNI/cert verification."""

    @pytest.mark.anyio
    async def test_mtls_client_built_through_guard_preserves_verify(self) -> None:
        from forge_agent.agent import peers as peers_module

        expected_id = "spiffe://hvslocal/ns/dev/sa/data-forge"
        peer = _make_peer().model_copy(update={"spiffe_id": expected_id})

        identity = AsyncMock()
        identity.create_tls_context.return_value = "fake-ssl-ctx"

        response = _make_success_response()

        class _FakeSSLObject:
            def getpeercert(self, binary_form: bool = False) -> bytes:
                return b"cert"

        class _FakeNetworkStream:
            def get_extra_info(self, name: str) -> object:
                return _FakeSSLObject()

        response.extensions = {"network_stream": _FakeNetworkStream()}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = response
        policy = EgressPolicy()

        with (
            patch("forge_agent.agent.peers.make_guarded_client", return_value=mock_client) as mk,
            patch.object(
                peers_module,
                "_probe_peer_tls_identity",
                new=AsyncMock(return_value=expected_id),
            ),
            patch.object(peers_module, "extract_spiffe_id_from_cert", return_value=expected_id),
        ):
            caller = PeerCaller([peer], identity=identity, egress_policy=policy)
            result = await caller.call_peer("data-forge", "task", {})

        assert result.status == "completed"
        # verify (the workload SSLContext) is preserved AND the policy is threaded.
        mk.assert_called_once_with(policy=policy, verify="fake-ssl-ctx")
        identity.create_tls_context.assert_awaited_once_with(server=False)
