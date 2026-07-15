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

#: Public alias -- other modules (e.g. ``routes/admin.py``'s structural
#: SecretRef restore) need to recognize this exact sentinel too.
REDACTED_VALUE = _REDACTED

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


def redact_secrets(data: Any, known_values: frozenset[str] | None = None) -> None:
    """Recursively redact secret-shaped and secret-named values in place.

    Three redaction strategies (the third is optional defense-in-depth):

    1. Structural -- a resolved ``SecretRef`` (``{"source": "env" |
       "k8s_secret", "name": ..., "key": ...}``) is redacted by shape,
       preserving the dict's structure (only ``name``/``key`` are blanked).
    2. By key name -- any *scalar* leaf whose key matches
       :data:`SENSITIVE_KEY_NAMES` is blanked, regardless of where in the
       tree it appears. ``None`` values are left as ``None`` (an unset
       credential is not the same as a redacted one).
    3. By VALUE (only when *known_values* is supplied) -- any *string* leaf
       (a dict value OR a list item) that CONTAINS one of *known_values* is
       blanked. *known_values* is the set of resolved secret values reachable
       from config (see ``routes/admin._resolved_secret_values``), so a secret
       baked into a NON-sensitive free-text field (an agent ``system_prompt``,
       a tool ``description``, a workflow param) is withheld on read even
       though its key name is not sensitive -- this closes Vector B's read
       side.

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
            # Only a NON-null key is a real secret component to redact (and
            # later require restoring). An env-source SecretRef carries
            # key=None, which was never secret -- blanking it to the sentinel
            # made an honest GET round-trip un-restorable from a BASE that
            # never persisted a key, and 400'd the write (SECURITY round 2).
            if data.get("key") is not None:
                data["key"] = _REDACTED
            return
        for key, value in data.items():
            if isinstance(value, dict | list):
                redact_secrets(value, known_values)
            elif (is_sensitive_key(key) and value is not None) or _contains_known_secret(
                value, known_values
            ):
                data[key] = _REDACTED
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict | list):
                redact_secrets(item, known_values)
            elif _contains_known_secret(item, known_values):
                data[i] = _REDACTED


def _contains_known_secret(value: Any, known_values: frozenset[str] | None) -> bool:
    """True if *value* is a string containing any of *known_values* (the
    read-path VALUE scan; see :func:`redact_secrets` strategy 3)."""
    if not known_values or not isinstance(value, str):
        return False
    return any(secret in value for secret in known_values)


def scrub_text(value: str, known_values: frozenset[str] | None) -> str:
    """Return :data:`REDACTED_VALUE` if *value* contains any known resolved
    secret, else *value* unchanged -- for a bare string field (e.g. an admin
    tool ``description``) returned outside a dict that :func:`redact_secrets`
    would walk."""
    return _REDACTED if _contains_known_secret(value, known_values) else value
