"""The credential-binding invariant must not fork between its two gates.

"Does this config carry a credential, and which hosts is it bound to" is
asked at two moments:

* WRITE time -- ``admin._manual_credential_binding`` /
  ``admin._openapi_credential_binding``, from the RAW BASE yaml dict, when an
  overlay repoints a destination.
* CONNECT time -- ``forge_agent.builder.openapi.resolve_bound_credential``,
  from the TYPED ``AuthConfig``, when the credential is actually attached.

Both delegate to the single predicate
``forge_security.egress.binding.credential_binding_from_raw_auth``. These
tests are the regression guard: if a future ``AuthType`` or a change to the
origin-derivation rule updates only one path, the parity assertions fail.
"""

from __future__ import annotations

import pytest
from forge_agent.builder.openapi import resolve_bound_credential
from forge_config.schema import AuthConfig, AuthType, ManualToolAPI, SecretRef, SecretSource
from forge_gateway.routes.admin import (
    _manual_credential_binding,
    _openapi_credential_binding,
    _resolved_host_from_manual_api,
)

_REF = SecretRef(source=SecretSource.ENV, name="TOKEN_VAR")


class _StubResolver:
    """Resolves any ref to a fixed value -- these tests are about the binding
    rule, not about secret resolution."""

    def resolve(self, ref: SecretRef) -> str:
        return "s3cret-value"


def _raw_auth(auth_type: str, allowed_hosts: list[str] | None) -> dict[str, object]:
    raw: dict[str, object] = {"type": auth_type}
    if auth_type in {"bearer", "api_key"}:
        raw["token"] = {"source": "env", "name": "TOKEN_VAR"}
    if auth_type == "basic":
        raw["username"] = {"source": "env", "name": "USER_VAR"}
        raw["password"] = {"source": "env", "name": "PASS_VAR"}
    if allowed_hosts is not None:
        raw["allowed_hosts"] = allowed_hosts
    return raw


def _typed_auth(auth_type: str, allowed_hosts: list[str] | None) -> AuthConfig:
    kwargs: dict[str, object] = {"type": AuthType(auth_type)}
    if auth_type in {"bearer", "api_key"}:
        kwargs["token"] = _REF
    if auth_type == "basic":
        kwargs["username"] = _REF
        kwargs["password"] = _REF
    if allowed_hosts is not None:
        kwargs["allowed_hosts"] = allowed_hosts
    return AuthConfig(**kwargs)  # type: ignore[arg-type]


_AUTH_SHAPES = [
    ("none", None),
    ("none", ["pinned.example.com"]),
    ("bearer", None),
    ("bearer", []),
    ("bearer", ["Allowed.example.com", "*.other.com"]),
    ("api_key", None),
    ("api_key", ["allowed.example.com"]),
    ("basic", None),
    ("basic", ["allowed.example.com"]),
]

_DECLARED_URL = "https://Declared.Example.com/v1/thing"
_DECLARED_HOST = "declared.example.com"


@pytest.mark.parametrize(("auth_type", "allowed_hosts"), _AUTH_SHAPES)
class TestWriteTimeAndConnectTimeAgree:
    def test_manual_tool_binding_matches_typed_runtime(
        self, auth_type: str, allowed_hosts: list[str] | None
    ) -> None:
        base_tool = {
            "name": "t",
            "api": {"url": _DECLARED_URL, "auth": _raw_auth(auth_type, allowed_hosts)},
        }
        write_time = _manual_credential_binding(base_tool)

        cred = resolve_bound_credential(
            _typed_auth(auth_type, allowed_hosts),
            _StubResolver(),
            declared_host=_DECLARED_HOST,
        )
        connect_time = (bool(cred.headers), cred.allowed_hosts)

        assert write_time == connect_time

    def test_openapi_source_binding_matches_typed_runtime(
        self, auth_type: str, allowed_hosts: list[str] | None
    ) -> None:
        base_src = {
            "name": "s",
            "url": _DECLARED_URL,
            "auth": _raw_auth(auth_type, allowed_hosts),
        }
        write_time = _openapi_credential_binding(base_src)

        cred = resolve_bound_credential(
            _typed_auth(auth_type, allowed_hosts),
            _StubResolver(),
            declared_host=_DECLARED_HOST,
        )
        connect_time = (bool(cred.headers), cred.allowed_hosts)

        assert write_time == connect_time


class TestResolvedHostMatchesSchema:
    """The write-time origin derivation must agree with the real schema
    property (``ManualToolAPI.resolved_url``) rather than re-implement the
    ``base_url`` + ``endpoint`` precedence rule."""

    @pytest.mark.parametrize(
        "api",
        [
            {"url": "https://a.example.com/x"},
            {"base_url": "https://b.example.com", "endpoint": "/v1/y"},
            {"base_url": "https://b.example.com/", "endpoint": "/v1/y"},
            {"base_url": "https://b.example.com///", "endpoint": "/v1/y"},
            {
                "url": "https://a.example.com/x",
                "base_url": "https://b.example.com",
                "endpoint": "/v1/y",
            },
            {"url": "https://a.example.com/x", "base_url": "https://b.example.com"},
        ],
    )
    def test_agrees_with_manual_tool_api_resolved_url(self, api: dict[str, str]) -> None:
        expected = ManualToolAPI.model_validate(api).resolved_url
        assert _resolved_host_from_manual_api(api)[1] == expected

    @pytest.mark.parametrize(
        "api",
        [
            {},
            {"endpoint": "/v1/y"},
            {"base_url": "https://b.example.com"},
            {"url": 42},
            "not-a-dict",
        ],
    )
    def test_unresolvable_api_yields_no_destination(self, api: object) -> None:
        assert _resolved_host_from_manual_api(api) == (None, None)

    def test_malformed_unrelated_field_does_not_hide_the_destination(self) -> None:
        """FAIL-OPEN guard: only the destination fields are schema-validated.
        A malformed unrelated field in BASE (here a bogus ``auth.type``) must
        NOT make the destination look absent -- that would silently skip the
        write-time gate."""
        api = {"url": "https://a.example.com/x", "auth": {"type": "not-a-real-auth-type"}}
        assert _resolved_host_from_manual_api(api) == ("a.example.com", "https://a.example.com/x")
