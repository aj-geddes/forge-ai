"""Tests for LLM routing.

Covers the resolution of ``LLMConfig`` into PydanticAI ``Model`` objects
across embedded / sidecar / external modes -- the contract that fixes the
production bug where ``default_model: nemotron`` (a ``model_list`` alias)
was handed to PydanticAI as a raw string and raised
``UserError: Unknown model: nemotron``.

See ``test_llm_routing_integration.py`` for end-to-end tests that exercise
the real outgoing HTTP request against a fake transport.
"""

from __future__ import annotations

from typing import Any

import pytest
from forge_agent.agent.llm import LLMConfigError, LLMRouter
from forge_config.schema import LiteLLMConfig, LiteLLMMode, LLMConfig
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel


def _make_llm_config(
    mode: LiteLLMMode = LiteLLMMode.EMBEDDED,
    default_model: str = "gpt-4o",
    endpoint: str | None = None,
    model_list: list[dict[str, Any]] | None = None,
    fallback_models: list[str] | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> LLMConfig:
    """Build an LLMConfig for testing."""
    litellm = LiteLLMConfig(
        mode=mode,
        endpoint=endpoint,
        model_list=model_list or [],
        fallback_models=fallback_models or [],
        timeout=timeout,
        max_retries=max_retries,
    )
    return LLMConfig(
        default_model=default_model,
        litellm=litellm,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


NEMOTRON_ENTRY = {
    "model_name": "nemotron",
    "litellm_params": {
        "model": "openai/nemotron-puzzle",
        "api_base": "http://192.168.86.42:8000/v1",
        "api_key": "not-used-by-vllm",
    },
}
CLAUDE_ENTRY = {
    "model_name": "claude-sonnet",
    "litellm_params": {
        "model": "anthropic/claude-sonnet-4-20250514",
        "api_key": "sk-ant-test",
    },
}


class TestEmbeddedModeWithoutModelList:
    """Backward-compatible passthrough when model_list is empty."""

    def test_no_model_list_returns_raw_string(self) -> None:
        config = _make_llm_config(mode=LiteLLMMode.EMBEDDED, default_model="openai:gpt-4o")
        router = LLMRouter(config)

        assert router.resolve_model() == "openai:gpt-4o"

    def test_legacy_prefixed_name_returns_raw_string(self) -> None:
        config = _make_llm_config(mode=LiteLLMMode.EMBEDDED, default_model="gpt-4o")
        router = LLMRouter(config)

        assert router.resolve_model() == "gpt-4o"


class TestEmbeddedModeWithModelList:
    """The deployed config shape: default_model resolves via model_list."""

    def test_alias_resolves_to_openai_compatible_model(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="nemotron",
            model_list=[NEMOTRON_ENTRY],
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, OpenAIChatModel)
        assert model.model_name == "nemotron-puzzle"
        assert str(model.base_url).rstrip("/") == "http://192.168.86.42:8000/v1"

    def test_anthropic_prefixed_alias_resolves_to_anthropic_model(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="claude-sonnet",
            model_list=[NEMOTRON_ENTRY, CLAUDE_ENTRY],
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, AnthropicModel)
        assert model.model_name == "claude-sonnet-4-20250514"

    def test_model_name_override_resolves_via_model_list(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="nemotron",
            model_list=[NEMOTRON_ENTRY, CLAUDE_ENTRY],
        )
        router = LLMRouter(config)

        model = router.resolve_model("claude-sonnet")

        assert isinstance(model, AnthropicModel)

    def test_alias_not_in_model_list_raises_config_error(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="totally-unknown",
            model_list=[NEMOTRON_ENTRY],
        )
        router = LLMRouter(config)

        with pytest.raises(LLMConfigError, match="totally-unknown"):
            router.resolve_model()

    def test_unsupported_provider_prefix_raises_config_error(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="mystery",
            model_list=[
                {
                    "model_name": "mystery",
                    "litellm_params": {"model": "cohere/command-r"},
                }
            ],
        )
        router = LLMRouter(config)

        with pytest.raises(LLMConfigError, match="cohere"):
            router.resolve_model()

    def test_model_without_provider_prefix_treated_as_openai_compatible(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="bare",
            model_list=[
                {
                    "model_name": "bare",
                    "litellm_params": {"model": "gpt-4o", "api_key": "sk-test"},
                }
            ],
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, OpenAIChatModel)
        assert model.model_name == "gpt-4o"


class TestEmbeddedModeFallbacks:
    """fallback_models must build a real PydanticAI FallbackModel."""

    def test_fallback_models_produce_fallback_model(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="nemotron",
            model_list=[NEMOTRON_ENTRY, CLAUDE_ENTRY],
            fallback_models=["claude-sonnet"],
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, FallbackModel)
        assert isinstance(model.models[0], OpenAIChatModel)
        assert isinstance(model.models[1], AnthropicModel)

    def test_unknown_fallback_alias_raises_config_error(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="nemotron",
            model_list=[NEMOTRON_ENTRY],
            fallback_models=["does-not-exist"],
        )
        router = LLMRouter(config)

        with pytest.raises(LLMConfigError, match="does-not-exist"):
            router.resolve_model()

    def test_no_fallback_models_returns_primary_only(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="nemotron",
            model_list=[NEMOTRON_ENTRY],
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, OpenAIChatModel)


class TestSidecarMode:
    """Sidecar mode routes to a local LiteLLM proxy via its OpenAI-compatible API."""

    def test_resolves_to_openai_model_at_endpoint(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
            default_model="gpt-4o",
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, OpenAIChatModel)
        assert model.model_name == "gpt-4o"
        assert str(model.base_url).rstrip("/") == "http://localhost:4000"

    def test_fallback_models_route_to_same_endpoint(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
            default_model="gpt-4o",
            fallback_models=["gpt-3.5-turbo"],
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, FallbackModel)
        assert all(isinstance(m, OpenAIChatModel) for m in model.models)
        assert model.models[1].model_name == "gpt-3.5-turbo"


class TestExternalMode:
    """External mode routes to a remote LiteLLM proxy via its OpenAI-compatible API."""

    def test_resolves_to_openai_model_at_endpoint(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EXTERNAL,
            endpoint="https://litellm.example.com",
            default_model="claude-3-opus",
        )
        router = LLMRouter(config)

        model = router.resolve_model()

        assert isinstance(model, OpenAIChatModel)
        assert model.model_name == "claude-3-opus"
        assert str(model.base_url).rstrip("/") == "https://litellm.example.com"


class TestDefaultBehavior:
    """Tests for fallback and default behavior."""

    def test_default_config_uses_embedded_mode_passthrough(self) -> None:
        config = LLMConfig(default_model="gpt-4o")
        router = LLMRouter(config)

        assert router.resolve_model() == "gpt-4o"

    def test_default_temperature_and_max_tokens(self) -> None:
        config = LLMConfig(default_model="gpt-4o")
        router = LLMRouter(config)
        settings = router.model_settings

        assert settings["temperature"] == 0.7
        assert settings["max_tokens"] == 4096


class TestSystemPrompt:
    """Tests for system prompt access."""

    def test_system_prompt_when_set(self) -> None:
        config = _make_llm_config(system_prompt="You are a helpful assistant.")
        router = LLMRouter(config)

        assert router.system_prompt == "You are a helpful assistant."

    def test_system_prompt_when_none(self) -> None:
        config = _make_llm_config(system_prompt=None)
        router = LLMRouter(config)

        assert router.system_prompt is None


class TestModelSettings:
    """Tests for model settings (temperature/max_tokens only -- see llm.py docstring)."""

    def test_settings_propagate_temperature(self) -> None:
        config = _make_llm_config(temperature=0.0)
        router = LLMRouter(config)

        assert router.model_settings["temperature"] == 0.0

    def test_settings_propagate_max_tokens(self) -> None:
        config = _make_llm_config(max_tokens=100)
        router = LLMRouter(config)

        assert router.model_settings["max_tokens"] == 100

    def test_settings_never_include_api_base(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
        )
        router = LLMRouter(config)

        assert "api_base" not in router.model_settings


class TestErrorCases:
    """Tests for error handling and invalid configurations."""

    def test_sidecar_without_endpoint_raises(self) -> None:
        with pytest.raises(ValueError, match="endpoint is required"):
            _make_llm_config(mode=LiteLLMMode.SIDECAR, endpoint=None)

    def test_external_without_endpoint_raises(self) -> None:
        with pytest.raises(ValueError, match="endpoint is required"):
            _make_llm_config(mode=LiteLLMMode.EXTERNAL, endpoint=None)

    def test_invalid_mode_string_raises(self) -> None:
        with pytest.raises(ValueError):
            LiteLLMConfig(mode="invalid_mode")  # type: ignore[arg-type]

    def test_model_list_entry_missing_litellm_params_model_raises(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="broken",
            model_list=[{"model_name": "broken", "litellm_params": {}}],
        )
        router = LLMRouter(config)

        with pytest.raises(LLMConfigError, match="litellm_params.model"):
            router.resolve_model()
