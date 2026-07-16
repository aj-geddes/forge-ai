"""LLM routing for Forge Agent.

Resolves ``LLMConfig`` into concrete PydanticAI ``Model`` objects, honoring
LiteLLM-style ``model_list`` aliases plus per-model ``api_base``/``api_key``
and ``fallback_models`` -- across the three supported deployment modes
(embedded / sidecar / external).

Historically Forge built a LiteLLM ``Router`` from ``model_list`` and then
never used it: the raw ``default_model`` string was handed straight to
PydanticAI, which only understands its own ``provider:model`` id format (or
a short list of legacy prefixes such as ``gpt-``/``claude-``). Any
``model_list`` alias that isn't already a name PydanticAI recognizes (e.g.
``nemotron``) raised ``pydantic_ai.exceptions.UserError: Unknown model``,
and ``api_base``/``api_key`` were silently dropped because there is no
``pydantic_ai.models.litellm`` in the installed PydanticAI version -- there
was never a code path that could have honored them.

This module treats ``model_list`` as the source of truth instead: each
entry's ``litellm_params`` (``model``, ``api_base``, ``api_key``) is
translated directly into the matching PydanticAI ``Model``/``Provider``
pair, so requests reach the configured endpoint using the configured
upstream model id and credentials.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from forge_config.exceptions import SecretResolutionError
from forge_config.schema import LiteLLMConfig, LiteLLMMode, LLMConfig, SecretRef, SecretSource
from forge_config.secret_resolver import CompositeSecretResolver, SecretResolver
from forge_security.egress import host_matches, validate_endpoint
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

# A ``model_list`` entry's ``litellm_params.api_key`` may be a literal
# ``${VAR}`` / ``${VAR:default}`` reference (the same encoding forge-config's
# loader substitutes and the admin choke point scrubs). When the whole string
# is exactly one such reference it is resolved through the SecretResolver at
# build time -- so it participates in secret handling instead of sitting as
# cleartext in overlay/promotion surfaces -- rather than handed to the
# provider verbatim. A plain string is passed through unchanged (back-compat).
_ENV_REF_FULL = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")

# LiteLLM-style provider prefixes (the part before "/" in
# ``litellm_params.model``, e.g. "openai/nemotron-puzzle") that speak the
# OpenAI-compatible chat-completions API and so route through
# ``OpenAIChatModel`` with a custom ``base_url``. This covers vLLM and most
# other self-hosted/OpenAI-compatible backends, which is why the deployed
# config expresses the vLLM endpoint as ``openai/nemotron-puzzle``.
_OPENAI_COMPATIBLE_PREFIXES = frozenset(
    {"openai", "azure", "vllm", "together_ai", "fireworks_ai", "deepinfra", "anyscale"}
)

# A deliberate placeholder, never a real credential. sidecar/external mode
# points PydanticAI's OpenAI-compatible client at an arbitrary configured
# endpoint; falling back to an ambient OPENAI_API_KEY here would risk
# leaking an unrelated credential to that endpoint. Forge's schema has no
# dedicated proxy api_key field, so deployments that require proxy auth
# must front the endpoint with network-level auth.
_PROXY_PLACEHOLDER_API_KEY = "forge-litellm-proxy-unused"


class LLMConfigError(ValueError):
    """Raised when an ``LLMConfig`` cannot be resolved into a usable model.

    Raised eagerly (at agent-creation time, not deferred to the first LLM
    request) so a misconfigured ``model_list``/``fallback_models`` fails
    loudly at startup instead of surfacing as a confusing runtime error.
    """


class LLMRouter:
    """Resolves ``LLMConfig`` into PydanticAI ``Model`` objects.

    Mode contracts:

    - **embedded**: ``default_model`` (and each ``fallback_models`` entry)
      is resolved by name against ``model_list``, and each resolved
      ``litellm_params`` entry is translated into a native PydanticAI
      ``Model`` hitting that entry's ``api_base``/``api_key`` directly. When
      ``fallback_models`` is non-empty the resolved models are combined
      into a PydanticAI ``FallbackModel``. When ``model_list`` is empty,
      ``default_model`` is passed through unchanged so plain
      provider-qualified names (e.g. ``openai:gpt-4o``) keep working exactly
      as before.
    - **sidecar / external**: these modes point at a running LiteLLM proxy,
      which exposes an OpenAI-compatible HTTP surface and resolves its own
      aliases server-side from its own config. Requests are routed there via
      ``OpenAIChatModel`` with ``base_url=endpoint``, so ``endpoint``
      (api_base) is genuinely honored rather than silently dropped.
      ``fallback_models`` are routed to the same proxy under their own
      names via a PydanticAI ``FallbackModel``.

    Raises ``LLMConfigError`` -- not silently -- when ``model_list`` is
    configured but a referenced alias (``default_model`` or a
    ``fallback_models`` entry) is missing from it, or when a ``model_list``
    entry uses a provider prefix this router cannot translate.
    """

    def __init__(self, config: LLMConfig, *, secret_resolver: SecretResolver | None = None) -> None:
        self._config = config
        # SLICE 6: an env-backed resolver so a ``${ENV_VAR}`` / SecretRef
        # ``api_key`` is resolved at build time (same mechanism as tool auth).
        # A plain-string api_key never touches the resolver.
        self._secret_resolver: SecretResolver = secret_resolver or CompositeSecretResolver()

    @property
    def model_settings(self) -> dict[str, Any]:
        """Model settings dict for PydanticAI (temperature, max_tokens).

        ``api_base`` is deliberately not included here: PydanticAI's
        ``ModelSettings``/``OpenAIChatModelSettings`` have no ``api_base``
        field, so putting it there is silently ignored. The endpoint is
        instead applied where it actually takes effect -- as ``base_url`` on
        the ``Provider`` used to build the ``Model`` (see ``resolve_model``).
        """
        return {
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }

    @property
    def system_prompt(self) -> str | None:
        """The configured system prompt."""
        return self._config.system_prompt

    def resolve_model(self, model_name_override: str | None = None) -> Model | str:
        """Resolve the model to use for a request.

        Args:
            model_name_override: If set, resolve this alias/model id
                instead of ``config.default_model``.

        Returns:
            A concrete PydanticAI ``Model`` when the alias could be
            translated (embedded-with-model_list, or sidecar/external), or
            the raw model string as a passthrough for embedded mode with no
            ``model_list`` configured.

        Raises:
            LLMConfigError: If ``model_list`` is configured but the
                requested alias (or a fallback alias) is not defined in it,
                or a ``model_list`` entry references an unsupported
                provider.
        """
        litellm_config = self._config.litellm
        alias = model_name_override or self._config.default_model

        if litellm_config.mode == LiteLLMMode.EMBEDDED:
            return self._resolve_embedded(litellm_config, alias)
        return self._resolve_proxy(litellm_config, alias)

    def _resolve_embedded(self, litellm_config: LiteLLMConfig, alias: str) -> Model | str:
        """Resolve ``alias`` for embedded mode.

        With no ``model_list`` configured, the alias is passed through
        unchanged (backward-compatible with plain PydanticAI model ids).
        With a ``model_list`` configured, ``alias`` (and every fallback)
        must be defined in it.
        """
        if not litellm_config.model_list:
            return alias

        primary = self._resolve_alias(litellm_config.model_list, alias)
        if not litellm_config.fallback_models:
            return primary

        fallbacks = [
            self._resolve_alias(litellm_config.model_list, name)
            for name in litellm_config.fallback_models
        ]
        return FallbackModel(primary, *fallbacks)

    def _resolve_proxy(self, litellm_config: LiteLLMConfig, alias: str) -> Model:
        """Resolve ``alias`` for sidecar/external mode against the proxy endpoint.

        The proxy is expected to be a LiteLLM proxy speaking the
        OpenAI-compatible API and resolving ``alias`` against its own
        server-side config, so no local ``model_list`` lookup is needed --
        only the endpoint (``api_base``) matters here.
        """
        provider = OpenAIProvider(
            base_url=litellm_config.endpoint, api_key=_PROXY_PLACEHOLDER_API_KEY
        )
        primary: Model = OpenAIChatModel(alias, provider=provider)
        if not litellm_config.fallback_models:
            return primary

        fallbacks: list[Model] = [
            OpenAIChatModel(name, provider=provider) for name in litellm_config.fallback_models
        ]
        return FallbackModel(primary, *fallbacks)

    def _resolve_alias(self, model_list: list[dict[str, Any]], alias: str) -> Model:
        """Look up ``alias`` in ``model_list`` and build its PydanticAI ``Model``."""
        entry = next((m for m in model_list if m.get("model_name") == alias), None)
        if entry is None:
            known = [m.get("model_name") for m in model_list]
            msg = (
                f"LLM alias {alias!r} is not defined in llm.litellm.model_list. "
                f"Known aliases: {known}"
            )
            raise LLMConfigError(msg)

        params = entry.get("litellm_params") or {}
        litellm_model = params.get("model")
        if not litellm_model:
            msg = f"llm.litellm.model_list entry {alias!r} is missing litellm_params.model"
            raise LLMConfigError(msg)

        return self._build_model(
            alias, litellm_model, params.get("api_base"), params.get("api_key")
        )

    def _build_model(
        self,
        alias: str,
        litellm_model: str,
        api_base: str | None,
        api_key: Any,
    ) -> Model:
        """Translate one LiteLLM-style ``<provider>/<model>`` id into a PydanticAI ``Model``.

        SLICE 6 (ADR-0006): the entry's ``api_base`` host is validated
        (fail-closed against internal/metadata addresses, subject to
        ``allowed_api_hosts``) and its ``api_key`` is resolved through the
        SecretResolver -- BEFORE any provider/socket is constructed, because
        litellm is embedded and owns its own HTTP socket, so the guard has to
        sit at build/config time rather than at connect time.
        """
        self._validate_api_base(alias, api_base)
        resolved_key = self._resolve_api_key(api_key)

        provider_prefix, sep, upstream_model = litellm_model.partition("/")
        if not sep:
            # No "<provider>/" prefix -- treat the whole string as an
            # OpenAI-compatible upstream model id.
            provider_prefix, upstream_model = "openai", litellm_model

        if provider_prefix == "anthropic":
            return AnthropicModel(
                upstream_model,
                provider=AnthropicProvider(api_key=resolved_key, base_url=api_base),
            )

        if provider_prefix in _OPENAI_COMPATIBLE_PREFIXES:
            return OpenAIChatModel(
                upstream_model,
                provider=OpenAIProvider(base_url=api_base, api_key=resolved_key),
            )

        msg = (
            f"llm.litellm.model_list alias {alias!r} uses unsupported provider "
            f"{provider_prefix!r} (model={litellm_model!r}). Supported providers: "
            f"anthropic, {sorted(_OPENAI_COMPATIBLE_PREFIXES)}."
        )
        raise LLMConfigError(msg)

    def _validate_api_base(self, alias: str, api_base: str | None) -> None:
        """Reject a ``model_list`` entry whose ``api_base`` targets an internal
        or metadata host (SLICE 6, fail-closed).

        ``allowed_api_hosts`` (when set) is a strict positive allow-list AND the
        escape hatch for a trusted internal endpoint: a host named there is
        permitted even if it resolves to a private address; a host not named
        there is rejected even if public. When ``allowed_api_hosts`` is empty
        the IP-layer classifier (``validate_endpoint``) rejects any
        internal/metadata/blocked ``api_base``. ``api_base is None`` (the
        provider's own default host) needs no validation.
        """
        if not api_base:
            return
        host = urlsplit(api_base).hostname
        if not host:
            msg = (
                f"llm.litellm.model_list alias {alias!r} has an api_base with no "
                f"host ({api_base!r})."
            )
            raise LLMConfigError(msg)

        allowed = self._config.litellm.allowed_api_hosts
        if allowed:
            if host_matches(host, frozenset(allowed)):
                return
            msg = (
                f"llm.litellm.model_list alias {alias!r} api_base host {host!r} is "
                f"not in llm.litellm.allowed_api_hosts ({allowed}). Add it to "
                "positively allow this provider host."
            )
            raise LLMConfigError(msg)

        if not validate_endpoint(api_base):
            msg = (
                f"llm.litellm.model_list alias {alias!r} api_base {api_base!r} "
                "targets an internal/metadata/blocked host. Point it at a public "
                "provider, or add its host to llm.litellm.allowed_api_hosts to "
                "trust an internal endpoint."
            )
            raise LLMConfigError(msg)

    def _resolve_api_key(self, api_key: Any) -> str | None:
        """Resolve a ``model_list`` entry's ``api_key`` at build time (SLICE 6).

        Accepts a ``SecretRef``-shaped mapping, a whole-string ``${ENV_VAR}`` /
        ``${ENV_VAR:default}`` reference (resolved through the SecretResolver),
        or a plain string (passed through unchanged for backward compatibility).
        """
        if api_key is None:
            return None
        if isinstance(api_key, dict):
            return self._secret_resolver.resolve(SecretRef.model_validate(api_key))
        if isinstance(api_key, str):
            match = _ENV_REF_FULL.fullmatch(api_key.strip())
            if match:
                return self._resolve_env_ref(match.group(1), match.group(2))
            return api_key
        return str(api_key)

    def _resolve_env_ref(self, name: str, default: str | None) -> str:
        """Resolve a ``${ENV_VAR}`` reference via the SecretResolver, honoring a
        ``${ENV_VAR:default}`` fallback when the variable is unset."""
        try:
            return self._secret_resolver.resolve(SecretRef(source=SecretSource.ENV, name=name))
        except SecretResolutionError:
            if default is not None:
                return default
            raise
