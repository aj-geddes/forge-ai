"""YAML configuration loading with environment variable overlay.

Also implements the BASE+OVERLAY resolution model (see
``forge_config.overlay`` and ``forge_config.writable_store``):

    effective_config = validate(substitute_env(deep_merge(BASE, OVERLAY)))

:func:`deep_merge` is a pure function -- recursive dict merge, with the
whitelisted editable collections (``tools.openapi_sources``,
``tools.manual_tools``, ``tools.workflows``, ``agents.agents``,
``agents.peers``) merged BY NAME rather than replaced wholesale. A
tombstone list entry ``{"__deleted__": "<name>"}`` deletes that named
entry and survives being re-merged against BASE.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from forge_config.exceptions import ConfigLoadError, ConfigValidationError
from forge_config.overlay import project_overlay_safe
from forge_config.schema import ForgeConfig

_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")

#: Provenance keys stamped onto an overlay document -- never part of the
#: mergeable content itself.
_PROVENANCE_KEYS = frozenset({"_rev", "_base_rev", "_updated_by", "_updated_at"})

#: (top_level_key, nested_key) paths whose list values are merged BY NAME
#: (each item keyed by its "name" field) rather than replaced wholesale.
_NAME_KEYED_LIST_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("tools", "openapi_sources"),
        ("tools", "manual_tools"),
        ("tools", "workflows"),
        ("agents", "agents"),
        ("agents", "peers"),
    }
)

#: The whitelisted editable top-level sections of a full ForgeConfig dict.
_EDITABLE_TOP_KEYS: tuple[str, ...] = ("tools", "agents", "llm")

#: load_effective_config defense-in-depth: even if a raw overlay.yaml on
#: disk somehow carried a base-only key (hand-edited PVC file, a bug in
#: the write path), only these top-level keys are ever taken from the
#: overlay -- everything else always comes from BASE. This is belt-and-
#: suspenders alongside OverlayDocument's write-time schema rejection.
_OVERLAY_ALLOWED_KEYS: frozenset[str] = frozenset({*_EDITABLE_TOP_KEYS, "metadata"})


def env_var_names(value: Any) -> set[str]:
    """Collect every ``${VAR}`` (or ``${VAR:default}``) environment-variable
    name referenced anywhere in *value* (a string, or a nested dict/list of
    them).

    Used by the admin mutation choke point to build a BASE-derived
    allowlist: an overlay edit may only reference an env var that BASE
    already references in its editable sections, so a ``config:write``
    caller can never smuggle a ``${FORGE_SESSION_KEY}`` (or any other
    process secret) into a value that ``_substitute_env_vars`` later
    resolves and ships to an attacker-controlled sink.
    """
    names: set[str] = set()
    if isinstance(value, str):
        for match in _ENV_PATTERN.finditer(value):
            names.add(match.group(1))
    elif isinstance(value, dict):
        for v in value.values():
            names |= env_var_names(v)
    elif isinstance(value, list):
        for item in value:
            names |= env_var_names(item)
    return names


def secret_refs(value: Any) -> set[str]:
    """Collect every secret reference in *value* -- in EITHER encoding -- as
    a canonical ``source:name[/key]`` identifier.

    Two encodings denote the same "read a value the pod holds" capability
    and must be allowlisted together:

    - a literal ``${VAR}`` (or ``${VAR:default}``) string, resolved by
      :func:`_substitute_env_vars`                        -> ``env:VAR``
    - a structured ``SecretRef`` mapping
      ``{"source": "env"|"k8s_secret", "name": ..., "key": ...}``, resolved
      by ``forge_config.secret_resolver``                 -> ``env:<name>`` /
      ``k8s_secret:<name>/<key>``

    Both can be planted at a secret SINK (a tool ``api.url``/header, an
    ``auth.token``, an ``llm.model_list`` ``api_key``/``api_base``) and
    shipped outbound. The admin mutation choke point compares the overlay's
    reference set against BASE's *editable* reference set and rejects any
    newly-introduced reference in EITHER encoding, so a ``config:write``
    caller can never smuggle a process/pod secret (``FORGE_SESSION_KEY`` et
    al.) into a value that is later resolved and exfiltrated.
    """
    refs: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            for match in _ENV_PATTERN.finditer(node):
                refs.add(f"env:{match.group(1)}")
        elif isinstance(node, dict):
            source = node.get("source")
            name = node.get("name")
            if source in ("env", "k8s_secret") and isinstance(name, str):
                if source == "k8s_secret":
                    refs.add(f"k8s_secret:{name}/{node.get('key')}")
                else:
                    refs.add(f"env:{name}")
            for sub in node.values():
                _walk(sub)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(value)
    return refs


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute ${VAR} and ${VAR:default} in string values."""
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default = match.group(2)
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            return match.group(0)  # Leave unresolved

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


def load_raw_config_dict(path: str | Path) -> dict[str, Any]:
    """Read and parse *path* (typically BASE) as a plain YAML mapping,
    with NO env substitution and NO ``ForgeConfig`` validation -- the raw
    dict, for callers (e.g. forge-gateway's admin routes) that need to
    compute :func:`compute_base_rev`, build a promotion diff, or feed
    :func:`deep_merge` directly."""
    return _load_yaml_dict(Path(path))


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    """Read and parse *path* as a YAML mapping. Shared by :func:`load_config`
    and :func:`load_effective_config`."""
    if not path.exists():
        msg = f"Configuration file not found: {path}"
        raise ConfigLoadError(msg)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        msg = f"Cannot read configuration file: {path}"
        raise ConfigLoadError(msg) from e

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        msg = f"Invalid YAML in {path}: {e}"
        raise ConfigLoadError(msg) from e

    if data is None:
        data = {}

    if not isinstance(data, dict):
        msg = f"Configuration root must be a mapping, got {type(data).__name__}"
        raise ConfigLoadError(msg)

    return data


def load_config(
    path: str | Path,
    *,
    env_overlay: bool = True,
) -> ForgeConfig:
    """Load and validate a Forge configuration file.

    Args:
        path: Path to the YAML configuration file.
        env_overlay: Whether to substitute environment variables in values.

    Returns:
        Validated ForgeConfig instance.

    Raises:
        ConfigLoadError: If the file cannot be read or parsed.
        ConfigValidationError: If the config fails Pydantic validation.
    """
    data = _load_yaml_dict(Path(path))

    if env_overlay:
        data = _substitute_env_vars(data)

    try:
        return ForgeConfig.model_validate(data)
    except ValidationError as e:
        msg = f"Configuration validation failed: {e}"
        raise ConfigValidationError(msg) from e


# --- deep_merge: pure BASE+OVERLAY merge function ---


def _tombstone_name(item: Any) -> str | None:
    """If *item* is a tombstone marker ``{"__deleted__": "<name>"}``,
    return the name it deletes; otherwise ``None``."""
    if isinstance(item, dict) and set(item.keys()) == {"__deleted__"}:
        name: str = item["__deleted__"]
        return name
    return None


def _merge_named_list(base_list: list[Any], overlay_list: list[Any]) -> list[Any]:
    """Merge two lists of ``{"name": ..., ...}`` dicts by name.

    Base entries keep their position; overlay entries either update an
    existing name (dict-merged onto the base entry), delete a name (via a
    ``{"__deleted__": "<name>"}`` tombstone), or append a brand-new name.
    Unnamed / non-dict base entries pass through unmodified and are never
    targeted by an overlay entry.
    """
    order: list[str] = []
    by_name: dict[str, Any] = {}
    passthrough: list[Any] = []

    for item in base_list:
        name = item.get("name") if isinstance(item, dict) else None
        if name is None:
            passthrough.append(item)
            continue
        order.append(name)
        by_name[name] = item

    for item in overlay_list:
        deleted = _tombstone_name(item)
        if deleted is not None:
            if deleted in by_name:
                del by_name[deleted]
                order.remove(deleted)
            continue
        name = item.get("name") if isinstance(item, dict) else None
        if name is None:
            passthrough.append(item)
            continue
        if name in by_name:
            by_name[name] = _merge_node(by_name[name], item, ())
        else:
            by_name[name] = item
            order.append(name)

    return [*passthrough, *(by_name[n] for n in order)]


def _merge_node(base_val: Any, overlay_val: Any, path: tuple[str, ...]) -> Any:
    if isinstance(overlay_val, dict) and isinstance(base_val, dict):
        result = dict(base_val)
        for k, v in overlay_val.items():
            if k in base_val:
                result[k] = _merge_node(base_val[k], v, (*path, k))
            else:
                result[k] = v
        return result
    if (
        len(path) == 2
        and path in _NAME_KEYED_LIST_PATHS
        and isinstance(base_val, list)
        and isinstance(overlay_val, list)
    ):
        return _merge_named_list(base_val, overlay_val)
    # Scalars and any other list replace outright -- overlay wins.
    return overlay_val


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Pure function: recursively merge *overlay* onto *base*.

    - Plain dict keys merge recursively.
    - The five whitelisted name-keyed collections
      (``tools.openapi_sources``/``manual_tools``/``workflows``,
      ``agents.agents``/``peers``) merge BY NAME; a
      ``{"__deleted__": "<name>"}`` entry tombstones that name.
    - Everything else (scalars, any other list) is replaced outright by
      the overlay value when present.
    - Provenance keys (``_rev``/``_base_rev``/``_updated_by``/
      ``_updated_at``) on *overlay* are ignored -- they are metadata
      about the overlay, not mergeable content.
    - Deterministic and idempotent: neither input dict is mutated, and
      merging the same overlay twice yields the same result.
    """
    content = {k: v for k, v in overlay.items() if k not in _PROVENANCE_KEYS}
    result: dict[str, Any] = _merge_node(base, content, ())
    return result


def canonicalize(data: Any) -> str:
    """Canonical (sorted-key, tight-separator) JSON serialization, used
    both for hashing (:func:`compute_base_rev`) and for deterministic
    equality comparisons (no-op pruning)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def editable_sections(full_config: dict[str, Any]) -> dict[str, Any]:
    """Extract the whitelisted editable subset (``tools``, ``agents``,
    ``llm``, ``metadata.description``) from a full BASE config dict --
    the same subset an :class:`~forge_config.overlay.OverlayDocument` may
    contain. Used both to compute the canonical ``_base_rev`` hash and to
    prune overlay entries that have become no-ops relative to the current
    BASE (see :func:`load_effective_config`).
    """
    out: dict[str, Any] = {}
    for key in _EDITABLE_TOP_KEYS:
        if key in full_config:
            out[key] = full_config[key]
    metadata = full_config.get("metadata")
    if isinstance(metadata, dict) and "description" in metadata:
        out["metadata"] = {"description": metadata["description"]}
    return out


def compute_base_rev(full_base_config: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical JSON of *full_base_config*'s
    editable sections -- the ``_base_rev`` an overlay is stamped with at
    write time, and what :func:`load_effective_config` recomputes against
    the CURRENT base to decide whether the base has moved since."""
    return hashlib.sha256(canonicalize(editable_sections(full_base_config)).encode()).hexdigest()


def _prune_against_base(
    overlay_content: dict[str, Any], base_editable: dict[str, Any]
) -> dict[str, Any]:
    """Drop any overlay entry that is now a structural no-op because BASE
    already contains it verbatim (e.g. after a human promoted the
    overlay's changes into git and ArgoCD reconciled). Only called when
    the overlay's stamped ``_base_rev`` no longer matches the current
    BASE hash (:func:`load_effective_config`) -- i.e. BASE has moved
    since the overlay was authored, which is exactly the situation a
    promotion produces. This is what returns drift to zero once every
    overlay entry has been promoted.
    """
    pruned: dict[str, Any] = {}
    for key, val in overlay_content.items():
        if key in ("tools", "agents") and isinstance(val, dict):
            base_sub = base_editable.get(key, {})
            new_sub: dict[str, Any] = {}
            for subkey, sublist in val.items():
                if (key, subkey) in _NAME_KEYED_LIST_PATHS and isinstance(sublist, list):
                    base_sublist = base_sub.get(subkey, []) if isinstance(base_sub, dict) else []
                    base_by_name = {
                        item.get("name"): item for item in base_sublist if isinstance(item, dict)
                    }
                    kept = []
                    for item in sublist:
                        deleted = _tombstone_name(item)
                        if deleted is not None:
                            # A delete is a no-op once BASE no longer has
                            # that name either (already reconciled).
                            if deleted in base_by_name:
                                kept.append(item)
                            continue
                        name = item.get("name") if isinstance(item, dict) else None
                        base_entry = base_by_name.get(name) if name is not None else None
                        if (
                            isinstance(item, dict)
                            and isinstance(base_entry, dict)
                            and all(base_entry.get(k) == v for k, v in item.items())
                        ):
                            # Structural SUBSET of BASE -- a no-op override
                            # (e.g. a PATCH that stored only {name, description}
                            # matching BASE). Prune it, or it lingers as
                            # permanent drift and shadows later BASE edits.
                            continue
                        kept.append(item)
                    if kept:
                        new_sub[subkey] = kept
                else:
                    new_sub[subkey] = sublist
            if new_sub:
                pruned[key] = new_sub
        elif key == "llm":
            if base_editable.get("llm") != val:
                pruned[key] = val
        elif key == "metadata":
            base_desc = (base_editable.get("metadata") or {}).get("description")
            if not isinstance(val, dict) or val.get("description") != base_desc:
                pruned[key] = val
        else:
            pruned[key] = val
    return pruned


def prune_noop_overlay(
    overlay_content: dict[str, Any],
    *,
    base_editable: dict[str, Any],
    overlay_base_rev: str | None,
) -> dict[str, Any]:
    """Prune overlay entries that are structural no-ops relative to the
    CURRENT base, when the base has moved since the overlay was authored
    (``overlay_base_rev != sha256(base_editable)``). A ``None``
    *overlay_base_rev* (no stamp yet) or an unchanged base is returned
    unmodified -- there is nothing to prune yet.
    """
    if overlay_base_rev is None:
        return overlay_content
    current_hash = compute_base_rev(base_editable)
    if overlay_base_rev == current_hash:
        return overlay_content
    return _prune_against_base(overlay_content, base_editable)


def compact_overlay(content: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip STRUCTURALLY-EMPTY containers from overlay
    CONTENT, unconditionally -- regardless of whether BASE has moved.

    Complements :func:`prune_noop_overlay`, which only prunes no-op
    entries once BASE has moved past the overlay's stamped ``_base_rev``
    (a promotion). A name-keyed list emptied by deleting every entry it
    ever held -- e.g. creating then deleting a runtime-only agent -- is a
    no-op under :func:`deep_merge`'s by-name merge semantics REGARDLESS of
    whether BASE has moved, so it must never be left sitting in the
    persisted overlay as permanent, un-clearable drift.

    Drops, bottom-up:

    - An empty list at one of the whitelisted by-name merge paths
      (:data:`_NAME_KEYED_LIST_PATHS` -- ``tools.manual_tools``,
      ``tools.openapi_sources``, ``tools.workflows``, ``agents.agents``,
      ``agents.peers``). An empty list there is always a no-op under
      by-name :func:`deep_merge` (there is nothing to merge in), so
      dropping it never changes the effective result. A list containing
      anything -- a real entry OR a tombstone
      ``{"__deleted__": "<name>"}`` (itself a REAL, non-no-op
      instruction) -- is left completely untouched, including its
      contents (never descended into).
    - Any dict that becomes (or already is) empty after its own children
      are compacted -- e.g. ``{"tools": {}}`` collapses to ``{}``. Safe
      because every dict-typed overlay field merges onto a same-shaped
      dict in BASE (see :func:`_merge_node`'s dict branch), so an empty
      overlay dict is always a no-op there too.

    Any OTHER list -- one NOT at a whitelisted by-name path, e.g.
    ``llm.litellm.fallback_models`` -- is left untouched even when empty:
    those replace BASE wholesale under :func:`deep_merge` (``_merge_node``'s
    "everything else... replaces outright" branch), so an empty list there
    is a real "clear this" instruction, not a no-op.
    """

    def _compact(node: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, val in node.items():
            child_path = (*path, key)
            if isinstance(val, list):
                if child_path in _NAME_KEYED_LIST_PATHS and not val:
                    continue
                out[key] = val
            elif isinstance(val, dict):
                compacted = _compact(val, child_path)
                if compacted:
                    out[key] = compacted
            else:
                out[key] = val
        return out

    return _compact(content, ())


def load_effective_config(
    base_path: str | Path,
    overlay_path: str | Path | None = None,
    *,
    env_overlay: bool = True,
) -> ForgeConfig:
    """Resolve ``effective_config = validate(substitute_env(deep_merge(BASE, OVERLAY)))``.

    Args:
        base_path: Path to the BASE ``forge.yaml`` (git/ArgoCD source of truth).
        overlay_path: Path to the overlay YAML document. ``None``, or a
            path that does not exist, means "no overlay yet" -- the
            effective config is exactly BASE.
        env_overlay: Whether to substitute environment variables.

    Returns:
        Validated ``ForgeConfig`` for the merged, effective configuration.

    Raises:
        ConfigLoadError: If BASE cannot be read/parsed.
        ConfigValidationError: If the merged result fails validation.
    """
    base_raw = _load_yaml_dict(Path(base_path))

    overlay_p = Path(overlay_path) if overlay_path is not None else None
    if overlay_p is None or not overlay_p.exists():
        merged = base_raw
    else:
        overlay_raw = _load_yaml_dict(overlay_p)
        overlay_base_rev = overlay_raw.get("_base_rev")
        overlay_content = {
            k: v
            for k, v in overlay_raw.items()
            if k not in _PROVENANCE_KEYS and k in _OVERLAY_ALLOWED_KEYS
        }
        # Field-level defense-in-depth (design doc layer D): even a
        # hand-edited PVC overlay that smuggled a base-only FIELD (a tool
        # url, an llm.litellm.model_list, a peer endpoint) past the write
        # path has it dropped here, so it is never merged into the
        # effective config -- the load path mirrors OverlayDocument's
        # write-time field-scoped rejection.
        overlay_content = project_overlay_safe(overlay_content)
        pruned = prune_noop_overlay(
            overlay_content,
            base_editable=editable_sections(base_raw),
            overlay_base_rev=overlay_base_rev,
        )
        # ALWAYS-SAFE structural compaction (see compact_overlay's
        # docstring): drops any by-name collection reduced to an empty
        # list (e.g. {"agents": {"agents": []}}), regardless of whether
        # BASE has moved. Applied here too -- not just at mutation time in
        # forge_gateway.routes.admin.apply_overlay_mutation -- so the
        # LOADED effective config and the drift computed from the same
        # overlay content (admin.get_config / get_promotion_diff, which
        # also call compact_overlay) always agree.
        compacted = compact_overlay(pruned)
        merged = deep_merge(base_raw, compacted)

    if env_overlay:
        merged = _substitute_env_vars(merged)

    try:
        return ForgeConfig.model_validate(merged)
    except ValidationError as e:
        msg = f"Configuration validation failed: {e}"
        raise ConfigValidationError(msg) from e
