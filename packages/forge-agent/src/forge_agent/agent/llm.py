"""LLM routing for Forge Agent.

Creates and manages the LiteLLM Router from LLMConfig, handling
embedded, sidecar, and external deployment modes.
"""

from __future__ import annotations

from typing import Any

from forge_config.schema import LiteLLMConfig, LiteLLMMode, LLMConfig


class LLMRouter:
    """Manages LLM model routing via LiteLLM.

    Configures the appropriate LiteLLM Router based on the deployment
    mode specified in LLMConfig:

    - embedded: Configures Router directly with model_list.
    - sidecar: Points to a local sidecar LiteLLM proxy endpoint.
    - external: Points to an external LiteLLM proxy endpoint.

    For PydanticAI integration, we provide the model identifier string
    that PydanticAI can use with its built-in LiteLLM support.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._router: Any = None
        self._setup()

    def _setup(self) -> None:
        """Initialize the router based on configuration mode."""
        litellm_config = self._config.litellm

        if litellm_config.mode == LiteLLMMode.EMBEDDED:
            self._setup_embedded(litellm_config)
        elif litellm_config.mode in (LiteLLMMode.SIDECAR, LiteLLMMode.EXTERNAL):
            self._setup_proxy(litellm_config)

    def _setup_embedded(self, litellm_config: LiteLLMConfig) -> None:
        """Set up embedded mode with direct LiteLLM Router.

        Args:
            litellm_config: The LiteLLM configuration.
        """
        if litellm_config.model_list:
            try:
                from litellm import Router  # type: ignore[attr-defined]

                fallbacks: list[Any] = (
                    [{"model": m} for m in litellm_config.fallback_models]
                    if litellm_config.fallback_models
                    else []
                )
                self._router = Router(
                    model_list=litellm_config.model_list,
                    fallbacks=fallbacks,
                    timeout=litellm_config.timeout,
                    num_retries=litellm_config.max_retries,
                )
            except ImportError:
                # LiteLLM Router not available; fall back to model string.
                self._router = None

    def _setup_proxy(self, litellm_config: LiteLLMConfig) -> None:
        """Set up sidecar/external mode pointing to a proxy endpoint.

        Args:
            litellm_config: The LiteLLM configuration with endpoint.
        """
        # For proxy modes, the endpoint is used as the base URL.
        # PydanticAI will use the model string which can include
        # the proxy endpoint prefix.
        self._router = None

    @property
    def model_name(self) -> str:
        """The model identifier for PydanticAI.

        For embedded mode, returns the default model name.
        For proxy modes, returns a litellm-formatted model string
        that includes the proxy endpoint.
        """
        config = self._config
        litellm_config = config.litellm

        if litellm_config.mode in (LiteLLMMode.SIDECAR, LiteLLMMode.EXTERNAL):
            # Format for LiteLLM proxy: openai/<model> with api_base
            return f"openai/{config.default_model}"

        return config.default_model

    @property
    def model_settings(self) -> dict[str, Any]:
        """Model settings dict for PydanticAI.

        Returns:
            Dict of settings including temperature, max_tokens, etc.
        """
        settings: dict[str, Any] = {
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }

        litellm_config = self._config.litellm
        if (
            litellm_config.mode in (LiteLLMMode.SIDECAR, LiteLLMMode.EXTERNAL)
            and litellm_config.endpoint
        ):
            settings["api_base"] = litellm_config.endpoint

        return settings

    @property
    def router(self) -> Any:
        """The underlying LiteLLM Router, if configured."""
        return self._router

    @property
    def system_prompt(self) -> str | None:
        """The configured system prompt."""
        return self._config.system_prompt
