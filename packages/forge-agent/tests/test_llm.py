"""Tests for LLM routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from forge_agent.agent.llm import LLMRouter
from forge_config.schema import LiteLLMConfig, LiteLLMMode, LLMConfig


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


class TestEmbeddedMode:
    """Tests for embedded (in-process LiteLLM) mode."""

    def test_embedded_no_model_list_sets_router_none(self) -> None:
        config = _make_llm_config(mode=LiteLLMMode.EMBEDDED, model_list=[])
        router = LLMRouter(config)

        assert router.router is None

    def test_embedded_with_model_list_creates_router(self) -> None:
        mock_router_instance = MagicMock()
        model_list = [
            {"model_name": "gpt-4o", "litellm_params": {"model": "gpt-4o"}},
        ]
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            model_list=model_list,
        )

        with (
            patch.dict("sys.modules", {"litellm": MagicMock()}),
            patch(
                "litellm.Router",
                return_value=mock_router_instance,
            ) as mock_cls,
        ):
            router = LLMRouter(config)

            mock_cls.assert_called_once_with(
                model_list=model_list,
                fallbacks=[],
                timeout=30.0,
                num_retries=3,
            )
            assert router.router is mock_router_instance

    def test_embedded_with_fallback_models(self) -> None:
        mock_router_instance = MagicMock()
        model_list = [
            {"model_name": "gpt-4o", "litellm_params": {"model": "gpt-4o"}},
        ]
        fallbacks = ["gpt-3.5-turbo", "claude-3-haiku"]
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            model_list=model_list,
            fallback_models=fallbacks,
        )

        with (
            patch.dict("sys.modules", {"litellm": MagicMock()}),
            patch(
                "litellm.Router",
                return_value=mock_router_instance,
            ) as mock_cls,
        ):
            router = LLMRouter(config)

            expected_fallbacks = [
                {"model": "gpt-3.5-turbo"},
                {"model": "claude-3-haiku"},
            ]
            mock_cls.assert_called_once_with(
                model_list=model_list,
                fallbacks=expected_fallbacks,
                timeout=30.0,
                num_retries=3,
            )
            assert router.router is mock_router_instance

    def test_embedded_import_error_falls_back(self) -> None:
        model_list = [
            {"model_name": "gpt-4o", "litellm_params": {"model": "gpt-4o"}},
        ]
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            model_list=model_list,
        )

        with patch.dict("sys.modules", {"litellm": None}):
            router = LLMRouter(config)

            assert router.router is None

    def test_embedded_model_name_returns_default(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            default_model="claude-3-opus",
        )
        router = LLMRouter(config)

        assert router.model_name == "claude-3-opus"

    def test_embedded_model_settings_no_api_base(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            temperature=0.5,
            max_tokens=2048,
        )
        router = LLMRouter(config)
        settings = router.model_settings

        assert settings["temperature"] == 0.5
        assert settings["max_tokens"] == 2048
        assert "api_base" not in settings

    def test_embedded_custom_timeout_and_retries(self) -> None:
        mock_router_instance = MagicMock()
        model_list = [
            {"model_name": "gpt-4o", "litellm_params": {"model": "gpt-4o"}},
        ]
        config = _make_llm_config(
            mode=LiteLLMMode.EMBEDDED,
            model_list=model_list,
            timeout=60.0,
            max_retries=5,
        )

        with (
            patch.dict("sys.modules", {"litellm": MagicMock()}),
            patch(
                "litellm.Router",
                return_value=mock_router_instance,
            ) as mock_cls,
        ):
            LLMRouter(config)

            mock_cls.assert_called_once_with(
                model_list=model_list,
                fallbacks=[],
                timeout=60.0,
                num_retries=5,
            )


class TestSidecarMode:
    """Tests for sidecar (local LiteLLM proxy) mode."""

    def test_sidecar_sets_router_none(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
        )
        router = LLMRouter(config)

        assert router.router is None

    def test_sidecar_model_name_prefixed(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
            default_model="gpt-4o",
        )
        router = LLMRouter(config)

        assert router.model_name == "openai/gpt-4o"

    def test_sidecar_model_settings_include_api_base(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
            temperature=0.3,
            max_tokens=1024,
        )
        router = LLMRouter(config)
        settings = router.model_settings

        assert settings["temperature"] == 0.3
        assert settings["max_tokens"] == 1024
        assert settings["api_base"] == "http://localhost:4000"


class TestExternalMode:
    """Tests for external (remote LiteLLM proxy) mode."""

    def test_external_sets_router_none(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EXTERNAL,
            endpoint="https://litellm.example.com",
        )
        router = LLMRouter(config)

        assert router.router is None

    def test_external_model_name_prefixed(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EXTERNAL,
            endpoint="https://litellm.example.com",
            default_model="claude-3-opus",
        )
        router = LLMRouter(config)

        assert router.model_name == "openai/claude-3-opus"

    def test_external_model_settings_include_api_base(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EXTERNAL,
            endpoint="https://litellm.example.com",
            temperature=0.9,
            max_tokens=8192,
        )
        router = LLMRouter(config)
        settings = router.model_settings

        assert settings["temperature"] == 0.9
        assert settings["max_tokens"] == 8192
        assert settings["api_base"] == "https://litellm.example.com"


class TestDefaultBehavior:
    """Tests for fallback and default behavior."""

    def test_default_config_uses_embedded_mode(self) -> None:
        config = LLMConfig(default_model="gpt-4o")
        router = LLMRouter(config)

        assert router.model_name == "gpt-4o"
        assert router.router is None

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


class TestModelSettingsPropagation:
    """Tests for model settings propagation across modes."""

    def test_settings_propagate_temperature(self) -> None:
        config = _make_llm_config(temperature=0.0)
        router = LLMRouter(config)

        assert router.model_settings["temperature"] == 0.0

    def test_settings_propagate_max_tokens(self) -> None:
        config = _make_llm_config(max_tokens=100)
        router = LLMRouter(config)

        assert router.model_settings["max_tokens"] == 100

    def test_settings_no_api_base_for_embedded(self) -> None:
        config = _make_llm_config(mode=LiteLLMMode.EMBEDDED)
        router = LLMRouter(config)

        assert "api_base" not in router.model_settings

    def test_settings_api_base_for_sidecar(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.SIDECAR,
            endpoint="http://localhost:4000",
        )
        router = LLMRouter(config)

        assert router.model_settings["api_base"] == "http://localhost:4000"

    def test_settings_api_base_for_external(self) -> None:
        config = _make_llm_config(
            mode=LiteLLMMode.EXTERNAL,
            endpoint="https://proxy.example.com",
        )
        router = LLMRouter(config)

        assert router.model_settings["api_base"] == "https://proxy.example.com"


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
