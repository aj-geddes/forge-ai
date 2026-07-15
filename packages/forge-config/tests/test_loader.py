"""Tests for config loader."""

from pathlib import Path

import pytest
from forge_config.exceptions import ConfigLoadError
from forge_config.loader import load_config
from forge_config.schema import AgentMode

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadConfig:
    def test_load_valid_config(self) -> None:
        config = load_config(FIXTURES / "valid_config.yaml")
        assert config.metadata.name == "test-forge"
        assert config.llm.default_model == "gpt-4o"
        assert len(config.tools.manual_tools) == 1
        assert config.tools.manual_tools[0].name == "echo"

    def test_load_minimal_config(self) -> None:
        config = load_config(FIXTURES / "minimal_config.yaml")
        assert config.metadata.name == "minimal"
        # Defaults should fill in
        assert config.llm.default_model == "gpt-4o"
        assert config.security.rate_limit_rpm == 60

    def test_load_config_omitting_mode_defaults_every_agent_to_passive(self) -> None:
        """ADR-0005 Phase 0: an existing config with no ``mode`` field on
        any persona must parse and behave exactly as today (default
        passive), i.e. this is a pure backward-compatibility guarantee."""
        config = load_config(FIXTURES / "valid_config.yaml")
        assert all(agent.mode == AgentMode.PASSIVE for agent in config.agents.agents)

    def test_load_config_with_declared_active_agent_mode(self) -> None:
        """ADR-0005 Phase 0: a persona may declare ``mode: active`` and the
        config loads -- this is inert metadata in Phase 0, no runtime
        behavior change."""
        config = load_config(FIXTURES / "active_mode_agent_config.yaml")
        agents_by_name = {agent.name: agent for agent in config.agents.agents}
        assert agents_by_name["assistant"].mode == AgentMode.PASSIVE
        assert agents_by_name["autonomous-analyst"].mode == AgentMode.ACTIVE

    def test_config_omitting_agentweave_yields_workload_disabled(self) -> None:
        """HIGH finding fix: minimal_config.yaml has no security block at
        all, let alone security.agentweave -- loading it must never
        silently enable the AgentWeave workload (SPIFFE+OPA mTLS) plane."""
        config = load_config(FIXTURES / "minimal_config.yaml")
        assert config.security.agentweave.enabled is False

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


class TestPruneNoopOverlaySubset:
    """Finding [MEDIUM]: prune must drop overlay entries that are a
    structural SUBSET of BASE (e.g. a PATCH that stored only {name,
    description}), not just byte-identical ones. Otherwise a partial PATCH
    lingers forever as drift and shadows later BASE edits to that entity."""

    def test_prune_drops_partial_subset_patch(self) -> None:
        from forge_config.loader import prune_noop_overlay

        base_editable = {
            "tools": {"manual_tools": [{"name": "t", "description": "d", "api": {"url": "u"}}]}
        }
        # A PATCH persisted only {name, description}: never byte-equal to the
        # promoted full BASE entity, but a strict subset of it -> a no-op.
        overlay_content = {"tools": {"manual_tools": [{"name": "t", "description": "d"}]}}
        pruned = prune_noop_overlay(
            overlay_content, base_editable=base_editable, overlay_base_rev="stale-hash"
        )
        assert pruned == {}

    def test_prune_keeps_partial_patch_that_actually_differs(self) -> None:
        from forge_config.loader import prune_noop_overlay

        base_editable = {
            "tools": {"manual_tools": [{"name": "t", "description": "old", "api": {"url": "u"}}]}
        }
        overlay_content = {"tools": {"manual_tools": [{"name": "t", "description": "new"}]}}
        pruned = prune_noop_overlay(
            overlay_content, base_editable=base_editable, overlay_base_rev="stale-hash"
        )
        assert pruned == overlay_content
