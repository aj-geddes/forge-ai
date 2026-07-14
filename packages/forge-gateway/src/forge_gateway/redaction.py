"""Shared secret-redaction helpers for the admin API.

Factored out of ``routes/admin.py`` (security review finding #2, ADR-0005
SS11) so every admin surface that can expose secret-shaped or
secret-named values -- not just ``GET /v1/admin/config`` -- redacts
consistently. ``routes/approvals.py`` applies this to a gated tool call's
drafted ``arguments`` before serializing them to a ``config:read``-only
caller.
"""

from __future__ import annotations

from typing import Any

_REDACTED = "***REDACTED***"

# Security-review finding #1 (CRITICAL, admin config redaction):
# forge_config.loader substitutes ``${ENV_VAR}`` placeholders with their
# literal value BEFORE Pydantic validation (see
# ``forge_config/loader.py::_substitute_env_vars``). A value that was
# *meant* to be a SecretRef -- e.g.
# ``llm.litellm.model_list[].litellm_params.api_key`` -- lands in the
# validated model as an ordinary plaintext string inside a permissive
# ``dict[str, Any]`` field (``LiteLLMConfig.model_list``). The structural
# SecretRef redaction below only recognises the resolved-ref *shape*
# (``{"source": ..., "name": ..., "key": ...}``) and walks straight past a
# plain string, so a naive dump would leak the real key.
#
# This is the fail-safe complement: redact by KEY NAME, recursively,
# regardless of the value's type or shape. Keys are matched exactly
# (case-insensitive) rather than by substring, so a benign field like
# ``token_id`` (a service token's non-secret label) is not swept up by a
# looser match on "token".
SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "authorization",
        "client_secret",
        "private_key",
    }
)


def is_sensitive_key(key: str) -> bool:
    """Case-insensitive, exact match against :data:`SENSITIVE_KEY_NAMES`."""
    return key.lower() in SENSITIVE_KEY_NAMES


def is_secret_ref_shape(data: dict[str, Any]) -> bool:
    """True if *data* is a resolved ``SecretRef`` (``{"source": "env" |
    "k8s_secret", "name": ..., "key": ...}``)."""
    return "source" in data and "name" in data and data.get("source") in ("env", "k8s_secret")


def redact_secrets(data: Any) -> None:
    """Recursively redact secret-shaped and secret-named values in place.

    Two independent, both-required redaction strategies:

    1. Structural -- a resolved ``SecretRef`` (``{"source": "env" |
       "k8s_secret", "name": ..., "key": ...}``) is redacted by shape,
       preserving the dict's structure (only ``name``/``key`` are blanked).
    2. By key name -- any *scalar* leaf whose key matches
       :data:`SENSITIVE_KEY_NAMES` is blanked, regardless of where in the
       tree it appears. ``None`` values are left as ``None`` (an unset
       credential is not the same as a redacted one).

    Container values (dict/list) are always recursed into first -- even
    under a sensitive key name -- so a SecretRef nested under e.g.
    ``{"token": {"source": "env", ...}}`` is redacted structurally rather
    than being wholesale-blanked into an opaque string.

    Callers MUST pass a freshly serialized/copied structure (e.g.
    ``model.model_dump(mode="json")``, or a tool call's own keyword-argument
    dict, never a live config object or shared mutable state) -- this
    function mutates *data* in place and the caller owns that copy boundary.
    """
    if isinstance(data, dict):
        if is_secret_ref_shape(data):
            data["name"] = _REDACTED
            if "key" in data:
                data["key"] = _REDACTED
            return
        for key, value in data.items():
            if isinstance(value, dict | list):
                redact_secrets(value)
            elif is_sensitive_key(key) and value is not None:
                data[key] = _REDACTED
    elif isinstance(data, list):
        for item in data:
            redact_secrets(item)
