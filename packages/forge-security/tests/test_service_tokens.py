"""Tests for forge_security.oidc.service_tokens.ServiceTokenVerifier
(ADR-0001 SS5)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from forge_config.schema import ServiceToken
from forge_security.oidc.errors import AuthError
from forge_security.oidc.service_tokens import SERVICE_TOKEN_PREFIX, ServiceTokenVerifier


def _digest(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


CI_TOKEN_PLAINTEXT = "forge_sk_ci-deployer_9xQ7v1pABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


def _make_verifier(*tokens: ServiceToken, now: datetime | None = None) -> ServiceTokenVerifier:
    clock_value = now or datetime(2026, 1, 1, tzinfo=UTC)
    return ServiceTokenVerifier(tokens, clock=lambda: clock_value)


class TestValidToken:
    def test_valid_token_resolves_to_principal_with_declared_roles(self):
        token = ServiceToken(
            id="ci-deployer",
            secret_sha256=_digest(CI_TOKEN_PLAINTEXT),
            roles=["admin"],
        )
        verifier = _make_verifier(token)

        info = verifier.verify(CI_TOKEN_PLAINTEXT)

        assert info.token_id == "ci-deployer"
        assert info.roles == ["admin"]


class TestUnknownAndWrongToken:
    def test_unknown_token_id_rejected(self):
        verifier = _make_verifier()  # no tokens configured

        with pytest.raises(AuthError) as exc:
            verifier.verify(CI_TOKEN_PLAINTEXT)

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"

    def test_wrong_secret_rejected(self):
        token = ServiceToken(
            id="ci-deployer", secret_sha256=_digest(CI_TOKEN_PLAINTEXT), roles=["admin"]
        )
        verifier = _make_verifier(token)

        with pytest.raises(AuthError) as exc:
            verifier.verify("forge_sk_ci-deployer_wrong-secret-value-entirely")

        assert exc.value.status == 401
        assert exc.value.code == "invalid_token"


class TestExpiry:
    def test_expired_token_rejected(self):
        token = ServiceToken(
            id="ci-deployer",
            secret_sha256=_digest(CI_TOKEN_PLAINTEXT),
            roles=["admin"],
            expires_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        verifier = _make_verifier(token, now=datetime(2026, 1, 1, tzinfo=UTC))

        with pytest.raises(AuthError) as exc:
            verifier.verify(CI_TOKEN_PLAINTEXT)

        assert exc.value.status == 401
        assert exc.value.code == "token_expired"

    def test_token_without_expiry_never_expires(self):
        token = ServiceToken(
            id="ci-deployer",
            secret_sha256=_digest(CI_TOKEN_PLAINTEXT),
            roles=["admin"],
            expires_at=None,
        )
        verifier = _make_verifier(token, now=datetime(2099, 1, 1, tzinfo=UTC))

        info = verifier.verify(CI_TOKEN_PLAINTEXT)
        assert info.token_id == "ci-deployer"

    def test_token_before_expiry_is_valid(self):
        token = ServiceToken(
            id="ci-deployer",
            secret_sha256=_digest(CI_TOKEN_PLAINTEXT),
            roles=["admin"],
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        verifier = _make_verifier(token, now=datetime(2026, 1, 1, tzinfo=UTC))

        info = verifier.verify(CI_TOKEN_PLAINTEXT)
        assert info.token_id == "ci-deployer"


class TestPrefix:
    def test_token_without_forge_sk_prefix_is_not_treated_as_service_token(self):
        verifier = _make_verifier()

        with pytest.raises(AuthError) as exc:
            verifier.verify("some-other-credential-format")

        assert exc.value.status == 401
        assert exc.value.code == "invalid_credential_format"

    def test_prefix_constant_matches_adr(self):
        assert SERVICE_TOKEN_PREFIX == "forge_sk_"


class TestConstantTimeComparison:
    def test_comparison_is_constant_time(self, monkeypatch: pytest.MonkeyPatch):
        token = ServiceToken(
            id="ci-deployer", secret_sha256=_digest(CI_TOKEN_PLAINTEXT), roles=["admin"]
        )
        verifier = _make_verifier(token)

        calls: list[tuple[str, str]] = []
        import forge_security.oidc.service_tokens as st_module

        real_compare_digest = st_module.hmac.compare_digest

        def spy(a: str, b: str) -> bool:
            calls.append((a, b))
            return real_compare_digest(a, b)

        monkeypatch.setattr(st_module.hmac, "compare_digest", spy)

        verifier.verify(CI_TOKEN_PLAINTEXT)

        assert len(calls) >= 1
