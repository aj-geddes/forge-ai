"""Tests for the BASE+OVERLAY resolution model: forge_config.loader's
deep_merge, editable_sections/compute_base_rev, prune_noop_overlay, and
load_effective_config.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from forge_config.exceptions import ConfigValidationError
from forge_config.loader import (
    canonicalize,
    compact_overlay,
    compute_base_rev,
    deep_merge,
    editable_sections,
    load_effective_config,
    prune_noop_overlay,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --- deep_merge: pure function, property/round-trip style ---


class TestDeepMergeScalarsAndDicts:
    def test_recursive_dict_merge(self) -> None:
        base = {"llm": {"default_model": "a", "temperature": 0.5}}
        overlay = {"llm": {"default_model": "b"}}
        merged = deep_merge(base, overlay)
        assert merged == {"llm": {"default_model": "b", "temperature": 0.5}}

    def test_overlay_key_absent_from_base_is_added(self) -> None:
        base = {"metadata": {"name": "forge"}}
        overlay = {"metadata": {"description": "new description"}}
        merged = deep_merge(base, overlay)
        assert merged["metadata"] == {"name": "forge", "description": "new description"}

    def test_empty_overlay_returns_base_content(self) -> None:
        base = {"llm": {"default_model": "a"}}
        assert deep_merge(base, {}) == base

    def test_does_not_mutate_inputs(self) -> None:
        base = {"llm": {"default_model": "a"}}
        overlay = {"llm": {"default_model": "b"}}
        base_copy, overlay_copy = copy.deepcopy(base), copy.deepcopy(overlay)
        deep_merge(base, overlay)
        assert base == base_copy
        assert overlay == overlay_copy

    def test_provenance_keys_are_ignored(self) -> None:
        base = {"llm": {"default_model": "a"}}
        overlay = {
            "_rev": 5,
            "_base_rev": "deadbeef",
            "_updated_by": "alice",
            "_updated_at": "2026-01-01T00:00:00Z",
        }
        merged = deep_merge(base, overlay)
        assert merged == base

    def test_non_name_keyed_list_is_replaced_wholesale(self) -> None:
        base = {"llm": {"fallback_models": ["a", "b"]}}
        overlay = {"llm": {"fallback_models": ["c"]}}
        merged = deep_merge(base, overlay)
        assert merged["llm"]["fallback_models"] == ["c"]


class TestDeepMergeNameKeyedLists:
    def test_tools_manual_tools_merge_by_name(self) -> None:
        base = {
            "tools": {
                "manual_tools": [
                    {"name": "a", "description": "base-a"},
                    {"name": "b", "description": "base-b"},
                ]
            }
        }
        overlay = {"tools": {"manual_tools": [{"name": "a", "description": "overlay-a"}]}}
        merged = deep_merge(base, overlay)
        by_name = {t["name"]: t for t in merged["tools"]["manual_tools"]}
        assert by_name["a"]["description"] == "overlay-a"
        assert by_name["b"]["description"] == "base-b"

    def test_new_named_entry_is_appended(self) -> None:
        base = {"tools": {"manual_tools": [{"name": "a"}]}}
        overlay = {"tools": {"manual_tools": [{"name": "z", "description": "new"}]}}
        merged = deep_merge(base, overlay)
        names = [t["name"] for t in merged["tools"]["manual_tools"]]
        assert names == ["a", "z"]

    def test_agents_and_peers_merge_by_name(self) -> None:
        base = {
            "agents": {
                "agents": [{"name": "assistant", "model": "gpt-4o"}],
                "peers": [{"name": "peer-a", "endpoint": "https://a.example.com"}],
            }
        }
        overlay = {
            "agents": {
                "agents": [{"name": "assistant", "model": "claude"}],
                "peers": [{"name": "peer-b", "endpoint": "https://b.example.com"}],
            }
        }
        merged = deep_merge(base, overlay)
        assert merged["agents"]["agents"][0]["model"] == "claude"
        peer_names = [p["name"] for p in merged["agents"]["peers"]]
        assert peer_names == ["peer-a", "peer-b"]

    def test_tombstone_deletes_named_entry(self) -> None:
        base = {"tools": {"manual_tools": [{"name": "a"}, {"name": "b"}]}}
        overlay = {"tools": {"manual_tools": [{"__deleted__": "a"}]}}
        merged = deep_merge(base, overlay)
        names = [t["name"] for t in merged["tools"]["manual_tools"]]
        assert names == ["b"]

    def test_tombstone_survives_re_merge_against_base(self) -> None:
        """A delete recorded in the overlay must keep deleting the entry
        every time the overlay is re-merged against BASE (BASE re-adding
        the entry on a future edit doesn't resurrect it -- the overlay
        always wins until the delete itself is dropped/promoted)."""
        base = {"tools": {"manual_tools": [{"name": "a"}, {"name": "b"}]}}
        overlay = {"tools": {"manual_tools": [{"__deleted__": "a"}]}}
        merged_once = deep_merge(base, overlay)
        merged_twice = deep_merge(merged_once, overlay)
        assert [t["name"] for t in merged_twice["tools"]["manual_tools"]] == ["b"]

    def test_tombstone_of_unknown_name_is_a_harmless_noop(self) -> None:
        base = {"tools": {"manual_tools": [{"name": "a"}]}}
        overlay = {"tools": {"manual_tools": [{"__deleted__": "does-not-exist"}]}}
        merged = deep_merge(base, overlay)
        assert [t["name"] for t in merged["tools"]["manual_tools"]] == ["a"]

    def test_determinism_and_idempotency(self) -> None:
        base = {"tools": {"manual_tools": [{"name": "a", "description": "x"}]}}
        overlay = {"tools": {"manual_tools": [{"name": "a", "description": "y"}]}}
        m1 = deep_merge(base, overlay)
        m2 = deep_merge(base, overlay)
        assert m1 == m2
        # Idempotent: merging an overlay that already matches the merged
        # result again changes nothing further.
        m3 = deep_merge(m1, overlay)
        assert m3 == m1


# --- canonicalize / compute_base_rev ---


class TestCanonicalization:
    def test_canonicalize_is_key_order_independent(self) -> None:
        assert canonicalize({"a": 1, "b": 2}) == canonicalize({"b": 2, "a": 1})

    def test_compute_base_rev_is_deterministic(self) -> None:
        config = {"tools": {"manual_tools": []}, "security": {"ignored": True}}
        assert compute_base_rev(config) == compute_base_rev(config)

    def test_compute_base_rev_ignores_non_editable_sections(self) -> None:
        a = {"tools": {"manual_tools": []}, "security": {"x": 1}}
        b = {"tools": {"manual_tools": []}, "security": {"x": 2}}
        assert compute_base_rev(a) == compute_base_rev(b)

    def test_compute_base_rev_changes_when_editable_content_changes(self) -> None:
        a = {"tools": {"manual_tools": []}}
        b = {"tools": {"manual_tools": [{"name": "x"}]}}
        assert compute_base_rev(a) != compute_base_rev(b)

    def test_editable_sections_extracts_only_whitelisted_keys(self) -> None:
        full = {
            "tools": {"manual_tools": []},
            "agents": {"agents": []},
            "llm": {"default_model": "a"},
            "metadata": {"name": "forge", "description": "hi"},
            "security": {"oidc": {}},
            "conversation_store": {"backend": "redis"},
        }
        result = editable_sections(full)
        assert set(result.keys()) == {"tools", "agents", "llm", "metadata"}
        assert result["metadata"] == {"description": "hi"}


# --- prune_noop_overlay: drift returns to zero after promotion ---


class TestPruneNoopOverlay:
    def test_no_pruning_when_base_rev_stamp_matches_current_base(self) -> None:
        base_editable = {"tools": {"manual_tools": [{"name": "a"}]}}
        overlay_content = {"tools": {"manual_tools": [{"name": "z"}]}}
        result = prune_noop_overlay(
            overlay_content,
            base_editable=base_editable,
            overlay_base_rev=compute_base_rev(base_editable),
        )
        assert result == overlay_content

    def test_no_stamp_means_nothing_pruned(self) -> None:
        overlay_content = {"tools": {"manual_tools": [{"name": "z"}]}}
        result = prune_noop_overlay(overlay_content, base_editable={}, overlay_base_rev=None)
        assert result == overlay_content

    def test_promoted_entry_is_pruned_once_base_moves(self) -> None:
        """Simulates promotion: the overlay added tool 'z' against an old
        base hash; BASE is then updated (by a human commit + ArgoCD
        reconcile) to literally include 'z'. The overlay's copy of 'z' is
        now a structural no-op and must be pruned."""
        old_base_editable = {"tools": {"manual_tools": [{"name": "a"}]}}
        overlay_base_rev = compute_base_rev(old_base_editable)

        overlay_content = {"tools": {"manual_tools": [{"name": "z", "description": "new tool"}]}}

        new_base_editable = {
            "tools": {
                "manual_tools": [
                    {"name": "a"},
                    {"name": "z", "description": "new tool"},
                ]
            }
        }

        result = prune_noop_overlay(
            overlay_content, base_editable=new_base_editable, overlay_base_rev=overlay_base_rev
        )
        assert result == {}

    def test_partially_promoted_overlay_keeps_unpromoted_entries(self) -> None:
        old_base_editable = {"tools": {"manual_tools": []}}
        overlay_base_rev = compute_base_rev(old_base_editable)

        overlay_content = {
            "tools": {
                "manual_tools": [
                    {"name": "promoted", "description": "already in base"},
                    {"name": "still-only-in-overlay", "description": "not yet promoted"},
                ]
            }
        }
        new_base_editable = {
            "tools": {"manual_tools": [{"name": "promoted", "description": "already in base"}]}
        }
        result = prune_noop_overlay(
            overlay_content, base_editable=new_base_editable, overlay_base_rev=overlay_base_rev
        )
        names = [t["name"] for t in result["tools"]["manual_tools"]]
        assert names == ["still-only-in-overlay"]

    def test_promoted_tombstone_delete_is_pruned(self) -> None:
        old_base_editable = {"tools": {"manual_tools": [{"name": "gone"}]}}
        overlay_base_rev = compute_base_rev(old_base_editable)
        overlay_content = {"tools": {"manual_tools": [{"__deleted__": "gone"}]}}
        new_base_editable = {"tools": {"manual_tools": []}}  # already removed in BASE
        result = prune_noop_overlay(
            overlay_content, base_editable=new_base_editable, overlay_base_rev=overlay_base_rev
        )
        assert result == {}


# --- compact_overlay: ALWAYS-SAFE structural compaction, base-move-agnostic ---
#
# Unlike prune_noop_overlay (which only prunes once BASE has moved past the
# overlay's stamped _base_rev), compact_overlay drops structurally-empty
# by-name-list/dict containers UNCONDITIONALLY -- e.g. after a runtime-only
# agent is created then deleted through the overlay, the resulting
# {"agents": {"agents": []}} scaffold is a no-op under deep_merge's by-name
# semantics REGARDLESS of whether BASE has moved, and must not linger as
# permanent, un-clearable drift_from_git=True.


class TestCompactOverlay:
    def test_empty_name_keyed_list_collapses_to_empty_dict(self) -> None:
        assert compact_overlay({"agents": {"agents": []}}) == {}

    def test_multiple_empty_name_keyed_lists_all_collapse(self) -> None:
        content = {"tools": {"manual_tools": []}, "agents": {"agents": []}}
        assert compact_overlay(content) == {}

    def test_tombstone_entry_is_preserved_not_treated_as_empty(self) -> None:
        content = {"agents": {"agents": [{"__deleted__": "x"}]}}
        assert compact_overlay(content) == content

    def test_real_entry_is_preserved(self) -> None:
        content = {"agents": {"agents": [{"name": "a", "model": "base-model"}]}}
        assert compact_overlay(content) == content

    def test_nested_empty_dict_collapses(self) -> None:
        assert compact_overlay({"tools": {}}) == {}

    def test_non_name_keyed_empty_list_is_left_untouched(self) -> None:
        """An empty list at a path OUTSIDE _NAME_KEYED_LIST_PATHS (e.g.
        llm.litellm.fallback_models) replaces BASE wholesale under
        deep_merge -- it is a real "clear this" instruction, not a no-op,
        so compact_overlay must never drop it."""
        content = {"llm": {"litellm": {"fallback_models": []}}}
        assert compact_overlay(content) == content


# --- load_effective_config: end-to-end ---


@pytest.fixture
def base_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "forge.yaml"
    path.write_text(
        yaml.dump(
            {
                "metadata": {"name": "test-forge", "description": "base description"},
                "llm": {"default_model": "gpt-4o"},
                "tools": {
                    "manual_tools": [
                        {
                            "name": "echo",
                            "description": "base echo",
                            "api": {"url": "https://example.com"},
                        }
                    ]
                },
                "security": {
                    "auth": {"mode": "dev_insecure"},
                    "oidc": {"enabled": False},
                    "service_tokens": {
                        "enabled": True,
                        "tokens": [{"id": "t1", "secret_sha256": "a" * 64, "roles": ["admin"]}],
                    },
                },
            }
        )
    )
    return path


class TestLoadEffectiveConfig:
    def test_overlay_absent_returns_base_only(self, base_config_path: Path) -> None:
        config = load_effective_config(base_config_path, None)
        assert config.metadata.name == "test-forge"
        assert len(config.tools.manual_tools) == 1

    def test_overlay_present_merges_correctly(self, base_config_path: Path, tmp_path: Path) -> None:
        # Phase-1 field-level split: a NEW tool needs a url (a base-only
        # destination), so the overlay may only EDIT a base-defined tool's
        # runtime-safe fields (here echo's description). A base-only field
        # smuggled into the overlay is pruned at load (project_overlay_safe).
        overlay_path = tmp_path / "forge.overlay.yaml"
        overlay_path.write_text(
            yaml.dump(
                {
                    "tools": {
                        "manual_tools": [
                            {
                                "name": "echo",
                                "description": "edited via overlay",
                                "api": {"url": "https://attacker.example.com"},
                            }
                        ]
                    },
                    "_rev": 1,
                }
            )
        )
        config = load_effective_config(base_config_path, overlay_path)
        names = {t.name for t in config.tools.manual_tools}
        assert names == {"echo"}
        echo = next(t for t in config.tools.manual_tools if t.name == "echo")
        # Runtime-safe description edit applied; the base-only url repoint in
        # the raw overlay file was dropped at load -- BASE's url wins.
        assert echo.description == "edited via overlay"
        assert echo.api.resolved_url == "https://example.com"

    def test_base_only_sections_always_come_from_base(
        self, base_config_path: Path, tmp_path: Path
    ) -> None:
        """Defense in depth: even if a raw overlay.yaml on disk somehow
        carried a base-only key (hand-edited PVC file, a bug upstream of
        this loader), load_effective_config itself filters the overlay
        to the whitelisted top-level keys BEFORE merging -- BASE always
        wins for security/oidc/service_tokens/authorization/
        conversation_store, independent of OverlayDocument's write-time
        rejection (belt-and-suspenders, not the only guard)."""
        overlay_path = tmp_path / "forge.overlay.yaml"
        overlay_path.write_text(yaml.dump({"security": {"oidc": {"enabled": True}}}))
        config = load_effective_config(base_config_path, overlay_path)
        # BASE said oidc.enabled: False and the overlay's "security" key
        # is dropped entirely before merging -- the raw file on disk
        # cannot flip this even bypassing OverlayDocument.
        assert config.security.oidc.enabled is False

    def test_service_tokens_cannot_be_smuggled_via_raw_overlay_file(
        self, base_config_path: Path, tmp_path: Path
    ) -> None:
        overlay_path = tmp_path / "forge.overlay.yaml"
        overlay_path.write_text(
            yaml.dump(
                {
                    "service_tokens": {
                        "tokens": [
                            {"id": "attacker", "secret_sha256": "b" * 64, "roles": ["admin"]}
                        ]
                    },
                    "authorization": {"default_role": "admin"},
                    "conversation_store": {"backend": "redis"},
                }
            )
        )
        config = load_effective_config(base_config_path, overlay_path)
        token_ids = {t.id for t in config.security.service_tokens.tokens}
        assert token_ids == {"t1"}
        assert config.security.authorization.default_role is None
        assert config.conversation_store.backend.value == "memory"

    def test_env_substitution_still_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FORGE_TEST_DESC", "from-env")
        base_path = tmp_path / "forge.yaml"
        base_path.write_text(
            yaml.dump({"metadata": {"name": "x", "description": "${FORGE_TEST_DESC}"}})
        )
        config = load_effective_config(base_path, None)
        assert config.metadata.description == "from-env"

    def test_invalid_merged_config_raises_validation_error(self, tmp_path: Path) -> None:
        base_path = tmp_path / "forge.yaml"
        base_path.write_text(yaml.dump({"metadata": {"name": "x"}}))
        overlay_path = tmp_path / "forge.overlay.yaml"
        # A workflow with zero steps survives field-projection (workflows are
        # runtime-safe, kept verbatim) but fails ForgeConfig validation on
        # merge (WorkflowStep list min_length=1). (An llm.litellm.mode edit
        # can no longer be used here -- it is base-only and pruned at load.)
        overlay_path.write_text(
            yaml.dump({"tools": {"workflows": [{"name": "w", "description": "d", "steps": []}]}})
        )
        with pytest.raises(ConfigValidationError):
            load_effective_config(base_path, overlay_path)

    def test_drift_returns_to_zero_after_promotion(self, tmp_path: Path) -> None:
        """Full lifecycle with a RUNTIME-SAFE edit: the overlay edits a
        base tool's description against BASE v1; 'promoting' by baking that
        description into BASE v2 makes the overlay entry a structural no-op,
        which the prune removes -- drift returns to zero with no
        duplication. (A new tool cannot be introduced via overlay -- it
        would carry a base-only url -- so promotion is exercised on an
        editable field.)"""
        base_path = tmp_path / "forge.yaml"
        base_v1 = {
            "metadata": {"name": "x"},
            "tools": {
                "manual_tools": [
                    {"name": "echo", "description": "old", "api": {"url": "https://x.example.com"}}
                ]
            },
        }
        base_path.write_text(yaml.dump(base_v1))

        from forge_config.loader import compute_base_rev as _cbr

        overlay_path = tmp_path / "forge.overlay.yaml"
        overlay_path.write_text(
            yaml.dump(
                {
                    "tools": {"manual_tools": [{"name": "echo", "description": "new"}]},
                    "_base_rev": _cbr(base_v1),
                }
            )
        )

        config_before = load_effective_config(base_path, overlay_path)
        echo_before = next(t for t in config_before.tools.manual_tools if t.name == "echo")
        assert echo_before.description == "new"

        # Promote: BASE now carries the edited description verbatim.
        base_v2 = {
            "metadata": {"name": "x"},
            "tools": {
                "manual_tools": [
                    {"name": "echo", "description": "new", "api": {"url": "https://x.example.com"}}
                ]
            },
        }
        base_path.write_text(yaml.dump(base_v2))

        config_after = load_effective_config(base_path, overlay_path)
        names_after = [t.name for t in config_after.tools.manual_tools]
        assert names_after == ["echo"]  # no duplication
        echo_after = next(t for t in config_after.tools.manual_tools if t.name == "echo")
        assert echo_after.description == "new"
