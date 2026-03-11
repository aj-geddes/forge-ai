"""Tests for secret resolution."""

import pytest
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import SecretRef, SecretSource
from forge_config.secret_resolver import (
    CompositeSecretResolver,
    EnvSecretResolver,
    SecretResolver,
)


class TestEnvSecretResolver:
    def test_resolve_existing_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SECRET", "s3cret")
        resolver = EnvSecretResolver()
        ref = SecretRef(source=SecretSource.ENV, name="TEST_SECRET")
        assert resolver.resolve(ref) == "s3cret"

    def test_resolve_missing_var(self) -> None:
        resolver = EnvSecretResolver()
        ref = SecretRef(source=SecretSource.ENV, name="DEFINITELY_NOT_SET_12345")
        with pytest.raises(SecretResolutionError, match="not set"):
            resolver.resolve(ref)

    def test_rejects_non_env_source(self) -> None:
        resolver = EnvSecretResolver()
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="secret", key="key")
        with pytest.raises(SecretResolutionError, match="only handles env"):
            resolver.resolve(ref)


class TestCompositeSecretResolver:
    def test_delegates_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "value123")
        resolver = CompositeSecretResolver()
        ref = SecretRef(source=SecretSource.ENV, name="MY_KEY")
        assert resolver.resolve(ref) == "value123"

    def test_no_resolver_for_source(self) -> None:
        resolver = CompositeSecretResolver(resolvers={})
        ref = SecretRef(source=SecretSource.ENV, name="X")
        with pytest.raises(SecretResolutionError, match="No resolver"):
            resolver.resolve(ref)

    def test_register_custom_resolver(self) -> None:
        class FakeResolver:
            def resolve(self, ref: SecretRef) -> str:
                return "fake-value"

        resolver = CompositeSecretResolver(resolvers={})
        resolver.register(SecretSource.K8S_SECRET, FakeResolver())  # type: ignore[arg-type]
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="s", key="k")
        assert resolver.resolve(ref) == "fake-value"

    def test_conforms_to_protocol(self) -> None:
        assert isinstance(EnvSecretResolver(), SecretResolver)
        assert isinstance(CompositeSecretResolver(), SecretResolver)
