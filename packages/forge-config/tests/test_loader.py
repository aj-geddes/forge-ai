"""Tests for config loader."""

from pathlib import Path

import pytest
from forge_config.exceptions import ConfigLoadError
from forge_config.loader import load_config

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadConfig:
    def test_load_valid_config(self) -> None:
        config = load_config(FIXTURES / "valid_config.yaml")
        assert config.metadata.name == "test-forge"
        assert config.llm.default_model == "gpt-4o"
        assert len(config.tools.manual) == 1
        assert config.tools.manual[0].name == "echo"

    def test_load_minimal_config(self) -> None:
        config = load_config(FIXTURES / "minimal_config.yaml")
        assert config.metadata.name == "minimal"
        # Defaults should fill in
        assert config.llm.default_model == "gpt-4o"
        assert config.security.rate_limit_rpm == 60

    def test_file_not_found(self) -> None:
        with pytest.raises(ConfigLoadError, match="not found"):
            load_config("/nonexistent/forge.yaml")

    def test_invalid_yaml(self) -> None:
        with pytest.raises(ConfigLoadError, match="Invalid YAML"):
            load_config(FIXTURES / "invalid_yaml.yaml")

    def test_env_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_NAME", "my-forge")
        monkeypatch.setenv("FORGE_ENV", "staging")

        config = load_config(FIXTURES / "env_config.yaml")
        assert config.metadata.name == "my-forge"
        assert config.metadata.environment == "staging"

    def test_env_substitution_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # FORGE_NAME not set - should use default
        monkeypatch.delenv("FORGE_NAME", raising=False)
        monkeypatch.setenv("FORGE_ENV", "prod")

        config = load_config(FIXTURES / "env_config.yaml")
        assert config.metadata.name == "default-forge"

    def test_env_overlay_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_NAME", "should-not-appear")
        monkeypatch.setenv("FORGE_ENV", "should-not-appear")

        config = load_config(FIXTURES / "env_config.yaml", env_overlay=False)
        assert config.metadata.name == "${FORGE_NAME:default-forge}"

    def test_empty_config_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        config = load_config(empty)
        assert config.metadata.name == "forge"  # All defaults

    def test_non_dict_root(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigLoadError, match="must be a mapping"):
            load_config(bad)
