"""Tests for forge_security.secrets."""

import pytest
from forge_config.exceptions import SecretResolutionError
from forge_config.schema import SecretRef, SecretSource
from forge_security.secrets import ForgeCompositeSecretResolver, K8sSecretResolver


class TestK8sSecretResolver:
    def test_resolve_reads_file(self, tmp_path):
        secret_dir = tmp_path / "my-secret"
        secret_dir.mkdir()
        (secret_dir / "password").write_text("s3cret\n")

        resolver = K8sSecretResolver(base_path=str(tmp_path))
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="my-secret", key="password")
        assert resolver.resolve(ref) == "s3cret"

    def test_resolve_missing_file_raises(self, tmp_path):
        resolver = K8sSecretResolver(base_path=str(tmp_path))
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="missing", key="key")
        with pytest.raises(SecretResolutionError, match="not found"):
            resolver.resolve(ref)

    def test_resolve_wrong_source_raises(self, tmp_path):
        resolver = K8sSecretResolver(base_path=str(tmp_path))
        ref = SecretRef(source=SecretSource.ENV, name="FOO")
        with pytest.raises(SecretResolutionError, match="k8s_secret"):
            resolver.resolve(ref)

    def test_resolve_missing_key_rejected_by_schema(self, tmp_path):
        """Pydantic's model validator rejects k8s_secret refs without a key."""
        with pytest.raises(ValueError):
            SecretRef(source=SecretSource.K8S_SECRET, name="s", key=None)


class TestForgeCompositeSecretResolver:
    def test_resolve_env(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET_VAR", "env_value")
        resolver = ForgeCompositeSecretResolver()
        ref = SecretRef(source=SecretSource.ENV, name="TEST_SECRET_VAR")
        assert resolver.resolve(ref) == "env_value"

    def test_resolve_k8s(self, tmp_path):
        secret_dir = tmp_path / "db-creds"
        secret_dir.mkdir()
        (secret_dir / "user").write_text("admin")

        k8s = K8sSecretResolver(base_path=str(tmp_path))
        resolver = ForgeCompositeSecretResolver(
            resolvers={
                SecretSource.ENV: ForgeCompositeSecretResolver()._resolvers[SecretSource.ENV],
                SecretSource.K8S_SECRET: k8s,
            }
        )
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="db-creds", key="user")
        assert resolver.resolve(ref) == "admin"

    def test_register_custom_resolver(self, tmp_path):
        resolver = ForgeCompositeSecretResolver()
        k8s = K8sSecretResolver(base_path=str(tmp_path))
        resolver.register(SecretSource.K8S_SECRET, k8s)

        secret_dir = tmp_path / "token"
        secret_dir.mkdir()
        (secret_dir / "value").write_text("tok123")

        ref = SecretRef(source=SecretSource.K8S_SECRET, name="token", key="value")
        assert resolver.resolve(ref) == "tok123"

    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=False)
        resolver = ForgeCompositeSecretResolver()
        ref = SecretRef(source=SecretSource.ENV, name="NONEXISTENT_VAR_12345")
        with pytest.raises(SecretResolutionError):
            resolver.resolve(ref)
