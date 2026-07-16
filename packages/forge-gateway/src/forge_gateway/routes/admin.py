"""Admin API routes for the Forge AI control plane.

Provides endpoints for config management, tool inspection, session
management, and peer status -- all consumed by the forge-ui frontend.

Read endpoints require the ``config:read`` permission; mutating endpoints
require ``config:write`` (ADR-0001 SS6.1). The static admin API key is
retired -- see ``forge_gateway.security``.

Layered BASE+OVERLAY config (see the design doc): every mutating route
funnels through :func:`apply_overlay_mutation`, the single choke point
that restores secret refs, merges, validates the WHOLE effective config,
runs the anti-escalation guard, atomically persists the overlay, appends
a hash-chained audit entry, and returns an honest envelope
(``persisted``/``durable``/``drift_from_git``/``rev``). A failed durable
write returns ``HTTP 507`` with ``persisted=False`` -- it is never
swallowed or masked as success.
"""

from __future__ import annotations

import copy
import difflib
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from forge_config import loader
from forge_config.exceptions import ConfigLoadError
from forge_config.loader import (
    compact_overlay,
    compute_base_rev,
    deep_merge,
    editable_sections,
    load_raw_config_dict,
    prune_noop_overlay,
    secret_refs,
)
from forge_config.overlay import (
    BASE_ONLY_KEYS,
    OverlayFieldError,
    project_overlay_safe,
    split_overlay_editable,
    validate_overlay_content,
)
from forge_config.schema import PERMISSION_WILDCARD, ForgeConfig, MutationPolicy, Permission
from forge_config.writable_store import OverlayConflictError, OverlayStore
from forge_security.egress import host_matches, validate_endpoint
from forge_security.oidc.principal import Principal
from pydantic import ValidationError

from forge_gateway import redaction, security
from forge_gateway.activity import recent_activity
from forge_gateway.auth import validate_peer_endpoint
from forge_gateway.models import (
    AdminActivityRecord,
    AdminActivityResponse,
    AdminAuditEntryResponse,
    AdminBaseConfigResponse,
    AdminConfigResponse,
    AdminConfigUpdateRequest,
    AdminConfigUpdateResponse,
    AdminHistoryResponse,
    AdminPeerCreateRequest,
    AdminPeerPingResponse,
    AdminPeerResponse,
    AdminPeerStatus,
    AdminRevertRequest,
    AdminSessionResponse,
    AdminToolInfo,
    AdminToolPreviewRequest,
    AdminToolPreviewResponse,
    ErrorResponse,
    PromotionDiffResponse,
)

logger = logging.getLogger("forge.gateway.admin")

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
)

_read = Depends(security.require_permission("config:read"))
_write_dep = security.require_permission("config:write")
_write_principal = Depends(_write_dep)
_entity_body: dict[str, Any] = Body(...)
_if_match_header: int | None = Header(default=None, alias="If-Match")

_ACTIVITY_DEFAULT_LIMIT = 50
_ACTIVITY_MAX_LIMIT = 200

#: Top-level ForgeConfig keys that can never be changed via the admin
#: mutation API -- structurally base-only (git-only). Mirrors
#: forge_config.overlay.BASE_ONLY_KEYS plus "conversation_store" is
#: already included there.
_BASE_ONLY_TOP_LEVEL_SECTIONS: frozenset[str] = frozenset(BASE_ONLY_KEYS)

#: Provenance keys stamped onto an overlay document -- never mergeable content.
_PROVENANCE_KEYS = frozenset({"_rev", "_base_rev", "_updated_by", "_updated_at"})

# Module-level state set from app lifespan
_config: ForgeConfig | None = None
_config_path: str = ""
_agent: Any = None
_overlay_store: OverlayStore | None = None
_base_path: str = ""

_UNSET: Any = object()


def set_state(
    config: ForgeConfig | None,
    config_path: str,
    agent: Any = _UNSET,
    *,
    overlay_store: Any = _UNSET,
    base_path: Any = _UNSET,
) -> None:
    """Wire admin state from the application lifespan.

    Pass ``agent``/``overlay_store``/``base_path`` to update them, or
    omit any of them to preserve the current value -- this allows config
    hot-reloads (which only know about ``config``/``config_path``) to
    update those without losing the agent or the overlay wiring.
    """
    global _config, _config_path, _agent, _overlay_store, _base_path
    _config = config
    _config_path = config_path
    if agent is not _UNSET:
        _agent = agent
    if overlay_store is not _UNSET:
        _overlay_store = overlay_store
    if base_path is not _UNSET:
        _base_path = base_path


def _effective_base_path() -> str:
    return _base_path or _config_path or os.environ.get("FORGE_CONFIG_SEED_PATH", "forge.yaml")


# --- apply_overlay_mutation: the single mutation choke point ---


async def apply_overlay_mutation(
    *,
    section: str,
    op: str,
    build_patch: Callable[[dict[str, Any]], dict[str, Any]],
    principal: Principal,
    permission_used: str,
    expected_rev: int | None = None,
    full_replace: bool = False,
) -> AdminConfigUpdateResponse:
    """Apply a config change via the overlay -- every mutating route
    funnels through this.

    Under a single lock acquisition (``OverlayStore.transaction()``):
    reads the current overlay, restores redacted ``SecretRef`` values by
    structural JSON-path from BASE (closing the previous ``_restore_secrets``
    TOCTOU window), builds the proposed new overlay content (either a
    name-keyed patch merged onto the current overlay, or a full
    replacement of the editable sections when *full_replace* is set),
    write-time-scrubs any pasted literal secret value, validates the
    WHOLE merged effective ``ForgeConfig``, runs the anti-escalation
    guard, atomically persists the overlay, and appends a hash-chained
    audit entry. Hot-reload (tool registry rebuild, auth reinit) happens
    AFTER the lock is released.

    Raises ``HTTPException``:
        405 -- ``mutation_policy`` is ``disabled``.
        403 -- *section* is base-only, or the anti-escalation guard denies.
        400 -- the merged config fails validation, or a literal secret
            was pasted where a reference was expected.
        409 -- *expected_rev* does not match the current rev.
        507 -- the durable overlay write failed (OSError). Never swallowed.
    """
    if _config is not None and _config.admin.mutation_policy == MutationPolicy.DISABLED:
        raise HTTPException(
            status_code=405,
            detail="config mutations are disabled (mutation_policy=disabled) -- edit BASE via git",
        )

    if section in _BASE_ONLY_TOP_LEVEL_SECTIONS:
        await _record_denial(
            section=section,
            op=op,
            principal=principal,
            permission_used=permission_used,
            reason="base_only_section",
        )
        raise HTTPException(
            status_code=403, detail=f"section '{section}' is base-only -- edit via git"
        )

    store = _overlay_store
    if store is None:
        raise HTTPException(
            status_code=507, detail="overlay store not configured; cannot persist config changes"
        )

    try:
        base_raw = load_raw_config_dict(_effective_base_path())
    except ConfigLoadError as e:
        raise HTTPException(status_code=500, detail=f"cannot read BASE config: {e}") from e

    base_rev = compute_base_rev(base_raw)
    new_effective_config: ForgeConfig | None = None
    new_state = None
    audit_diff: dict[str, Any] = {}

    try:
        async with store.transaction() as txn:
            current_state = txn.read_state()
            current_rev = current_state.rev if current_state else 0
            if expected_rev is not None and expected_rev != current_rev:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            f"stale rev: expected {expected_rev}, current rev is {current_rev}"
                        ),
                        "current_rev": current_rev,
                    },
                )

            current_overlay_raw = txn.read_overlay()
            current_overlay_content = {
                k: v for k, v in current_overlay_raw.items() if k not in _PROVENANCE_KEYS
            }

            patch = build_patch(current_overlay_content)
            _restore_secret_refs(patch, base_raw, current_overlay_content)

            new_overlay_content = (
                patch if full_replace else deep_merge(current_overlay_content, patch)
            )

            # SECURITY: never persist a redaction sentinel that could not be
            # restored (it would clobber the real secret and break the sink).
            _reject_unrestored_redactions(new_overlay_content)

            # SECURITY (Phase-1 field-level split): the load-bearing
            # STRUCTURAL gate. A base-only FIELD -- a tool/openapi
            # DESTINATION (url/base_url/endpoint), a SECRET binding
            # (api_key/auth/SecretRef/secret header), the
            # llm.litellm.model_list/endpoint/mode registry, a peer
            # endpoint (i.e. peer creation), or a requires_approval
            # downgrade -- is un-representable in a valid OverlayDocument,
            # so it is rejected here with a clear promote-via-git 400
            # (routing the UI to the export flow) instead of being silently
            # applied as an exfil/SSRF primitive. This runs on BOTH the
            # granular handlers AND the wholesale PUT /config full_replace
            # path (the former primary bypass).
            try:
                validate_overlay_content(new_overlay_content)
            except OverlayFieldError as e:
                await txn.append_audit(
                    {
                        "actor_sub": principal.sub,
                        "actor_email": principal.email,
                        "permission_used": permission_used,
                        "section": section,
                        "op": op,
                        "outcome": "denied",
                        "rev": current_rev,
                        "base_rev": base_rev,
                        "diff": {},
                        "reason": "base_only_field",
                    }
                )
                raise HTTPException(status_code=400, detail=str(e)) from e

            # SECURITY (SLICE 7): destinations are now runtime-editable, so
            # every repointed tool/openapi destination is gated here at write
            # time -- reject an internal literal (SSRF classifier) and, for a
            # credentialed tool, any host outside its BASE credential binding.
            # The connect-time GuardedBackend remains the authority.
            try:
                _enforce_destination_binding(new_overlay_content, base_raw)
            except HTTPException:
                await txn.append_audit(
                    {
                        "actor_sub": principal.sub,
                        "actor_email": principal.email,
                        "permission_used": permission_used,
                        "section": section,
                        "op": op,
                        "outcome": "denied",
                        "rev": current_rev,
                        "base_rev": base_rev,
                        "diff": {},
                        "reason": "destination_binding",
                    }
                )
                raise

            # SECURITY (secret exfiltration, Vector B / root cause): after
            # the Phase-1 field-level split NO overlay-editable field is a
            # secret SINK, so the per-ref BASE allowlist was itself the bug --
            # reject EVERY secret reference (${VAR} or structured SecretRef)
            # anywhere in the overlay content. The only fields still editable
            # are inert free text (agent system_prompt/description, a
            # ParameterDef default, a workflow step's params,
            # metadata.description); a ref in one can only resolve a pod secret
            # (FORGE_SESSION_KEY, an LLM api_key, a tool token) to cleartext and
            # leak it via GET /config or GET /agents.
            _reject_secret_refs_in_overlay(new_overlay_content)

            # SECURITY (SSRF + identity): every peer written through the
            # overlay -- including via a wholesale PUT /config that bypasses
            # the dedicated peer handlers -- must pass the same endpoint
            # guard, trust-elevation gate, and spiffe trust-domain check.
            _validate_overlay_peers(new_overlay_content, principal)

            try:
                # RESIDUAL A (write side): SUBSTRING scrub over the >=8-char-
                # floored resolved-secret set -- mirrors the read-path value-scan
                # so a secret embedded inside a larger free-text leaf cannot pass.
                known_secrets = _resolved_secret_values(base_raw)
                _scrub_resolved_secrets(new_overlay_content, known_secrets)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

            pruned = prune_noop_overlay(
                new_overlay_content,
                base_editable=editable_sections(base_raw),
                overlay_base_rev=base_rev,
            )
            # ALWAYS-SAFE structural compaction (unlike prune_noop_overlay,
            # unconditional -- it does not require BASE to have moved): drops
            # any by-name-list/dict container that has become structurally
            # empty (e.g. {"agents": {"agents": []}} after a runtime-only
            # agent is created then deleted). This is what is both persisted
            # and used to compute drift below, so an overlay that reduces to
            # a no-op is never left on disk as permanent, un-clearable drift.
            compacted = compact_overlay(pruned)
            merged_raw = deep_merge(base_raw, compacted)
            from forge_config.loader import _substitute_env_vars  # noqa: PLC0415

            substituted = _substitute_env_vars(merged_raw)
            try:
                new_effective_config = ForgeConfig.model_validate(substituted)
            except ValidationError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

            try:
                security.check_no_config_escalation(principal, config_after=new_effective_config)
            except security.ConfigEscalationDeniedError as e:
                await txn.append_audit(
                    {
                        "actor_sub": principal.sub,
                        "actor_email": principal.email,
                        "permission_used": permission_used,
                        "section": section,
                        "op": op,
                        "outcome": "denied",
                        "rev": current_rev,
                        "base_rev": base_rev,
                        "diff": {},
                        "reason": "escalation_denied",
                    }
                )
                raise HTTPException(status_code=403, detail=str(e)) from e

            try:
                new_state = await txn.write(
                    compacted,
                    base_rev=base_rev,
                    updated_by=principal.sub,
                    expected_rev=expected_rev,
                )
            except OverlayConflictError as e:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            f"stale rev: expected {e.expected_rev}, current rev is {e.current_rev}"
                        ),
                        "current_rev": e.current_rev,
                    },
                ) from e

            audit_diff = {"section": section, "op": op}
            await txn.append_audit(
                {
                    "actor_sub": principal.sub,
                    "actor_email": principal.email,
                    "permission_used": permission_used,
                    "section": section,
                    "op": op,
                    "outcome": "applied",
                    "rev": new_state.rev,
                    "base_rev": base_rev,
                    "diff": audit_diff,
                }
            )

            # Publish the new effective config to EVERY in-process runtime
            # consumer INSIDE the lock, in the same serialized order as the
            # durable write: admin's own _config, the conversational and
            # programmatic persona resolvers (so a newly-created agent is
            # immediately chat-resolvable instead of 404 "Unknown agent
            # persona"), the auth subsystem, and the A2A agent card. Two
            # concurrent mutations must never let the LATER-releasing
            # request's config overwrite the config from the request that
            # actually produced the higher rev (a bare post-lock assignment
            # would race on the order the event loop resumes each coroutine).
            # Routed through the shared apply_effective_config_in_process so
            # this and the disk-watch reload callback cannot drift; it is
            # fully synchronous (no await), so holding the lock across it is
            # safe. Wrapped so a propagation glitch can never fail an
            # already-persisted, already-audited mutation.
            try:
                from forge_gateway.app import (  # noqa: PLC0415
                    apply_effective_config_in_process,
                )

                apply_effective_config_in_process(
                    new_effective_config, _agent, config_path=_config_path
                )
            except Exception:
                logger.exception(
                    "Failed to propagate new effective config in-process after mutation"
                )
    except HTTPException:
        raise
    except OSError as e:
        logger.error("durable overlay write failed for section=%s op=%s: %s", section, op, e)
        raise HTTPException(status_code=507, detail=f"could not persist config change: {e}") from e

    assert new_effective_config is not None  # noqa: S101 -- set on every non-exceptional path above
    assert new_state is not None  # noqa: S101

    # Auth reinit, persona-resolver propagation, and the A2A agent-card
    # refresh already happened in-process INSIDE the lock above (via
    # apply_effective_config_in_process). The awaited tool rebuild stays
    # here so its result can populate the response's ``reloaded`` field.
    reloaded = False
    if _agent is not None:
        try:
            registry = getattr(_agent, "_registry", None)
            if registry is not None:
                reloaded = await registry.build_and_swap(new_effective_config)
        except Exception:
            logger.exception("Failed to reload tool surface after config mutation")

    drift = bool(compacted)
    parts = [f"Config {op} applied to '{section}'"]
    if reloaded:
        parts.append("tools reloaded")
    parts.append(f"rev {new_state.rev}")

    return AdminConfigUpdateResponse(
        success=True,
        reloaded=reloaded,
        persisted=True,
        durable=True,
        drift_from_git=drift,
        rev=new_state.rev,
        base_rev=base_rev,
        promotion_available=drift,
        message=" - ".join(parts),
    )


async def _record_denial(
    *, section: str, op: str, principal: Principal, permission_used: str, reason: str
) -> None:
    """Best-effort audit of a denial that happens BEFORE any overlay
    transaction starts (e.g. a base-only section). Never raises -- a
    logging failure must not mask the real HTTPException."""
    if _overlay_store is None:
        return
    try:
        await _overlay_store.append_audit_locked(
            {
                "actor_sub": principal.sub,
                "actor_email": principal.email,
                "permission_used": permission_used,
                "section": section,
                "op": op,
                "outcome": "denied",
                "diff": {},
                "reason": reason,
            }
        )
    except OSError:
        logger.exception("Failed to record denial audit entry (section=%s, op=%s)", section, op)


# --- Secret handling: structural restore + write-time scrub ---


#: Fields, in priority order, that STABLY identify an entry in a
#: secret-bearing list so a redacted secret is restored by IDENTITY rather
#: than by positional index. ``llm.litellm.model_list`` keys on
#: ``model_name`` (NOT ``name``); tools/agents/peers key on ``name``. A
#: positional guess mis-restores across a reorder/delete and clobbers the
#: WRONG entry's secret (SECURITY round 2), so position is never used.
_STABLE_LIST_KEY_FIELDS: tuple[str, ...] = ("name", "model_name")

#: Path marker for a list entry with no stable key: it can never resolve
#: back to a BASE entry, so any redaction sentinel under it is left in place
#: (and :func:`_reject_unrestored_redactions` turns it into a 400) rather
#: than positional-guessed.
_UNRESOLVABLE_SEGMENT: tuple[str] = ("__unresolvable__",)


def _stable_list_key(item: Any) -> tuple[str, str] | None:
    """The ``(field, value)`` that stably identifies *item* within its list,
    or ``None`` if it has no stable key (never fall back to position)."""
    if isinstance(item, dict):
        for field in _STABLE_LIST_KEY_FIELDS:
            val = item.get(field)
            if isinstance(val, str):
                return (field, val)
    return None


def _resolve_path(root: Any, path: list[Any]) -> Any:
    node = root
    for segment in path:
        if segment == _UNRESOLVABLE_SEGMENT:
            return None
        if isinstance(segment, tuple) and segment and segment[0] == "__by_key__":
            if not isinstance(node, list):
                return None
            _, key_field, key_value = segment
            node = next(
                (i for i in node if isinstance(i, dict) and i.get(key_field) == key_value),
                None,
            )
        elif isinstance(node, dict):
            node = node.get(segment)
        else:
            return None
        if node is None:
            return None
    return node


def _restore_secret_refs_at(
    node: Any, base_raw: dict[str, Any], overlay_raw: dict[str, Any], path: list[Any]
) -> None:
    if isinstance(node, dict):
        is_redacted_ref = (
            node.get("source") in ("env", "k8s_secret")
            and node.get("name") == redaction.REDACTED_VALUE
        )
        if is_redacted_ref:
            original = _resolve_path(base_raw, path) or _resolve_path(overlay_raw, path)
            if isinstance(original, dict):
                if "name" in original:
                    node["name"] = original["name"]
                if "key" in original and node.get("key") == redaction.REDACTED_VALUE:
                    node["key"] = original["key"]
            return
        for k, v in node.items():
            if (
                isinstance(v, str)
                and v == redaction.REDACTED_VALUE
                and redaction.is_sensitive_key(k)
            ):
                # A plaintext secret that ``redact_secrets`` blanked BY KEY
                # NAME (e.g. an inline ``api_key: "${OPENAI_API_KEY}"`` or a
                # ``ManualToolAPI.headers`` "Authorization" value) -- restore
                # it by STRUCTURAL json-path from BASE (fallback overlay), the
                # same way SecretRef-shaped values are restored, so a UI
                # round-trip of the redacted read-back never clobbers the real
                # value. If BASE has no value at this path the sentinel is left
                # in place and _reject_unrestored_redactions raises 400.
                original = _resolve_path(base_raw, [*path, k])
                if original is None:
                    original = _resolve_path(overlay_raw, [*path, k])
                if isinstance(original, str):
                    node[k] = original
            else:
                _restore_secret_refs_at(v, base_raw, overlay_raw, [*path, k])
    elif isinstance(node, list):
        for item in node:
            key = _stable_list_key(item)
            if key is not None:
                _restore_secret_refs_at(
                    item, base_raw, overlay_raw, [*path, ("__by_key__", key[0], key[1])]
                )
            else:
                # No stable key -> never positional-guess; descend with an
                # unresolvable marker so any redaction sentinel here is left
                # in place and _reject_unrestored_redactions raises 400.
                _restore_secret_refs_at(item, base_raw, overlay_raw, [*path, _UNRESOLVABLE_SEGMENT])


def _restore_secret_refs(
    patch: dict[str, Any], base_raw: dict[str, Any], current_overlay_content: dict[str, Any]
) -> None:
    """Restore redacted ``SecretRef`` values in *patch* by STRUCTURAL
    JSON-path lookup against BASE (falling back to the current overlay
    content, for a secret-bearing entity that only ever existed in the
    overlay) -- never by matching the redacted VALUE, and never outside
    the caller's single lock acquisition (closing the previous
    ``_restore_secrets`` TOCTOU window)."""
    _restore_secret_refs_at(patch, base_raw, current_overlay_content, [])


def _scrub_resolved_secrets(node: Any, known_secrets: frozenset[str], path: str = "$") -> None:
    """Reject (raise ``ValueError``) any string leaf in *node* that CONTAINS a
    known resolved secret value -- the overlay must only ever contain
    ``${ENV_VAR}``/``SecretRef`` references, never a resolved value someone
    pasted directly, even embedded as a SUBSTRING of a larger free-text field
    (e.g. ``"...the key is <secret>..."`` in an agent ``system_prompt``).

    This SUBSTRING match mirrors the read-path value-scan
    (:func:`redaction._contains_known_secret`); the prior exact-equality gate let
    such an embedded literal through the write path while the read path blanked
    it -- an asymmetry that surfaced the value in cleartext via the (previously
    unredacted) promotion diff. *known_secrets* is
    :func:`_resolved_secret_values` (the ``>=8``-char-floored set) so a
    trivially-common short substring does not over-reject."""
    if not known_secrets:
        return
    if isinstance(node, dict):
        is_secret_ref_shape = node.get("source") in ("env", "k8s_secret")
        for k, v in node.items():
            if is_secret_ref_shape and k in ("name", "key"):
                continue  # the ref's own metadata (an env var NAME), not a pasted secret value
            _scrub_resolved_secrets(v, known_secrets, f"{path}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scrub_resolved_secrets(item, known_secrets, f"{path}[{i}]")
    elif isinstance(node, str) and any(secret in node for secret in known_secrets):
        msg = (
            f"a literal secret value was found at {path} -- use a ${{ENV_VAR}} "
            "reference or SecretRef instead of pasting a resolved secret"
        )
        raise ValueError(msg)


def _reject_unrestored_redactions(node: Any, path: str = "$") -> None:
    """Raise ``HTTP 400`` if any redaction sentinel (``***REDACTED***``)
    survives into the overlay content about to be persisted.

    ``redact_secrets`` blanks secrets both structurally (SecretRef shape)
    AND by key name (an inline plaintext secret). The restore step reverses
    both, but a caller can submit the sentinel at a path BASE has no value
    for. Persisting it would clobber the real secret (all downstream calls
    then break) and let the sentinel masquerade as a value -- so reject the
    whole mutation instead."""
    if isinstance(node, dict):
        for k, v in node.items():
            _reject_unrestored_redactions(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _reject_unrestored_redactions(item, f"{path}[{i}]")
    elif isinstance(node, str) and node == redaction.REDACTED_VALUE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"a redacted secret placeholder ({redaction.REDACTED_VALUE}) at {path} "
                "could not be restored from BASE -- resubmit the real ${ENV_VAR} or "
                "SecretRef value for this field instead of the redacted read-back"
            ),
        )


def _find_secret_ref_paths(node: Any, path: str = "$") -> list[str]:
    """Every json-path in *node* holding a secret reference in EITHER
    encoding: a string matching ``loader._ENV_PATTERN`` (``${VAR}`` /
    ``${VAR:default}`` / an embedded ``pre${VAR}post``) or a structured
    ``SecretRef`` dict (``source`` in env/k8s_secret with a ``name``/``key``)."""
    found: list[str] = []

    def _walk(n: Any, p: str) -> None:
        if isinstance(n, str):
            if loader._ENV_PATTERN.search(n):
                found.append(p)
        elif isinstance(n, dict):
            if n.get("source") in ("env", "k8s_secret") and ("name" in n or "key" in n):
                found.append(p)
            for k, v in n.items():
                _walk(v, f"{p}.{k}")
        elif isinstance(n, list):
            for i, item in enumerate(n):
                _walk(item, f"{p}[{i}]")

    _walk(node, path)
    return found


def _reject_secret_refs_in_overlay(overlay_content: dict[str, Any]) -> None:
    """Raise ``HTTP 400`` if overlay content contains ANY secret reference, in
    EITHER encoding, anywhere.

    ROOT CAUSE (Phase-1 final). After the field-level split, NO
    overlay-editable field is a legitimate secret SINK: every destination and
    secret binding (``api_key``/``auth``/``headers``/``model_list``/
    ``endpoint``) is base-only and structurally un-representable in a valid
    ``OverlayDocument`` (already rejected upstream by
    :func:`validate_overlay_content`). The only fields that remain editable are
    inert free text -- an ``AgentDef.system_prompt``/``description``, a
    ``ManualTool``/``Workflow`` ``ParameterDef.default``, a
    ``WorkflowStep.params`` value, ``metadata.description``. A ``${VAR}`` /
    ``${VAR:default}`` placeholder (resolved by ``_substitute_env_vars``) or a
    structured ``SecretRef`` (resolved by ``forge_config.secret_resolver``) in
    ANY of those is never legitimate content -- it can only be an attempt to
    resolve a pod secret (an LLM provider ``api_key``, a tool bearer token,
    ``FORGE_SESSION_KEY``) to cleartext and leak it back via ``GET /config`` /
    ``GET /agents`` (Vector B). So the previous per-ref-id BASE allowlist
    (``secret_refs(...) - base_allowed`` -- which permitted any reference BASE
    already used) was ITSELF the bug: reject EVERY secret reference outright and
    route the operator to git. Runs on BOTH the granular handlers AND the
    wholesale ``PUT /config`` full_replace path."""
    locations = _find_secret_ref_paths(overlay_content)
    if not locations:
        return
    refs = sorted(secret_refs(overlay_content))
    raise HTTPException(
        status_code=400,
        detail=(
            f"overlay contains secret reference(s) {refs or locations} at {locations} "
            "-- promote via git; secrets are not editable at runtime. No overlay-editable "
            "field (system_prompt/description/parameter default/workflow step params/"
            "metadata.description) is a secret sink, so a ${VAR} or SecretRef in one can "
            "only be an attempt to resolve a pod secret (a session signing key, an LLM "
            "provider api_key, a tool bearer token) to cleartext and leak it via GET "
            "/config or GET /agents"
        ),
    )


#: A resolved secret shorter than this is NOT used for the read-path VALUE
#: scan -- a short/common substring would over-redact benign content; real API
#: keys / signing keys are far longer.
_MIN_SCANNED_SECRET_LEN = 8


def _resolved_secret_values(base_raw: dict[str, Any]) -> frozenset[str]:
    """Every env-resolved secret VALUE reachable from BASE (via a ``${VAR}``
    string OR a structured ``env`` ``SecretRef``), for the read-path value scan
    -- so a resolved secret baked into a non-sensitive free-text field (an agent
    ``system_prompt``, a tool ``description``) is blanked on read even though
    its key name is not sensitive. ``k8s_secret`` refs are skipped (their value
    is not in the process env). Short values are dropped to avoid over-redacting
    on a trivially-common substring."""
    values: set[str] = set()
    for ref in secret_refs(base_raw):
        if ref.startswith("env:"):
            val = os.environ.get(ref[len("env:") :])
            if val and len(val) >= _MIN_SCANNED_SECRET_LEN:
                values.add(val)
    return frozenset(values)


def _read_base_raw_or_empty() -> dict[str, Any]:
    """BASE as a raw dict, or ``{}`` if it cannot be read -- best-effort input
    to :func:`_resolved_secret_values` on read paths (a missing/broken BASE must
    never 500 a redacted GET; the structural + key-name redaction still
    applies)."""
    try:
        return load_raw_config_dict(_effective_base_path())
    except ConfigLoadError:
        return {}


#: Ordinal rank of each peer trust tier. A config:write caller may set a
#: peer trust_level up to the safe ceiling below via the overlay; setting it
#: HIGHER self-assigns a more-trusted identity and requires a permission
#: beyond config:write (SECURITY round 2).
_TRUST_LEVEL_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

#: The highest trust rank a plain config:write caller may set via the
#: overlay without elevation. "low" (0) -- raising a peer above it is a
#: privileged act (git BASE edit, or the elevation permission below).
_MAX_OVERLAY_TRUST_RANK_WITHOUT_ELEVATION = _TRUST_LEVEL_RANK["low"]

#: The permission required to raise a peer's trust_level above the safe
#: overlay ceiling. Held by the built-in ``admin`` role (via its wildcard),
#: not by a plain config:write role.
_TRUST_ELEVATION_PERMISSION = Permission.INFRASTRUCTURE_WRITE.value


def _may_elevate_peer_trust(principal: Principal) -> bool:
    """Whether *principal* may set a peer trust_level above the safe overlay
    ceiling. config:write alone is insufficient -- that would let a caller
    self-assign the highest tier; it requires ``infrastructure:write`` (or
    the admin wildcard)."""
    perms = principal.permissions
    return _TRUST_ELEVATION_PERMISSION in perms or PERMISSION_WILDCARD in perms


def _configured_trust_domain() -> str:
    """The workload trust domain from BASE
    (``security.agentweave.trust_domain``). It is base-only, so the live
    in-memory config reflects it; defaults to the schema default before
    config is wired."""
    if _config is not None:
        return _config.security.agentweave.trust_domain
    return "forge.local"


def _validate_overlay_peers(overlay_content: dict[str, Any], principal: Principal) -> None:
    """Run the peer SSRF + identity guard over EVERY ``agents.peers`` entry
    the overlay writes -- not only the dedicated ``create_peer``/
    ``patch_peer`` handlers.

    A wholesale ``PUT /config`` (``full_replace``) writes ``agents.peers``
    straight from the incoming payload, so without this the dedicated
    handlers' ``validate_peer_endpoint`` check is bypassed and a caller can
    add a peer pointed at ``169.254.169.254`` / ``kubernetes.default`` /
    ``*.svc`` and drive SSRF via peer ping/call.

    Also constrains two self-assignable identity fields (SECURITY round 2):
    a ``config:write`` caller may not raise a peer's ``trust_level`` above
    the safe overlay ceiling without ``infrastructure:write`` (it was only
    enum-validated before -- so the highest tier was self-assignable), and
    any pinned ``spiffe_id`` must be a ``spiffe://`` URI whose trust domain
    is the configured one (not an attacker-chosen authority with no
    allowlist)."""
    agents = overlay_content.get("agents")
    if not isinstance(agents, dict):
        return
    peers = agents.get("peers")
    if not isinstance(peers, list):
        return
    trust_domain = _configured_trust_domain()
    for peer in peers:
        if not isinstance(peer, dict) or set(peer.keys()) == {"__deleted__"}:
            continue
        name = peer.get("name")
        endpoint = peer.get("endpoint")
        if isinstance(endpoint, str) and not validate_peer_endpoint(endpoint):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"peer '{name}' endpoint targets a private, link-local, or "
                    "cluster-internal network -- rejected by SSRF policy"
                ),
            )

        trust_level = peer.get("trust_level")
        if isinstance(trust_level, str):
            rank = _TRUST_LEVEL_RANK.get(trust_level.lower())
            if (
                rank is not None
                and rank > _MAX_OVERLAY_TRUST_RANK_WITHOUT_ELEVATION
                and not _may_elevate_peer_trust(principal)
            ):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"peer '{name}' trust_level '{trust_level}' exceeds the safe "
                        "overlay default 'low'; raising peer trust at runtime requires the "
                        "'infrastructure:write' permission (or promote the change to BASE "
                        "via git)"
                    ),
                )

        spiffe_id = peer.get("spiffe_id")
        if spiffe_id is not None:
            if (
                not isinstance(spiffe_id, str)
                or not spiffe_id.startswith("spiffe://")
                or len(spiffe_id) > 2048
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"peer '{name}' spiffe_id must be a spiffe:// URI",
                )
            authority = spiffe_id[len("spiffe://") :].split("/", 1)[0].lower()
            if authority != trust_domain.lower():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"peer '{name}' spiffe_id trust domain '{authority}' is not the "
                        f"configured trust domain '{trust_domain}' -- a peer identity may "
                        "only be pinned within this deployment's trust domain"
                    ),
                )


# --- SLICE 7: runtime-editable destinations, binding-gated ---
#
# Phase 1 made tool destinations (url/base_url/endpoint) structurally
# BASE-ONLY. SLICE 7 relaxes the OverlayDocument schema to ALLOW those
# destination fields at runtime, and moves the safety from "structurally
# un-representable" to this WRITE-TIME gate: a destination edit may never
# become an SSRF or credential-exfiltration primitive.
#
# Two independent checks per repointed destination:
#   1. SSRF classifier (defense-in-depth) -- reject an internal/private/
#      metadata/blocked literal early. The connect-time GuardedBackend
#      remains the authority; this is the cheap first-line write-time gate.
#   2. Credential binding -- a tool that carries a credential in BASE
#      (auth.type != none) may only be repointed WITHIN its bound host set
#      (BASE auth.allowed_hosts, or the BASE-declared origin host when
#      allowed_hosts is empty -- "pin to where you were pointed"). A tool
#      with NO credential may move to any PUBLIC host (check 1 still blocks
#      internal). Secrets stay git-only, so a runtime-created tool can never
#      acquire a credential and is forever bound only by check 1.


def _index_by_name(items: Any) -> dict[str, Any]:
    """Index a list of name-keyed dicts by their ``name`` (skipping any
    entry without a string ``name``, e.g. a tombstone)."""
    if not isinstance(items, list):
        return {}
    result: dict[str, Any] = {}
    for e in items:
        if isinstance(e, dict) and isinstance(e.get("name"), str):
            result[e["name"]] = e
    return result


def _resolved_host_from_manual_api(api: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(hostname, resolved_url)`` for a manual-tool ``api`` dict,
    mirroring ``ManualToolAPI.resolved_url`` (``base_url`` + ``endpoint``
    take precedence over ``url``)."""
    if not isinstance(api, dict):
        return (None, None)
    base_url = api.get("base_url")
    endpoint = api.get("endpoint")
    url = api.get("url")
    if isinstance(base_url, str) and isinstance(endpoint, str):
        target = base_url.rstrip("/") + endpoint
    elif isinstance(url, str):
        target = url
    else:
        return (None, None)
    return (urlsplit(target).hostname, target)


def _manual_credential_binding(base_tool: dict[str, Any] | None) -> tuple[bool, frozenset[str]]:
    """``(has_credential, bound_hosts)`` for a BASE manual tool.

    A tool "carries a credential" iff its BASE ``api.auth.type`` is not
    ``none``. The bound host set is ``auth.allowed_hosts`` (lowercased) when
    non-empty, else the single BASE-declared origin host.
    """
    if not isinstance(base_tool, dict):
        return (False, frozenset())
    api = base_tool.get("api")
    if not isinstance(api, dict):
        return (False, frozenset())
    auth = api.get("auth")
    if not isinstance(auth, dict):
        return (False, frozenset())
    if (auth.get("type") or "none") == "none":
        return (False, frozenset())
    allowed = auth.get("allowed_hosts")
    if isinstance(allowed, list) and allowed:
        return (True, frozenset(str(h).lower() for h in allowed))
    origin = _resolved_host_from_manual_api(api)[0]
    return (True, frozenset({origin.lower()}) if origin else frozenset())


def _openapi_credential_binding(base_src: dict[str, Any] | None) -> tuple[bool, frozenset[str]]:
    """``(has_credential, bound_hosts)`` for a BASE OpenAPI source -- the
    same rule as :func:`_manual_credential_binding`, with the origin taken
    from the BASE-declared ``url`` host."""
    if not isinstance(base_src, dict):
        return (False, frozenset())
    auth = base_src.get("auth")
    if not isinstance(auth, dict):
        return (False, frozenset())
    if (auth.get("type") or "none") == "none":
        return (False, frozenset())
    allowed = auth.get("allowed_hosts")
    if isinstance(allowed, list) and allowed:
        return (True, frozenset(str(h).lower() for h in allowed))
    url = base_src.get("url")
    origin = urlsplit(url).hostname if isinstance(url, str) else None
    return (True, frozenset({origin.lower()}) if origin else frozenset())


def _reject_internal_destination(name: Any, url: str) -> None:
    """Raise ``HTTP 400`` if *url* targets an internal/private/blocked
    address (SSRF classifier, defense-in-depth; the connect-time guard is
    the authority)."""
    if not validate_endpoint(url):
        raise HTTPException(
            status_code=400,
            detail=(
                f"destination for tool {name!r} ({url!r}) targets an internal, "
                "private, or blocked address -- rejected by the SSRF classifier at "
                "write time; point it at a public host or promote via git"
            ),
        )


def _enforce_destination_binding(overlay_content: dict[str, Any], base_raw: dict[str, Any]) -> None:
    """SLICE 7 write-time gate: a runtime destination edit must never become
    an SSRF or credential-exfiltration primitive.

    For every ``manual_tool`` whose overlay entry sets a destination field
    (``url``/``base_url``/``endpoint``) and every ``openapi_source`` whose
    overlay entry sets ``url``: reject an internal destination
    (defense-in-depth) and, if the tool carries a BASE credential, reject
    unless the new host is within that credential's binding. A tool with no
    BASE credential may move to any public host.
    """
    tools = overlay_content.get("tools")
    if not isinstance(tools, dict):
        return

    base_tools = base_raw.get("tools") if isinstance(base_raw, dict) else {}
    if not isinstance(base_tools, dict):
        base_tools = {}

    base_manual = _index_by_name(base_tools.get("manual_tools"))
    for entry in tools.get("manual_tools") or []:
        if not isinstance(entry, dict) or set(entry) == {"__deleted__"}:
            continue
        api = entry.get("api")
        if not isinstance(api, dict):
            continue
        if not any(k in api for k in ("url", "base_url", "endpoint")):
            continue
        name = entry.get("name")
        base_tool = base_manual.get(name) if isinstance(name, str) else None
        base_api = base_tool.get("api") if isinstance(base_tool, dict) else None
        eff_api = {**(base_api if isinstance(base_api, dict) else {}), **api}
        new_host, target_url = _resolved_host_from_manual_api(eff_api)
        if new_host is None or target_url is None:
            continue
        _reject_internal_destination(name, target_url)
        has_cred, bound = _manual_credential_binding(base_tool)
        if has_cred and bound and not host_matches(new_host, bound):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"tool {name!r} may not be repointed to host {new_host!r}: it "
                    f"carries a credential bound to {sorted(bound)} (its BASE "
                    "auth.allowed_hosts or origin host); a credentialed tool may only "
                    "be repointed within its bound host set -- move the binding in BASE "
                    "and promote via git"
                ),
            )

    base_openapi = _index_by_name(base_tools.get("openapi_sources"))
    for entry in tools.get("openapi_sources") or []:
        if not isinstance(entry, dict) or set(entry) == {"__deleted__"}:
            continue
        if "url" not in entry:
            continue
        url = entry.get("url")
        if not isinstance(url, str):
            continue
        name = entry.get("name")
        base_src = base_openapi.get(name) if isinstance(name, str) else None
        _reject_internal_destination(name, url)
        has_cred, bound = _openapi_credential_binding(base_src)
        new_host = urlsplit(url).hostname
        if has_cred and bound and new_host and not host_matches(new_host, bound):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"openapi source {name!r} may not be repointed to host "
                    f"{new_host!r}: it carries a credential bound to {sorted(bound)}; a "
                    "credentialed source may only be repointed within its bound host "
                    "set -- promote via git"
                ),
            )


# --- Config endpoints ---


@router.get(
    "/config",
    response_model=AdminConfigResponse,
    responses={500: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def get_config(response: Response) -> AdminConfigResponse:
    """Return the current EFFECTIVE config (BASE deep-merged with the
    overlay) with secrets redacted."""
    if _config is None:
        raise HTTPException(status_code=500, detail="No config loaded")

    known_values = _resolved_secret_values(_read_base_raw_or_empty())
    data = _config.model_dump(mode="json")
    _redact_secrets(data, known_values)

    rev = 0
    base_rev: str | None = None
    drift = False
    layers = ["base"]
    if _overlay_store is not None:
        state = _overlay_store.read_state()
        if state is not None:
            rev = state.rev
            base_rev = state.base_rev
            layers = ["base", "overlay"]
            overlay_raw = _overlay_store.read_overlay()
            overlay_content = {k: v for k, v in overlay_raw.items() if k not in _PROVENANCE_KEYS}
            try:
                base_raw = load_raw_config_dict(_effective_base_path())
                pruned = prune_noop_overlay(
                    overlay_content,
                    base_editable=editable_sections(base_raw),
                    overlay_base_rev=overlay_raw.get("_base_rev"),
                )
                # READ-PATH self-heal: mirror the write path's ALWAYS-SAFE
                # structural compaction (see apply_overlay_mutation) so a
                # no-op scaffold already sitting on disk at BOOT (e.g. a
                # create-then-delete runtime-only agent from a prior
                # process) reports drift_from_git=False immediately,
                # instead of staying stuck True until the next mutation.
                drift = bool(compact_overlay(pruned))
            except ConfigLoadError:
                drift = bool(overlay_content)

    response.headers["ETag"] = str(rev)
    return AdminConfigResponse(
        config=data,
        path=_config_path,
        rev=rev,
        base_rev=base_rev,
        drift_from_git=drift,
        source_layers=layers,
        mutation_policy=_config.admin.mutation_policy.value,
    )


@router.get(
    "/config/base",
    response_model=AdminBaseConfigResponse,
    responses={500: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def get_base_config() -> AdminBaseConfigResponse:
    """Return BASE (git/ArgoCD source of truth) alone, secrets redacted,
    for diffing against the effective config in the UI."""
    try:
        base_raw = load_raw_config_dict(_effective_base_path())
    except ConfigLoadError as e:
        raise HTTPException(status_code=500, detail=f"cannot read BASE config: {e}") from e

    base_rev = compute_base_rev(base_raw)
    known_values = _resolved_secret_values(base_raw)
    redacted = copy.deepcopy(base_raw)
    _redact_secrets(redacted, known_values)
    return AdminBaseConfigResponse(config=redacted, base_rev=base_rev)


@router.get(
    "/config/promotion/diff",
    response_model=PromotionDiffResponse,
    responses={500: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def get_promotion_diff() -> PromotionDiffResponse:
    """Unified diff of the overlay's content against BASE, plus a
    ready-to-paste PR body. Export-only: no git write happens here or
    anywhere in this pod -- promoting is always a human action."""
    if _overlay_store is None:
        return PromotionDiffResponse(
            diff="", pr_body="", rev=0, base_rev=None, drift_from_git=False
        )

    try:
        base_raw = load_raw_config_dict(_effective_base_path())
    except ConfigLoadError as e:
        raise HTTPException(status_code=500, detail=f"cannot read BASE config: {e}") from e

    base_rev = compute_base_rev(base_raw)
    overlay_raw = _overlay_store.read_overlay()
    state = _overlay_store.read_state()
    rev = state.rev if state else 0
    overlay_content = {k: v for k, v in overlay_raw.items() if k not in _PROVENANCE_KEYS}
    base_editable = editable_sections(base_raw)
    pruned = prune_noop_overlay(
        overlay_content, base_editable=base_editable, overlay_base_rev=overlay_raw.get("_base_rev")
    )
    # READ-PATH self-heal: same ALWAYS-SAFE structural compaction as
    # get_config/apply_overlay_mutation, so a no-op scaffold on disk at
    # BOOT yields an empty promotion diff and drift_from_git=False below,
    # not just after the next mutation touches the overlay.
    compacted = compact_overlay(pruned)
    merged_editable = deep_merge(base_editable, compacted)

    base_yaml = yaml.dump(base_editable, sort_keys=True, default_flow_style=False).splitlines(
        keepends=True
    )
    merged_yaml = yaml.dump(merged_editable, sort_keys=True, default_flow_style=False).splitlines(
        keepends=True
    )
    diff_text = "".join(
        difflib.unified_diff(
            base_yaml, merged_yaml, fromfile="BASE (forge.yaml)", tofile="overlay (promoted)"
        )
    )

    # RESIDUAL A (read side): the diff + PR body are built from UNSUBSTITUTED
    # base_raw, so a secret still appears only as ${VAR} there. But a resolved
    # secret LITERAL that a prior overlay smuggled into an editable free-text
    # field (an agent system_prompt) would surface in cleartext -- run the
    # read-path resolved-value scan over both texts (mirrors GET /config).
    known_values = _resolved_secret_values(base_raw)
    diff_text = redaction.redact_text(diff_text, known_values)

    pr_body = (
        f"## Promote Forge config overlay (rev {rev})\n\n"
        "Auto-generated from the running instance's config overlay. Paste this "
        "diff into a commit against `forge.yaml` (the BASE editable sections: "
        "`tools`, `agents`, `llm`, `metadata.description`).\n\n"
        f"```diff\n{diff_text}```\n\n"
        "Once merged and reconciled by ArgoCD, the overlay's promoted entries "
        "are pruned automatically on next load -- drift_from_git returns to "
        "zero without any further action.\n"
    )
    pr_body = redaction.redact_text(pr_body, known_values)
    return PromotionDiffResponse(
        diff=diff_text, pr_body=pr_body, rev=rev, base_rev=base_rev, drift_from_git=bool(compacted)
    )


@router.get(
    "/config/history",
    response_model=AdminHistoryResponse,
    dependencies=[_read],
)
async def get_config_history(
    limit: int = Query(default=50, ge=1, le=200),
) -> AdminHistoryResponse:
    """Paginated (newest-first) view of the hash-chained config audit journal."""
    if _overlay_store is None:
        return AdminHistoryResponse(entries=[], total=0)
    entries = _overlay_store.read_audit_entries()
    ordered = sorted(entries, key=lambda e: e.seq, reverse=True)
    page = ordered[:limit]
    return AdminHistoryResponse(
        entries=[
            AdminAuditEntryResponse(**e.model_dump(mode="json", exclude={"prev_entry_sha256"}))
            for e in page
        ],
        total=len(entries),
    )


def _find_history_snapshot(rev: int) -> Path | None:
    if _overlay_store is None:
        return None
    candidates = [p for p in _overlay_store.list_history() if p.name.startswith(f"overlay-{rev}-")]
    return candidates[-1] if candidates else None


@router.post(
    "/config/revert",
    response_model=AdminConfigUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def revert_config(
    request: AdminRevertRequest,
    principal: Principal = _write_principal,
) -> AdminConfigUpdateResponse:
    """Drop the overlay entirely (``to_rev`` omitted) or roll back to a
    prior overlay revision snapshot. Either way this creates a NEW rev
    -- history is never rewritten."""
    new_content: dict[str, Any] = {}
    if request.to_rev is not None:
        snapshot = _find_history_snapshot(request.to_rev)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"history rev {request.to_rev} not found")
        raw = yaml.safe_load(snapshot.read_text(encoding="utf-8")) or {}
        new_content = {k: v for k, v in raw.items() if k not in _PROVENANCE_KEYS}

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return new_content

    return await apply_overlay_mutation(
        section="config",
        op="revert",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        full_replace=True,
    )


@router.put(
    "/config",
    response_model=AdminConfigUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def update_config(
    request: AdminConfigUpdateRequest,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    """Replace the editable sections (tools/agents/llm/metadata.description)
    from a full config-shaped payload. Base-only top-level sections
    (security, conversation_store) are rejected with 403 if the payload
    tries to actually CHANGE them -- an honest round-trip of the current
    value is not penalized."""
    incoming = request.config
    incoming_editable = editable_sections(incoming)
    safe_patch = project_overlay_safe(incoming_editable)
    if _config is not None:
        # Redact BOTH sides identically before comparing: the client's
        # payload almost always came from a prior GET, whose secret
        # values are already redacted -- comparing against the live
        # (unredacted) config would spuriously treat every honest
        # round-trip as a base-only change.
        current_effective_redacted = _config.model_dump(mode="json")
        _redact_secrets(current_effective_redacted)
        incoming_redacted = copy.deepcopy(incoming)
        _redact_secrets(incoming_redacted)
        for key in ("security", "conversation_store"):
            current_val = current_effective_redacted.get(key)
            if key in incoming_redacted and incoming_redacted[key] != current_val:
                raise HTTPException(status_code=403, detail=f"'{key}' is base-only -- edit via git")

        # SECURITY (Phase-1 field-level split): a wholesale PUT round-trips
        # the WHOLE editable config (which always carries base-only fields --
        # a tool url, the llm.litellm defaults, a peer endpoint). Project it
        # down to the overlay-safe subset AND detect any base-only field
        # whose value actually CHANGED. An honest round-trip (unchanged
        # base-only fields) is tolerated and those fields are simply not
        # written; a real repoint/secret-plant/new-destination is surfaced
        # as a promote-via-git 400 rather than silently applied.
        effective_editable = editable_sections(current_effective_redacted)
        incoming_editable_redacted = editable_sections(incoming_redacted)
        safe_patch, changed = split_overlay_editable(
            incoming_editable,
            incoming_for_diff=incoming_editable_redacted,
            effective_for_diff=effective_editable,
        )
        if changed:
            raise HTTPException(
                status_code=400,
                detail=(
                    "config update changes base-only field(s) "
                    f"{sorted(changed)}: destinations (url/base_url/endpoint/"
                    "api_base/peer endpoint), secrets & secret-bindings "
                    "(api_key/auth/SecretRef/secret headers), the model_list "
                    "registry, and security controls are BASE-only -- promote "
                    "this change via git (export it from GET /v1/admin/config/"
                    "promotion/diff); it cannot be applied through the runtime "
                    "overlay"
                ),
            )

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return safe_patch

    return await apply_overlay_mutation(
        section="config",
        op="replace",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
        full_replace=True,
    )


@router.get("/config/schema", dependencies=[_read])
async def get_config_schema() -> dict[str, Any]:
    """Return JSON Schema for the ForgeConfig model."""
    schema: dict[str, Any] = ForgeConfig.model_json_schema()
    return schema


# --- Tools endpoints (manual_tools; name-keyed CRUD via the overlay) ---


@router.get(
    "/tools",
    response_model=list[AdminToolInfo],
    dependencies=[_read],
)
async def list_tools() -> list[AdminToolInfo]:
    """List all registered tools with metadata."""
    if _agent is None:
        return []

    registry = getattr(_agent, "_registry", None)
    if registry is None:
        return []

    known_values = _resolved_secret_values(_read_base_raw_or_empty())
    tools: list[AdminToolInfo] = []
    for tool in registry.tools:
        tools.append(
            AdminToolInfo(
                name=tool.name,
                description=redaction.scrub_text(
                    getattr(tool, "description", "") or "", known_values
                ),
                source=_classify_tool_source(tool.name),
            )
        )
    return tools


@router.get(
    "/tools/{name}",
    response_model=AdminToolInfo,
    responses={404: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def get_tool(name: str) -> AdminToolInfo:
    if _config is None:
        raise HTTPException(status_code=404, detail="No config loaded")
    tool = next((t for t in _config.tools.manual_tools if t.name == name), None)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    known_values = _resolved_secret_values(_read_base_raw_or_empty())
    return AdminToolInfo(
        name=tool.name,
        description=redaction.scrub_text(tool.description, known_values),
        source="configured",
    )


@router.post(
    "/tools",
    response_model=AdminConfigUpdateResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def create_tool(
    entity: dict[str, Any] = _entity_body,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    name = entity.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="tool 'name' is required")
    if _config is not None and any(t.name == name for t in _config.tools.manual_tools):
        raise HTTPException(status_code=409, detail=f"Tool '{name}' already exists")

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"tools": {"manual_tools": [entity]}}

    return await apply_overlay_mutation(
        section="tools",
        op="create",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.patch(
    "/tools/{name}",
    response_model=AdminConfigUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def patch_tool(
    name: str,
    entity: dict[str, Any] = _entity_body,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    if _config is None or not any(t.name == name for t in _config.tools.manual_tools):
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")

    patched = {**entity, "name": name}

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"tools": {"manual_tools": [patched]}}

    return await apply_overlay_mutation(
        section="tools",
        op="update",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.delete(
    "/tools/{name}",
    response_model=AdminConfigUpdateResponse,
    responses={
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def delete_tool(
    name: str,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    if _config is None or not any(t.name == name for t in _config.tools.manual_tools):
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"tools": {"manual_tools": [{"__deleted__": name}]}}

    return await apply_overlay_mutation(
        section="tools",
        op="delete",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.post(
    "/tools/preview",
    response_model=AdminToolPreviewResponse,
    responses={400: {"model": ErrorResponse}},
    dependencies=[_write_principal],
)
async def preview_tools(request: AdminToolPreviewRequest) -> AdminToolPreviewResponse:
    """Dry-run: parse an OpenAPI spec and return the tool list without registering.

    ADR-0006: this is a live SSRF-read primitive -- a caller-supplied ``source``
    is fetched by ``_fetch_remote_spec``. It therefore (a) requires
    ``config:write`` (not merely ``config:read`` -- fetching an attacker-chosen
    URL is a mutation of the server's egress surface, matching every other
    admin mutation), and (b) constructs the builder with the deployment's egress
    policy so the spec fetch runs through the connect-time SSRF guard. The error
    path returns a generic message: the fetched body is never echoed back to the
    caller (it could contain internal-service content).
    """
    try:
        from forge_agent.builder.openapi import OpenAPIToolBuilder  # noqa: PLC0415
        from forge_config.schema import EgressPolicy, OpenAPISource  # noqa: PLC0415

        source = OpenAPISource.model_validate(request.source)
        egress_policy = _config.security.egress if _config is not None else EgressPolicy()
        builder = OpenAPIToolBuilder(source, egress_policy=egress_policy)
        tools = await builder.build()

        return AdminToolPreviewResponse(
            tools=[
                AdminToolInfo(
                    name=t.name,
                    description=getattr(t, "description", "") or "",
                    source="openapi",
                )
                for t in tools
            ],
            count=len(tools),
        )
    except Exception as e:
        # Do NOT leak the fetched spec body (or any upstream error text that may
        # embed it) to the caller -- log server-side, return a generic 400.
        logger.warning("tools/preview failed for source: %s", e)
        raise HTTPException(status_code=400, detail="failed to preview OpenAPI source") from e


# --- Agents endpoints (agents.agents; name-keyed CRUD via the overlay) ---


@router.get("/agents", dependencies=[_read])
async def list_agents() -> list[dict[str, Any]]:
    if _config is None:
        return []
    known_values = _resolved_secret_values(_read_base_raw_or_empty())
    agents: list[dict[str, Any]] = []
    for a in _config.agents.agents:
        data = a.model_dump(mode="json")
        _redact_secrets(data, known_values)
        agents.append(data)
    return agents


@router.get(
    "/agents/{name}",
    responses={404: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def get_agent(name: str) -> dict[str, Any]:
    if _config is None:
        raise HTTPException(status_code=404, detail="No config loaded")
    agent_def = next((a for a in _config.agents.agents if a.name == name), None)
    if agent_def is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    data = agent_def.model_dump(mode="json")
    _redact_secrets(data, _resolved_secret_values(_read_base_raw_or_empty()))
    return data


@router.post(
    "/agents",
    response_model=AdminConfigUpdateResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def create_agent(
    entity: dict[str, Any] = _entity_body,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    name = entity.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="agent 'name' is required")
    if _config is not None and any(a.name == name for a in _config.agents.agents):
        raise HTTPException(status_code=409, detail=f"Agent '{name}' already exists")

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"agents": {"agents": [entity]}}

    return await apply_overlay_mutation(
        section="agents",
        op="create",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.patch(
    "/agents/{name}",
    response_model=AdminConfigUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def patch_agent(
    name: str,
    entity: dict[str, Any] = _entity_body,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    if _config is None or not any(a.name == name for a in _config.agents.agents):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    patched = {**entity, "name": name}

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"agents": {"agents": [patched]}}

    return await apply_overlay_mutation(
        section="agents",
        op="update",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.delete(
    "/agents/{name}",
    response_model=AdminConfigUpdateResponse,
    responses={
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def delete_agent(
    name: str,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    if _config is None or not any(a.name == name for a in _config.agents.agents):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"agents": {"agents": [{"__deleted__": name}]}}

    return await apply_overlay_mutation(
        section="agents",
        op="delete",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


# --- Sessions endpoints ---


@router.get(
    "/sessions",
    response_model=list[AdminSessionResponse],
    dependencies=[_read],
)
async def list_sessions() -> list[AdminSessionResponse]:
    """List active agent sessions.

    Reads through ``ForgeAgent.context`` (ADR-0003 WS-7's async
    ``ConversationStore`` abstraction) rather than any backend-specific
    internals, so this works identically whether the agent is wired to
    the in-memory default store or a ``RedisConversationStore``.
    """
    if _agent is None:
        return []

    store = getattr(_agent, "context", None)
    if store is None:
        return []

    sessions: list[AdminSessionResponse] = []
    for session_id in await store.session_ids():
        sessions.append(
            AdminSessionResponse(
                session_id=session_id,
                message_count=await store.message_count(session_id),
                # Per-session persona attribution isn't tracked by
                # ConversationStore -- reserved for a future enhancement.
                agent=None,
            )
        )
    return sessions


@router.delete(
    "/sessions/{session_id}",
    responses={404: {"model": ErrorResponse}},
    dependencies=[Depends(_write_dep)],
)
async def delete_session(session_id: str) -> dict[str, str]:
    """Terminate a session."""
    if _agent is None:
        raise HTTPException(status_code=404, detail="No agent available")

    store = getattr(_agent, "context", None)
    if store is None:
        raise HTTPException(status_code=404, detail="No session context available")

    if session_id not in await store.session_ids():
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    await store.clear_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# --- Activity endpoint ---


@router.get(
    "/activity",
    response_model=AdminActivityResponse,
    dependencies=[_read],
)
async def get_activity(
    limit: int = Query(default=_ACTIVITY_DEFAULT_LIMIT, ge=1, le=_ACTIVITY_MAX_LIMIT),
) -> AdminActivityResponse:
    """Return the most recent tool invocations, newest first.

    Backed by ``forge_gateway.activity.recent_activity`` -- an in-memory,
    bounded log recorded at the same seams as the ``forge_tool_invocations_total``
    Prometheus counter, but retaining per-call detail (arguments, outcome,
    session) rather than just a count.
    """
    records = recent_activity.snapshot(limit)
    return AdminActivityResponse(
        activity=[
            AdminActivityRecord(
                tool=record.tool,
                arguments=record.arguments,
                ok=record.ok,
                error=record.error,
                timestamp=record.timestamp,
                session_id=record.session_id,
                interface=record.interface,
            )
            for record in records
        ]
    )


# --- Peers endpoints ---


@router.get(
    "/peers",
    response_model=list[AdminPeerResponse],
    dependencies=[_read],
)
async def list_peers() -> list[AdminPeerResponse]:
    """List peers with connection status.

    RESIDUAL B: applies the same read-path redaction as the other admin GETs
    (key-name + resolved-value substring scan) before serializing, so a
    secret-shaped or secret-valued string in e.g. ``capabilities`` is blanked
    and no future secret-bearing peer field silently leaks through this route."""
    if _config is None:
        return []

    known_values = _resolved_secret_values(_read_base_raw_or_empty())
    peers: list[AdminPeerResponse] = []
    for peer in _config.agents.peers:
        data: dict[str, Any] = {
            "name": peer.name,
            "endpoint": peer.endpoint,
            "trust_level": peer.trust_level.value,
            "capabilities": list(peer.capabilities),
            "spiffe_id": peer.spiffe_id,
        }
        _redact_secrets(data, known_values)
        peers.append(AdminPeerResponse(status=AdminPeerStatus.UNKNOWN, **data))
    return peers


@router.post(
    "/peers",
    response_model=AdminPeerResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def create_peer(
    request: AdminPeerCreateRequest,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminPeerResponse:
    """Peer creation is BASE-ONLY (Phase-1 field-level split).

    A new peer REQUIRES an ``endpoint`` -- an outbound DESTINATION that
    ships task payloads and trusts the response -- so defining one is a
    git-promoted, human-reviewed change, not a runtime overlay edit
    (``OverlayPeerAgent`` cannot even carry ``endpoint``). Return a clear
    promote-via-git 400 so the UI routes the operator to the export flow.
    Editing an EXISTING base-defined peer's ``capabilities``/
    ``trust_level``/``spiffe_id`` remains runtime-safe via ``PATCH
    /v1/admin/peers/{name}``.
    """
    if _config is None:
        raise HTTPException(status_code=500, detail="No config loaded")
    raise HTTPException(
        status_code=400,
        detail=(
            f"creating peer '{request.name}' is base-only: a peer carries an "
            "endpoint (an outbound destination), so it must be added to BASE and "
            "promoted via git (export it from GET /v1/admin/config/promotion/diff); "
            "it cannot be created through the runtime overlay. Editing an existing "
            "peer's capabilities/trust_level/spiffe_id is still allowed via PATCH."
        ),
    )


@router.patch(
    "/peers/{name}",
    response_model=AdminConfigUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def patch_peer(
    name: str,
    entity: dict[str, Any] = _entity_body,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    if _config is None or not any(p.name == name for p in _config.agents.peers):
        raise HTTPException(status_code=404, detail=f"Peer '{name}' not found")

    endpoint = entity.get("endpoint")
    if endpoint is not None and not validate_peer_endpoint(endpoint):
        raise HTTPException(
            status_code=400, detail="Peer endpoint targets a private or internal network"
        )

    patched = {**entity, "name": name}

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"agents": {"peers": [patched]}}

    return await apply_overlay_mutation(
        section="agents",
        op="update",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.delete(
    "/peers/{name}",
    response_model=AdminConfigUpdateResponse,
    responses={
        404: {"model": ErrorResponse},
        405: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        507: {"model": ErrorResponse},
    },
)
async def delete_peer(
    name: str,
    principal: Principal = _write_principal,
    if_match: int | None = _if_match_header,
) -> AdminConfigUpdateResponse:
    if _config is None or not any(p.name == name for p in _config.agents.peers):
        raise HTTPException(status_code=404, detail=f"Peer '{name}' not found")

    def _patch(_current: dict[str, Any]) -> dict[str, Any]:
        return {"agents": {"peers": [{"__deleted__": name}]}}

    return await apply_overlay_mutation(
        section="agents",
        op="delete",
        build_patch=_patch,
        principal=principal,
        permission_used="config:write",
        expected_rev=if_match,
    )


@router.post(
    "/peers/{name}/ping",
    response_model=AdminPeerPingResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def ping_peer(name: str) -> AdminPeerPingResponse:
    """Health-check a specific peer.

    Only allows pinging peers that are in the configured peer list,
    and validates that peer endpoints don't target private/internal IPs.
    Measures the round-trip latency of a reachable check (docs/user/
    features/peers.md "Pinging a peer") -- left unset when unreachable.
    """
    if _config is None:
        raise HTTPException(status_code=404, detail="No config loaded")

    peer = next((p for p in _config.agents.peers if p.name == name), None)
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Peer '{name}' not found")

    # SSRF protection: validate the peer endpoint
    if not validate_peer_endpoint(peer.endpoint):
        raise HTTPException(
            status_code=400,
            detail="Peer endpoint targets a private or internal network",
        )

    import httpx  # noqa: PLC0415

    start = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                peer.endpoint.rstrip("/") + "/health/live",
                timeout=5.0,
            )
            resp.raise_for_status()
            latency_ms = (time.monotonic() - start) * 1000
            return AdminPeerPingResponse(
                name=name,
                status="reachable",
                http_status=resp.status_code,
                latency_ms=round(latency_ms, 2),
            )
    except httpx.HTTPError as e:
        return AdminPeerPingResponse(name=name, status="unreachable", error=str(e))


# --- Helpers ---


def _classify_tool_source(name: str) -> str:
    """Heuristic to classify tool origin."""
    if name.startswith("peer_"):
        return "peer"
    return "configured"


# Security-review finding #1 (CRITICAL): the actual redaction logic now
# lives in ``forge_gateway.redaction`` (shared with routes/approvals.py --
# security review finding #2, ADR-0005 SS11) -- these names are kept as
# thin aliases so this module's public surface (and existing tests that
# reach into it, e.g. ``admin._redact_secrets(...)``) are unaffected.
_SENSITIVE_KEY_NAMES = redaction.SENSITIVE_KEY_NAMES
_is_sensitive_key = redaction.is_sensitive_key
_is_secret_ref_shape = redaction.is_secret_ref_shape
_redact_secrets = redaction.redact_secrets
