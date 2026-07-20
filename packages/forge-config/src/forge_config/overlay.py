"""``OverlayDocument``: the pydantic schema for the whitelisted, editable
subset of ``forge.yaml`` that may live in the runtime config overlay
(``/app/data/overlay/forge.overlay.yaml``).

SLICE 7 (Phase-2 payoff): tool/openapi DESTINATION fields
(``url``/``base_url``/``endpoint``) are now runtime-editable via the overlay.
The safety moved from "structurally un-representable" to a WRITE-TIME gate in
``forge_gateway.routes.admin._enforce_destination_binding``: an internal
literal is rejected by the SSRF classifier, and a credentialed tool may only
be repointed within its BASE credential binding. SECRETS remain git-only.

VOCABULARY (used verbatim throughout this module and the admin routes).
Two tiers of protection, and they are NOT interchangeable:

* **STRUCTURAL** -- un-representable in a valid ``OverlayDocument``. The
  field-scoped sub-models below simply do not DECLARE the field and every one
  is ``extra="forbid"``, so a payload carrying it is a
  ``pydantic.ValidationError`` at parse time -- exactly the mechanism by which
  :class:`OverlayMetadata` already rejects ``metadata.name``. STRUCTURAL
  covers: SECRETS and secret bindings (``SecretRef`` in any encoding,
  ``api_key``, ``AuthConfig`` token/username/password, secret-bearing
  ``headers``), the ``llm.litellm`` ``model_list``/``endpoint``/``mode``
  registry, ``peers[].endpoint`` (hence peer creation), ``requires_approval``,
  a manual tool's ``method``/``body_template``/``timeout``, and an
  ``openapi_source``'s ``path``/``spec``.
* **GATED** -- representable in the overlay, but policed at WRITE time by
  ``forge_gateway.routes.admin._enforce_destination_binding``. GATED covers
  exactly the tool/openapi DESTINATIONS
  (``tools.manual_tools[].api.url``/``base_url``/``endpoint`` and
  ``tools.openapi_sources[].url``): an internal literal is rejected by the
  SSRF classifier, and a tool carrying a BASE credential may only be
  repointed within that credential's bound host set.

So a runtime edit may move WHERE a base-vetted call goes (within policy) but
never WHICH credential it carries. Everything that IS a secret/secret-binding,
or CREATES a new entity that structurally carries one, remains BASE-ONLY
(git-promoted, human-reviewed). Two crisp asymmetries fall out:

* SELECTION vs DEFINITION -- choosing an existing base-vetted tool/model/peer
  by NAME is inert; repointing where a call goes is GATED (allowed within the
  binding policy); changing which credential it carries is base-only
  (STRUCTURAL).
* NEW-ENTITY asymmetry -- creating a ``peer``/``model_list`` entry is
  STRUCTURALLY base-only because each REQUIRES an endpoint/api_base the
  overlay cannot express (and usually a secret); a runtime-created
  ``manual_tool``/``openapi_source`` can express a destination but never a
  credential, so it is bound only by the GATED SSRF check. Creating an
  ``AgentDef`` or ``Workflow`` is runtime-safe because neither carries any
  url or secret -- they only compose/select base-defined destinations. Hence
  ``AgentDef`` and ``Workflow`` are reused WHOLE, while tools/llm/peers get a
  field-scoped sub-model.

The runtime secret-ref/SSRF/trust guards in ``forge_gateway.routes.admin``
remain as defense-in-depth over the fields that ARE still editable (an agent
``system_prompt`` ``${ref}``, a workflow/tool parameter default, a peer's
still-editable ``trust_level``/``spiffe_id``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from forge_config.schema import AgentDef, ParameterDef, ResponseMapping, Workflow

#: Top-level ``forge.yaml`` keys that may NEVER appear in an overlay
#: document. Enforced structurally (this validator), not just by
#: convention -- see the design doc's "structural base-only guarantee".
BASE_ONLY_KEYS: tuple[str, ...] = (
    "security",
    "oidc",
    "service_tokens",
    "authorization",
    "conversation_store",
)

#: The whitelisted editable top-level sections.
EDITABLE_SECTIONS: tuple[str, ...] = ("tools", "agents", "llm", "metadata")


class OverlayFieldError(ValueError):
    """A STRUCTURALLY base-only FIELD (a secret/secret-binding, the
    model_list registry, a peer endpoint, a security control, or a new-entity
    that structurally carries one) was present in overlay content. Carries a
    ``promote via git`` message and the offending field locations so the admin
    API can return a clear 400 that routes the UI to the export/promotion flow
    rather than failing silently."""

    def __init__(self, message: str, *, locations: list[str] | None = None) -> None:
        super().__init__(message)
        self.locations = locations or []


class Tombstone(BaseModel):
    """A ``{"__deleted__": "<name>"}`` capability-REMOVAL marker. Deleting
    any entity is always runtime-safe (it can only reduce capability, never
    reach a destination or secret), so a tombstone is accepted in every
    name-keyed overlay list alongside the field-scoped entity model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    deleted: str = Field(alias="__deleted__")


# --- Tools: field-scoped, destination/secret-free ---


class OverlayManualToolAPI(BaseModel):
    """The runtime-editable subset of a ``ManualToolAPI`` after SLICE 7.

    DESTINATION fields (``url``/``base_url``/``endpoint``) are now editable
    at runtime -- but the admin mutation choke point
    (``_enforce_destination_binding``) gates every such edit: an internal
    literal is rejected by the SSRF classifier, and a tool that carries a
    BASE credential may only be repointed within its bound host set. SECRETS
    stay git-only: ``extra="forbid"`` still structurally rejects
    ``headers``/``auth`` (SECRET bindings) and
    ``method``/``body_template``/``timeout`` (the pinned request-construction
    surface), so a runtime edit can move WHERE a call goes but never WHICH
    credential it carries."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    response_mapping: ResponseMapping | None = None


class OverlayManualTool(BaseModel):
    """Runtime-editable subset of a ``ManualTool``: identity/selector
    ``name`` plus the inert ``description``/``parameters`` and the
    editable ``api`` destination (``url``/``base_url``/``endpoint``, gated at
    write time by the binding check). Omits ``requires_approval`` (a security
    control) and every secret binding (``headers``/``auth``) -- so an overlay
    may repoint a base-defined tool's destination (within its credential
    binding) but never change its credential or ungate it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    parameters: list[ParameterDef] | None = None
    api: OverlayManualToolAPI | None = None


class OverlayOpenAPISource(BaseModel):
    """Runtime-editable subset of an ``OpenAPISource``: the remote-spec
    DESTINATION ``url`` (gated at write time by the binding check, exactly
    like a manual tool) plus filters/labels over the base-defined spec
    (``route_map``/``prefix``/``namespace``/``include_tags``/
    ``include_operations``). Omits ``path``/``spec`` (the local spec source),
    ``auth`` (SECRET binding), and the ``requires_approval``/
    ``approval_operations`` security controls -- so a runtime edit may
    repoint the source (within its credential binding) or broaden a filter,
    but never change its credential."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None
    route_map: dict[str, str] | None = None
    prefix: str | None = None
    namespace: str | None = None
    include_tags: list[str] | None = None
    include_operations: list[str] | None = None


class OverlayToolsConfig(BaseModel):
    """The editable ``tools`` surface. ``manual_tools``/``openapi_sources``
    use their field-scoped models (no SECRET representable -- STRUCTURAL;
    their destinations ARE representable and GATED at write time);
    ``workflows`` reuse the FULL :class:`~forge_config.schema.Workflow`
    because a workflow carries no url or secret -- it only composes existing
    base-defined tools by name."""

    model_config = ConfigDict(extra="forbid")

    openapi_sources: list[OverlayOpenAPISource | Tombstone] | None = None
    manual_tools: list[OverlayManualTool | Tombstone] | None = None
    workflows: list[Workflow | Tombstone] | None = None


# --- LLM: field-scoped, destination/secret-free ---


class OverlayLiteLLMConfig(BaseModel):
    """Runtime-editable subset of ``LiteLLMConfig``: sampling/retry knobs
    and ``fallback_models`` (a SELECTION of model_name strings that must
    resolve to the BASE ``model_list``). Omits ``mode``/``endpoint`` (the
    proxy DESTINATION and destination-selecting switch) and ``model_list``
    (the registry of destinations+credentials: each entry's
    ``litellm_params`` carries ``api_base`` and ``api_key``) WHOLESALE."""

    model_config = ConfigDict(extra="forbid")

    fallback_models: list[str] | None = None
    timeout: float | None = None
    max_retries: int | None = None


class OverlayLLMConfig(BaseModel):
    """Runtime-editable subset of ``LLMConfig``: ``default_model`` (a model
    alias SELECTION resolved against the BASE ``model_list``),
    ``temperature``/``max_tokens`` (sampling knobs), ``system_prompt``
    (LLM content sent only to the base-defined endpoint), and the
    field-scoped ``litellm``. A model string cannot carry a ``base_url``,
    so selection injects no destination."""

    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None
    litellm: OverlayLiteLLMConfig | None = None


# --- Agents/peers ---


class OverlayPeerAgent(BaseModel):
    """Runtime-editable subset of a ``PeerAgent``: labels/identity on an
    EXISTING base-defined peer (``capabilities``/``trust_level``/
    ``spiffe_id``). Omits ``endpoint`` (the outbound DESTINATION), so peer
    CREATION (``endpoint`` is required on ``PeerAgent``) is impossible and
    only the safe fields of a base-defined peer are editable. The runtime
    SSRF/trust-ceiling/spiffe-domain guard in the admin API stays as
    defense-in-depth over the still-editable ``trust_level``/``spiffe_id``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    capabilities: list[str] | None = None
    trust_level: str | None = None
    spiffe_id: str | None = None


class OverlayAgentsConfig(BaseModel):
    """The editable ``agents`` surface. ``agents`` reuse the FULL
    :class:`~forge_config.schema.AgentDef` (every field is a scalar or a
    name selection with no sink -> full runtime CRUD); ``peers`` use the
    field-scoped model (no ``endpoint`` representable); ``default`` is a
    default-agent NAME selector."""

    model_config = ConfigDict(extra="forbid")

    default: str | None = None
    agents: list[AgentDef | Tombstone] | None = None
    peers: list[OverlayPeerAgent | Tombstone] | None = None


class OverlayMetadata(BaseModel):
    """The only ``metadata`` field the overlay may carry."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None


class OverlayDocument(BaseModel):
    """The on-disk shape of ``forge.overlay.yaml``.

    Carries only the field-scoped editable subset (see the module docstring)
    plus provenance stamps (``_rev``, ``_base_rev``, ``_updated_by``,
    ``_updated_at``). ``extra="forbid"`` at the top level PLUS the explicit
    :meth:`reject_base_only_keys` validator reject any base-only top-level
    key, and the field-scoped sub-models reject any base-only FIELD -- so a
    caller can never construct a valid ``OverlayDocument`` that plants/changes
    a secret, ungates an approval, or defines a peer/model_list entry. That
    is the STRUCTURAL tier: SECRET self-service through the overlay is
    impossible by construction, not merely policy-denied.

    DESTINATIONS are the other tier. Since slice 7 a tool/openapi destination
    (``url``/``base_url``/``endpoint``) IS representable here -- it is GATED
    at write time by
    ``forge_gateway.routes.admin._enforce_destination_binding``, not blocked
    by this schema. Do not read this model as a barrier against repointing:
    removing that write-time gate would remove the only thing standing
    between a ``config:write`` caller and an arbitrary public destination.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tools: OverlayToolsConfig | None = None
    agents: OverlayAgentsConfig | None = None
    llm: OverlayLLMConfig | None = None
    metadata: OverlayMetadata | None = None

    rev: int = Field(default=0, alias="_rev")
    base_rev: str | None = Field(default=None, alias="_base_rev")
    updated_by: str | None = Field(default=None, alias="_updated_by")
    updated_at: datetime | None = Field(default=None, alias="_updated_at")

    @model_validator(mode="before")
    @classmethod
    def reject_base_only_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            present = [k for k in BASE_ONLY_KEYS if k in data]
            if present:
                msg = (
                    f"overlay document may not contain base-only key(s) {present!r}: "
                    "security, oidc, service_tokens, authorization, and "
                    "conversation_store can only ever be edited in BASE (git) -- "
                    "this is a structural guarantee enforced at schema level, not "
                    "a runtime permission check."
                )
                raise ValueError(msg)
        return data

    def to_editable_dict(self) -> dict[str, Any]:
        """Serialize back to the whitelisted overlay shape (with
        ``_``-prefixed provenance keys), suitable for
        ``OverlayStore.write_overlay``."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


#: Human-readable summary of the base-only field classes, appended to every
#: promote-via-git rejection so an operator/UI knows WHY and where to go.
_PROMOTE_HINT = (
    "these are BASE-only (the llm model_list api_base and peer endpoint "
    "destinations; secrets & secret-bindings: api_key/auth/SecretRef/secret "
    "headers; the model_list registry; and security controls like "
    "requires_approval) -- "
    "promote this change via git (export it from the promotion diff at "
    "GET /v1/admin/config/promotion/diff); it cannot be applied through the "
    "runtime overlay"
)


def _error_locations(exc: ValidationError) -> list[str]:
    """Dotted field paths from a ``ValidationError`` (list indices dropped
    to keep the message stable), deduplicated in first-seen order."""
    seen: list[str] = []
    for err in exc.errors():
        parts = [str(p) for p in err["loc"] if not isinstance(p, int)]
        loc = ".".join(parts)
        if loc and loc not in seen:
            seen.append(loc)
    return seen


def validate_overlay_content(content: dict[str, Any]) -> None:
    """Validate merged overlay CONTENT against the field-scoped overlay
    schema (:class:`OverlayDocument`), raising :class:`OverlayFieldError`
    with a clear promote-via-git message if any base-only field is present.

    This is the load-bearing STRUCTURAL check the admin mutation choke point
    runs before persisting -- it turns "a config:write caller planted an
    api_key / added a model_list entry / created a peer endpoint" from a
    silently-applied exfil/SSRF primitive into a 400 that the UI routes to
    the promotion flow.

    It does NOT cover a tool/openapi destination repoint: that is
    overlay-representable since slice 7 and is GATED separately, immediately
    after this call, by
    ``forge_gateway.routes.admin._enforce_destination_binding``.
    """
    try:
        OverlayDocument.model_validate(content)
    except ValidationError as exc:
        locations = _error_locations(exc)
        where = f" at {', '.join(locations)}" if locations else ""
        msg = f"overlay may not set base-only field(s){where}: {_PROMOTE_HINT}"
        raise OverlayFieldError(msg, locations=locations) from exc


# --- PUT /config round-trip projection (base-only tolerance) ---
#
# The granular tools/agents/peers routes send targeted patches, so a
# base-only field in one of them is simply a structural error (400 via
# validate_overlay_content). The wholesale ``PUT /config`` path, however,
# round-trips the WHOLE editable config (which came from a GET and therefore
# always carries base-only fields -- the llm.litellm defaults, a peer's
# endpoint). :func:`split_overlay_editable` projects that payload down to the
# overlay-representable subset AND reports any base-only field whose value
# actually CHANGED, so an honest round-trip is tolerated (unchanged base-only
# fields are dropped) while a real secret-plant / new peer endpoint is
# surfaced as a promote-via-git action. NOTE: a tool/openapi DESTINATION is
# NOT base-only here -- it is overlay-representable, so it flows through into
# the safe patch and is policed by the GATED write-time check
# (``_enforce_destination_binding``) instead.

_MANUAL_TOOL_SAFE: frozenset[str] = frozenset({"name", "description", "parameters", "api"})
_MANUAL_TOOL_API_OVERLAYABLE: frozenset[str] = frozenset(
    {"response_mapping", "url", "base_url", "endpoint"}
)
_OPENAPI_OVERLAYABLE: frozenset[str] = frozenset(
    {"name", "url", "route_map", "prefix", "namespace", "include_tags", "include_operations"}
)
_LLM_SAFE: frozenset[str] = frozenset(
    {"default_model", "temperature", "max_tokens", "system_prompt", "litellm"}
)
_LITELLM_SAFE: frozenset[str] = frozenset({"fallback_models", "timeout", "max_retries"})
_PEER_SAFE: frozenset[str] = frozenset({"name", "capabilities", "trust_level", "spiffe_id"})


def _by_name(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                out[item["name"]] = item
    return out


def _project_entry(entry: Any, safe_keys: frozenset[str], *, api_safe: bool = False) -> Any:
    """Keep only *safe_keys* of a name-keyed entry (tombstones pass through);
    when *api_safe*, also strip the nested ``api`` down to the
    overlay-representable subset :data:`_MANUAL_TOOL_API_OVERLAYABLE`
    (``response_mapping`` plus the three GATED destination fields
    ``url``/``base_url``/``endpoint``)."""
    if not isinstance(entry, dict):
        return entry
    if set(entry.keys()) == {"__deleted__"}:
        return entry
    projected: dict[str, Any] = {k: v for k, v in entry.items() if k in safe_keys}
    if api_safe and isinstance(projected.get("api"), dict):
        api = {k: v for k, v in projected["api"].items() if k in _MANUAL_TOOL_API_OVERLAYABLE}
        if api:
            projected["api"] = api
        else:
            projected.pop("api", None)
    return projected


def _project_list(items: Any, safe_keys: frozenset[str], *, api_safe: bool = False) -> list[Any]:
    if not isinstance(items, list):
        return []
    return [_project_entry(item, safe_keys, api_safe=api_safe) for item in items]


def _diff_entry(
    incoming: dict[str, Any],
    effective: dict[str, Any] | None,
    safe_keys: frozenset[str],
    path: str,
    changed: list[str],
    *,
    api_safe: bool = False,
) -> None:
    """Record base-only field paths in *incoming* that differ from
    *effective* (a missing base entity means every base-only field it
    carries is newly introduced -> a change)."""
    for key, val in incoming.items():
        if key in safe_keys:
            if api_safe and key == "api" and isinstance(val, dict):
                base_api = effective.get("api") if isinstance(effective, dict) else None
                for api_key, api_val in val.items():
                    if api_key in _MANUAL_TOOL_API_OVERLAYABLE:
                        continue
                    base_val = base_api.get(api_key) if isinstance(base_api, dict) else None
                    if base_val != api_val:
                        changed.append(f"{path}.api.{api_key}")
            continue
        base_val = effective.get(key) if isinstance(effective, dict) else None
        if base_val != val:
            changed.append(f"{path}.{key}")


def _split_named_list(
    incoming: Any,
    effective: Any,
    safe_keys: frozenset[str],
    path: str,
    changed: list[str],
    *,
    api_safe: bool = False,
) -> None:
    effective_by_name = _by_name(effective)
    if not isinstance(incoming, list):
        return
    for entry in incoming:
        if not isinstance(entry, dict) or set(entry.keys()) == {"__deleted__"}:
            continue
        name = entry.get("name")
        base_entry = effective_by_name.get(name) if isinstance(name, str) else None
        _diff_entry(entry, base_entry, safe_keys, f"{path}[{name}]", changed, api_safe=api_safe)


def project_overlay_safe(incoming: dict[str, Any]) -> dict[str, Any]:
    """Project an editable-sections (or overlay-content) dict down to only
    the fields :class:`OverlayDocument` can carry -- dropping every
    STRUCTURALLY base-only secret/registry/security-control field. Tool and
    openapi DESTINATIONS are carried through (they are overlay-representable
    since slice 7) and policed instead by the write-time GATE.

    Used both by the wholesale ``PUT /config`` split below and by the load
    path (``forge_config.loader.load_effective_config``) as belt-and-braces:
    even a hand-edited PVC overlay that smuggled a base-only field onto disk
    has that field pruned at load, so it is never merged into the effective
    config.
    """
    safe: dict[str, Any] = {}

    tools = incoming.get("tools")
    if isinstance(tools, dict):
        safe_tools: dict[str, Any] = {}
        if "manual_tools" in tools:
            safe_tools["manual_tools"] = _project_list(
                tools["manual_tools"], _MANUAL_TOOL_SAFE, api_safe=True
            )
        if "openapi_sources" in tools:
            safe_tools["openapi_sources"] = _project_list(
                tools["openapi_sources"], _OPENAPI_OVERLAYABLE
            )
        if "workflows" in tools:
            safe_tools["workflows"] = tools["workflows"]
        if safe_tools:
            safe["tools"] = safe_tools

    llm = incoming.get("llm")
    if isinstance(llm, dict):
        safe_llm = {k: v for k, v in llm.items() if k in _LLM_SAFE}
        if isinstance(safe_llm.get("litellm"), dict):
            safe_litellm = {k: v for k, v in safe_llm["litellm"].items() if k in _LITELLM_SAFE}
            if safe_litellm:
                safe_llm["litellm"] = safe_litellm
            else:
                safe_llm.pop("litellm", None)
        if safe_llm:
            safe["llm"] = safe_llm

    agents = incoming.get("agents")
    if isinstance(agents, dict):
        safe_agents: dict[str, Any] = {}
        if "default" in agents:
            safe_agents["default"] = agents["default"]
        if "agents" in agents:
            safe_agents["agents"] = agents["agents"]
        if "peers" in agents:
            safe_agents["peers"] = _project_list(agents["peers"], _PEER_SAFE)
        if safe_agents:
            safe["agents"] = safe_agents

    metadata = incoming.get("metadata")
    if isinstance(metadata, dict) and "description" in metadata:
        safe["metadata"] = {"description": metadata["description"]}

    return safe


def split_overlay_editable(
    incoming: dict[str, Any],
    *,
    incoming_for_diff: dict[str, Any],
    effective_for_diff: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Split an incoming editable-sections dict for the wholesale
    ``PUT /config`` path.

    Returns ``(safe_patch, changed_base_only_paths)``:

    * ``safe_patch`` -- *incoming* projected down to only the overlay-safe
      fields (the exact subset :class:`OverlayDocument` can carry). Built
      from the raw *incoming* so real (unredacted) safe values persist.
    * ``changed_base_only_paths`` -- base-only field paths whose value in
      *incoming_for_diff* differs from *effective_for_diff*. The caller
      passes REDACTED copies for both so an honest round-trip of a redacted
      secret is not misread as a change; a non-empty list means the caller
      must promote via git.
    """
    safe = project_overlay_safe(incoming)
    changed: list[str] = []

    tools = incoming.get("tools")
    if isinstance(tools, dict):
        tools_diff = incoming_for_diff.get("tools", {})
        eff_tools = effective_for_diff.get("tools", {})
        if isinstance(tools_diff, dict):
            _split_named_list(
                tools_diff.get("manual_tools"),
                eff_tools.get("manual_tools") if isinstance(eff_tools, dict) else None,
                _MANUAL_TOOL_SAFE,
                "tools.manual_tools",
                changed,
                api_safe=True,
            )
            _split_named_list(
                tools_diff.get("openapi_sources"),
                eff_tools.get("openapi_sources") if isinstance(eff_tools, dict) else None,
                _OPENAPI_OVERLAYABLE,
                "tools.openapi_sources",
                changed,
            )

    llm = incoming.get("llm")
    if isinstance(llm, dict):
        llm_diff = incoming_for_diff.get("llm", {})
        eff_llm = effective_for_diff.get("llm", {})
        if isinstance(llm_diff, dict):
            for key, val in llm_diff.items():
                if key in _LLM_SAFE and key != "litellm":
                    continue
                if key == "litellm" and isinstance(val, dict):
                    base_litellm = eff_llm.get("litellm") if isinstance(eff_llm, dict) else None
                    for lk, lv in val.items():
                        if lk in _LITELLM_SAFE:
                            continue
                        base_lv = base_litellm.get(lk) if isinstance(base_litellm, dict) else None
                        if base_lv != lv:
                            changed.append(f"llm.litellm.{lk}")
                    continue
                base_v = eff_llm.get(key) if isinstance(eff_llm, dict) else None
                if base_v != val:
                    changed.append(f"llm.{key}")

    agents = incoming.get("agents")
    if isinstance(agents, dict):
        agents_diff = incoming_for_diff.get("agents", {})
        eff_agents = effective_for_diff.get("agents", {})
        if isinstance(agents_diff, dict):
            _split_named_list(
                agents_diff.get("peers"),
                eff_agents.get("peers") if isinstance(eff_agents, dict) else None,
                _PEER_SAFE,
                "agents.peers",
                changed,
            )

    return safe, changed
