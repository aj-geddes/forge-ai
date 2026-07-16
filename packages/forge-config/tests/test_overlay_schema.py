"""Tests for forge_config.overlay -- the Phase-1 field-level split enforced
BY CONSTRUCTION.

Two guarantees:
  1. TOP-LEVEL base-only sections (security/oidc/service_tokens/
     authorization/conversation_store) can never appear in an overlay.
  2. FIELD-level: a base-only FIELD -- a destination (url/base_url/endpoint/
     api_base/peer endpoint), a secret/secret-binding (api_key/auth/SecretRef/
     secret header), the llm.litellm model_list/endpoint/mode registry, or a
     security control (requires_approval) -- is un-representable in a valid
     OverlayDocument, so it is a ValidationError at parse time. Creating a
     tool/openapi/peer (each needs a destination) is therefore impossible,
     while creating an AgentDef/Workflow (no destination/secret) is fine.
"""

from __future__ import annotations

import pytest
from forge_config.overlay import (
    BASE_ONLY_KEYS,
    OverlayDocument,
    OverlayFieldError,
    project_overlay_safe,
    split_overlay_editable,
    validate_overlay_content,
)
from pydantic import ValidationError


class TestOverlayDocumentAcceptsRuntimeSafeFields:
    def test_empty_overlay_is_valid(self) -> None:
        doc = OverlayDocument.model_validate({})
        assert doc.tools is None
        assert doc.rev == 0

    def test_tool_edit_of_safe_fields_validates(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "tools": {
                    "manual_tools": [
                        {
                            "name": "echo",
                            "description": "echoes input",
                            "parameters": [{"name": "q"}],
                            "api": {"response_mapping": {"result_path": "$.data"}},
                        }
                    ]
                }
            }
        )
        assert doc.tools is not None
        assert doc.tools.manual_tools is not None
        assert len(doc.tools.manual_tools) == 1

    def test_tool_destination_edit_validates(self) -> None:
        # SLICE 7: url/base_url/endpoint are now runtime-editable.
        doc = OverlayDocument.model_validate(
            {
                "tools": {
                    "manual_tools": [
                        {"name": "echo", "api": {"url": "https://new.example.com/echo"}},
                        {
                            "name": "e2",
                            "api": {"base_url": "https://h.example.com", "endpoint": "/x"},
                        },
                    ]
                }
            }
        )
        assert doc.tools is not None
        validate_overlay_content(
            {"tools": {"manual_tools": [{"name": "echo", "api": {"url": "https://x.example.com"}}]}}
        )

    def test_openapi_url_edit_validates(self) -> None:
        # SLICE 7: an openapi source's remote-spec url is runtime-editable.
        doc = OverlayDocument.model_validate(
            {
                "tools": {
                    "openapi_sources": [
                        {"name": "petstore", "url": "https://p.example.com/openapi.json"}
                    ]
                }
            }
        )
        assert doc.tools is not None
        validate_overlay_content(
            {"tools": {"openapi_sources": [{"name": "petstore", "url": "https://p.example.com/x"}]}}
        )

    def test_openapi_filter_edit_validates(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "tools": {
                    "openapi_sources": [
                        {"name": "petstore", "include_tags": ["pets"], "prefix": "ps"}
                    ]
                }
            }
        )
        assert doc.tools is not None

    def test_workflow_is_reused_whole(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "tools": {
                    "workflows": [{"name": "wf", "description": "d", "steps": [{"tool": "echo"}]}]
                }
            }
        )
        assert doc.tools is not None

    def test_agent_full_crud_validates(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "agents": {
                    "default": "assistant",
                    "agents": [
                        {
                            "name": "researcher",
                            "system_prompt": "be helpful",
                            "model": "gpt-4o",
                            "tools": ["echo"],
                            "max_turns": 5,
                            "mode": "active",
                        }
                    ],
                }
            }
        )
        assert doc.agents is not None
        assert doc.agents.agents is not None

    def test_peer_safe_field_edit_validates(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "agents": {
                    "peers": [
                        {
                            "name": "peer-a",
                            "capabilities": ["q"],
                            "trust_level": "low",
                            "spiffe_id": "spiffe://forge.local/peer-a",
                        }
                    ]
                }
            }
        )
        assert doc.agents is not None

    def test_llm_safe_fields_validate(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "llm": {
                    "default_model": "claude-x",
                    "temperature": 0.3,
                    "max_tokens": 1000,
                    "system_prompt": "hi",
                    "litellm": {"fallback_models": ["claude-x"], "timeout": 10, "max_retries": 2},
                }
            }
        )
        assert doc.llm is not None
        assert doc.llm.default_model == "claude-x"

    def test_metadata_description_only(self) -> None:
        doc = OverlayDocument.model_validate({"metadata": {"description": "hello"}})
        assert doc.metadata is not None
        assert doc.metadata.description == "hello"

    def test_metadata_rejects_other_fields(self) -> None:
        with pytest.raises(ValidationError):
            OverlayDocument.model_validate({"metadata": {"name": "sneaky"}})

    def test_tombstones_accepted_in_every_name_keyed_list(self) -> None:
        OverlayDocument.model_validate(
            {
                "tools": {
                    "manual_tools": [{"__deleted__": "echo"}],
                    "openapi_sources": [{"__deleted__": "petstore"}],
                    "workflows": [{"__deleted__": "wf"}],
                },
                "agents": {
                    "agents": [{"__deleted__": "researcher"}],
                    "peers": [{"__deleted__": "peer-a"}],
                },
            }
        )

    def test_provenance_stamps_round_trip(self) -> None:
        doc = OverlayDocument.model_validate(
            {
                "_rev": 3,
                "_base_rev": "abc123",
                "_updated_by": "alice@example.com",
                "_updated_at": "2026-01-01T00:00:00Z",
            }
        )
        assert doc.rev == 3
        assert doc.base_rev == "abc123"
        dumped = doc.to_editable_dict()
        assert dumped["_rev"] == 3
        assert dumped["_base_rev"] == "abc123"


class TestOverlayDocumentRejectsBaseOnlyFields:
    """Each base-only FIELD is a hard ValidationError -- structurally
    un-representable, so no runtime code path can persist it."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"tools": {"manual_tools": [{"name": "t", "api": {"method": "POST"}}]}},
            {"tools": {"manual_tools": [{"name": "t", "api": {"headers": {"A": "b"}}}]}},
            {"tools": {"manual_tools": [{"name": "t", "api": {"auth": {"type": "bearer"}}}]}},
            {"tools": {"manual_tools": [{"name": "t", "api": {"body_template": {}}}]}},
            {"tools": {"manual_tools": [{"name": "t", "requires_approval": False}]}},
            {"tools": {"openapi_sources": [{"name": "s", "path": "/spec"}]}},
            {"tools": {"openapi_sources": [{"name": "s", "spec": "http://x"}]}},
            {"tools": {"openapi_sources": [{"name": "s", "auth": {"type": "bearer"}}]}},
            {"tools": {"openapi_sources": [{"name": "s", "requires_approval": True}]}},
            {"llm": {"litellm": {"model_list": [{"model_name": "m"}]}}},
            {"llm": {"litellm": {"endpoint": "http://x"}}},
            {"llm": {"litellm": {"mode": "external"}}},
            {"agents": {"peers": [{"name": "p", "endpoint": "http://x"}]}},
        ],
    )
    def test_base_only_field_is_rejected(self, payload: dict) -> None:
        with pytest.raises(ValidationError):
            OverlayDocument.model_validate(payload)
        with pytest.raises(OverlayFieldError):
            validate_overlay_content(payload)

    def test_validate_overlay_content_message_names_field_and_promotion(self) -> None:
        with pytest.raises(OverlayFieldError) as exc:
            validate_overlay_content(
                {"tools": {"manual_tools": [{"name": "t", "api": {"method": "POST"}}]}}
            )
        msg = str(exc.value)
        assert "method" in msg
        assert "promote" in msg.lower()
        assert "git" in msg.lower()
        assert exc.value.locations


class TestOverlayDocumentRejectsBaseOnlyKeys:
    @pytest.mark.parametrize("key", list(BASE_ONLY_KEYS))
    def test_rejects_each_base_only_key(self, key: str) -> None:
        with pytest.raises(ValidationError, match="base-only"):
            OverlayDocument.model_validate({key: {"anything": "goes"}})

    def test_rejects_security_with_nested_oidc_disable_attempt(self) -> None:
        with pytest.raises(ValidationError, match="base-only"):
            OverlayDocument.model_validate({"security": {"oidc": {"enabled": False}}, "tools": {}})

    def test_rejects_authorization_role_self_grant_attempt(self) -> None:
        with pytest.raises(ValidationError, match="base-only"):
            OverlayDocument.model_validate(
                {"authorization": {"bindings": [{"role": "admin", "subs": ["attacker-sub"]}]}}
            )

    def test_rejects_unknown_top_level_key(self) -> None:
        with pytest.raises(ValidationError):
            OverlayDocument.model_validate({"totally_made_up_section": {}})

    def test_multiple_base_only_keys_all_named_in_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            OverlayDocument.model_validate({"security": {}, "oidc": {}})
        message = str(exc_info.value)
        assert "security" in message
        assert "oidc" in message


class TestOverlayDocumentRejectsAdminPolicyKey:
    def test_admin_mutation_policy_is_not_an_editable_overlay_section(self) -> None:
        with pytest.raises(ValidationError):
            OverlayDocument.model_validate({"admin": {"mutation_policy": "overlay"}})


class TestProjectOverlaySafe:
    """The load-path / PUT projection drops base-only fields, keeping only
    the overlay-safe subset (mirrors the write-time structural rejection)."""

    def test_drops_tool_destination_and_secret_fields(self) -> None:
        safe = project_overlay_safe(
            {
                "tools": {
                    "manual_tools": [
                        {
                            "name": "echo",
                            "description": "d",
                            "api": {
                                "url": "http://x",
                                "headers": {"A": "b"},
                                "auth": {"type": "bearer"},
                                "response_mapping": {"result_path": "$"},
                            },
                            "requires_approval": True,
                        }
                    ]
                }
            }
        )
        tool = safe["tools"]["manual_tools"][0]
        assert tool == {
            "name": "echo",
            "description": "d",
            "api": {"url": "http://x", "response_mapping": {"result_path": "$"}},
        }

    def test_drops_llm_model_list_and_endpoint(self) -> None:
        safe = project_overlay_safe(
            {
                "llm": {
                    "default_model": "m",
                    "litellm": {
                        "mode": "external",
                        "endpoint": "http://x",
                        "model_list": [{"model_name": "m"}],
                        "fallback_models": ["m"],
                    },
                }
            }
        )
        assert safe["llm"] == {"default_model": "m", "litellm": {"fallback_models": ["m"]}}

    def test_drops_peer_endpoint(self) -> None:
        safe = project_overlay_safe(
            {"agents": {"peers": [{"name": "p", "endpoint": "http://x", "trust_level": "low"}]}}
        )
        assert safe["agents"]["peers"][0] == {"name": "p", "trust_level": "low"}

    def test_projection_output_always_validates(self) -> None:
        raw = {
            "tools": {"manual_tools": [{"name": "t", "api": {"url": "http://x"}}]},
            "llm": {"litellm": {"model_list": [{"model_name": "m"}], "endpoint": "http://y"}},
            "agents": {"peers": [{"name": "p", "endpoint": "http://z"}]},
        }
        validate_overlay_content(project_overlay_safe(raw))


class TestSplitOverlayEditable:
    """PUT /config round-trip: unchanged base-only fields are tolerated
    (dropped), a CHANGED base-only field is surfaced for promotion."""

    def test_unchanged_round_trip_reports_no_change(self) -> None:
        eff = {
            "tools": {
                "manual_tools": [{"name": "t", "description": "d", "api": {"url": "http://x"}}]
            },
            "llm": {"default_model": "m", "litellm": {"mode": "embedded", "model_list": []}},
        }
        safe, changed = split_overlay_editable(eff, incoming_for_diff=eff, effective_for_diff=eff)
        assert changed == []
        validate_overlay_content(safe)

    def test_changed_tool_url_is_not_reported(self) -> None:
        # SLICE 7: url is now overlay-safe (runtime-editable, gated at write
        # time by the binding check), so a url change is NOT surfaced here.
        eff = {"tools": {"manual_tools": [{"name": "t", "api": {"url": "http://good"}}]}}
        inc = {"tools": {"manual_tools": [{"name": "t", "api": {"url": "http://evil"}}]}}
        _, changed = split_overlay_editable(inc, incoming_for_diff=inc, effective_for_diff=eff)
        assert not any("url" in c for c in changed)

    def test_changed_tool_auth_is_reported(self) -> None:
        eff = {"tools": {"manual_tools": [{"name": "t", "api": {"auth": {"type": "none"}}}]}}
        inc = {"tools": {"manual_tools": [{"name": "t", "api": {"auth": {"type": "bearer"}}}]}}
        _, changed = split_overlay_editable(inc, incoming_for_diff=inc, effective_for_diff=eff)
        assert any("auth" in c for c in changed)

    def test_new_peer_endpoint_is_reported(self) -> None:
        eff: dict = {"agents": {"peers": []}}
        inc = {"agents": {"peers": [{"name": "new", "endpoint": "http://x"}]}}
        _, changed = split_overlay_editable(inc, incoming_for_diff=inc, effective_for_diff=eff)
        assert any("endpoint" in c for c in changed)

    def test_changed_model_list_is_reported(self) -> None:
        eff = {"llm": {"litellm": {"model_list": [{"model_name": "a"}]}}}
        inc = {"llm": {"litellm": {"model_list": [{"model_name": "a"}, {"model_name": "b"}]}}}
        _, changed = split_overlay_editable(inc, incoming_for_diff=inc, effective_for_diff=eff)
        assert any("model_list" in c for c in changed)

    def test_safe_field_change_is_not_reported(self) -> None:
        eff = {"llm": {"default_model": "a", "temperature": 0.7}}
        inc = {"llm": {"default_model": "b", "temperature": 0.1}}
        safe, changed = split_overlay_editable(inc, incoming_for_diff=inc, effective_for_diff=eff)
        assert changed == []
        assert safe["llm"]["default_model"] == "b"
