"""Integration tests proving LLM requests actually reach the configured
``api_base`` with the configured upstream model id.

These are the regression tests for the production bug: Forge built a
LiteLLM ``Router`` from ``llm.litellm.model_list`` and then never used it --
the raw ``llm.default_model`` string ("nemotron") was handed straight to
PydanticAI, which tried to resolve it as a native ``provider:model`` id and
raised ``pydantic_ai.exceptions.UserError: Unknown model: nemotron``.

The config shape below mirrors ``deploy/helm/forge/values-hvs-k8s.yaml``
exactly. No real network calls are made -- the outgoing HTTP transport is
replaced with a fake that records requests and returns a canned
OpenAI-compatible chat completion response.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
from forge_agent.agent.core import ForgeAgent
from forge_config.schema import (
    AgentsConfig,
    ForgeConfig,
    LiteLLMConfig,
    LiteLLMMode,
    LLMConfig,
    ToolsConfig,
)

# Mirrors deploy/helm/forge/values-hvs-k8s.yaml `llm:` section.
NEMOTRON_API_BASE = "http://192.168.86.42:8000/v1"
NEMOTRON_UPSTREAM_MODEL = "nemotron-puzzle"
CLAUDE_UPSTREAM_MODEL = "claude-sonnet-4-20250514"


def _deployed_llm_config(*, default_model: str = "nemotron") -> LLMConfig:
    """LLMConfig matching the deployed values-hvs-k8s.yaml shape."""
    return LLMConfig(
        default_model=default_model,
        litellm=LiteLLMConfig(
            mode=LiteLLMMode.EMBEDDED,
            model_list=[
                {
                    "model_name": "nemotron",
                    "litellm_params": {
                        "model": f"openai/{NEMOTRON_UPSTREAM_MODEL}",
                        "api_base": NEMOTRON_API_BASE,
                        "api_key": "not-used-by-vllm",
                    },
                },
                {
                    "model_name": "claude-sonnet",
                    "litellm_params": {
                        "model": f"anthropic/{CLAUDE_UPSTREAM_MODEL}",
                        "api_key": "test-anthropic-key",
                    },
                },
            ],
            fallback_models=["claude-sonnet"],
        ),
    )


def _make_config(*, default_model: str = "nemotron") -> ForgeConfig:
    return ForgeConfig(
        llm=_deployed_llm_config(default_model=default_model),
        tools=ToolsConfig(),
        agents=AgentsConfig(),
    )


def _openai_chat_completion_response(content: str, model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


class RecordingTransport:
    """Fake ``httpx.AsyncClient.send`` that records requests instead of
    touching the network, and returns a canned OpenAI-style response."""

    def __init__(self, content: str = "hello from vLLM") -> None:
        self.requests: list[httpx.Request] = []
        self._content = content

    async def __call__(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        self.requests.append(request)
        sent_body = json.loads(request.content)
        return httpx.Response(
            200,
            json=_openai_chat_completion_response(self._content, model=sent_body.get("model", "")),
            request=request,
        )


async def test_default_model_routes_through_model_list_to_configured_api_base() -> None:
    """`default_model: nemotron` must resolve via model_list and reach the
    configured api_base with the configured upstream model id -- this is
    the exact scenario that raised `UserError: Unknown model: nemotron`."""
    config = _make_config(default_model="nemotron")
    transport = RecordingTransport()

    with patch.object(httpx.AsyncClient, "send", new=transport):
        agent = ForgeAgent(config)
        result = await agent.run_conversational("hi")

    assert result.output == "hello from vLLM"
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert str(sent.url) == f"{NEMOTRON_API_BASE}/chat/completions"
    sent_body = json.loads(sent.content)
    assert sent_body["model"] == NEMOTRON_UPSTREAM_MODEL


async def test_configured_api_key_is_forwarded_as_bearer_auth() -> None:
    """The model_list entry's api_key must actually be sent upstream."""
    config = _make_config(default_model="nemotron")
    transport = RecordingTransport()

    with patch.object(httpx.AsyncClient, "send", new=transport):
        agent = ForgeAgent(config)
        await agent.run_conversational("hi")

    sent = transport.requests[0]
    assert sent.headers["authorization"] == "Bearer not-used-by-vllm"


async def test_unknown_default_model_alias_fails_loudly_at_agent_creation() -> None:
    """If model_list is configured but default_model isn't in it, Forge must
    raise a clear config error rather than silently doing nothing or
    deferring to a confusing runtime UserError."""
    config = _make_config(default_model="totally-not-configured")

    agent = ForgeAgent(config)
    try:
        await agent.initialize()
    except Exception as exc:  # noqa: BLE001 - asserting on message content below
        assert "totally-not-configured" in str(exc)
        assert "model_list" in str(exc)
    else:
        raise AssertionError("expected a config error for an unresolvable model alias")
