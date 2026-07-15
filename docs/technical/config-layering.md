---
layout: page
title: Config Layering
description: The BASE+OVERLAY runtime config-editing model -- what is safe to edit through the admin API, what is structurally git-only, and how the split is enforced.
parent: Technical
nav_order: 8
---

# Config Layering

Forge AI resolves one **effective config** from two layers at every read:

```
effective_config = validate(substitute_env(deep_merge(BASE, OVERLAY)))
```

- **BASE** -- `forge.yaml`, mounted from the Helm-managed ConfigMap at `/app/config/forge.yaml`. Git/ArgoCD is the source of truth; the pod treats this file as read-only.
- **OVERLAY** -- `forge.overlay.yaml`, a whitelisted diff persisted on the **same RWO Longhorn PVC as the user-token store** (`/app/data`). It exists only when `persistence.enabled` is true; without a durable volume there is nowhere safe to write a runtime edit, so `FORGE_CONFIG_OVERLAY_PATH` is unset and the effective config is exactly BASE.

The admin API (`/v1/admin/config`, `/v1/admin/tools`, `/v1/admin/agents`, `/v1/admin/peers`) never writes BASE. Every mutation is written to the overlay, merged back in on the next read, and remains subject to a structural editable/base-only split described below.

**Source:** `packages/forge-config/src/forge_config/loader.py` (`load_effective_config`, `deep_merge`)

## Deployment wiring

```yaml
- name: FORGE_CONFIG_SEED_PATH
  value: /app/config/forge.yaml
- name: FORGE_CONFIG_OVERLAY_PATH
  value: /app/data/overlay/forge.overlay.yaml   # only set when persistence.enabled
```

`FORGE_CONFIG_SEED_PATH` and `FORGE_CONFIG_PATH` point at the same ConfigMap-mounted file; `FORGE_CONFIG_OVERLAY_PATH` is gated on `persistence.enabled` in `values.yaml`. Persistence also forces `agent.replicaCount: 1` and `autoscaling.enabled: false` at Helm render time (the chart `fail`s otherwise) -- the overlay, like the user-token store, is a single-writer JSON/YAML file on an RWO volume; a second concurrent writer would corrupt it with last-writer-wins overwrites.

**Source:** `deploy/helm/forge/templates/deployment.yaml` (env block and the `persistence.enabled`/single-writer `fail` guard at the top of the template)

## Merge semantics

`deep_merge(base, overlay)` is a pure function:

- Plain dict keys merge recursively.
- Five name-keyed collections merge **by name** rather than replacing wholesale: `tools.openapi_sources`, `tools.manual_tools`, `tools.workflows`, `agents.agents`, `agents.peers`. An overlay entry updates the base entry with the same `name`, or appends a new one.
- A tombstone entry `{"__deleted__": "<name>"}` removes that named entry and survives being re-merged against BASE on every subsequent load.
- Every other scalar or list is replaced outright by the overlay value when present.
- Provenance keys stamped on the overlay document (`_rev`, `_base_rev`, `_updated_by`, `_updated_at`) are metadata, never mergeable content.

At load time, only `tools`, `agents`, `llm`, and `metadata` are ever taken from the overlay (`_OVERLAY_ALLOWED_KEYS`) -- belt-and-suspenders in case a hand-edited PVC file smuggled something else onto disk. The merged dict then passes through `_substitute_env_vars` (resolving `${VAR}`/`${VAR:default}`) and `ForgeConfig.model_validate` before it becomes the effective config.

**Source:** `packages/forge-config/src/forge_config/loader.py` (`_merge_node`, `_merge_named_list`, `load_effective_config`)

## What's runtime-editable vs. git-only

The split is enforced **structurally**, not by a runtime permission check: `forge_config.overlay.OverlayDocument` and its field-scoped sub-models (`OverlayManualTool`, `OverlayOpenAPISource`, `OverlayLLMConfig`, `OverlayLiteLLMConfig`, `OverlayPeerAgent`) all set `model_config = ConfigDict(extra="forbid")` and simply do not declare the base-only fields. A payload carrying one of those fields fails Pydantic validation at parse time -- it can never be represented as a valid overlay document, so there is no code path where it could be silently applied.

| Section | Runtime-editable via overlay | BASE-only (git-promoted) |
|---|---|---|
| `agents.agents` | Full CRUD -- reuses `AgentDef` whole (name, model, system_prompt, tools, description, etc. carry no URL or secret) | -- |
| `agents.peers` | `capabilities`, `trust_level` (up to `low` without `infrastructure:write`), `spiffe_id` (must match the configured trust domain) on an *existing* peer | `endpoint` (the outbound destination) -- so creating a peer is impossible via the overlay |
| `tools.manual_tools` | `description`, `parameters`, `api.response_mapping` | `api.url`/`base_url`/`endpoint`/`headers`/`auth`/`method`/`body_template`, `requires_approval` -- so creating a tool is impossible (it needs a `url`) |
| `tools.openapi_sources` | `route_map`, `prefix`, `namespace`, `include_tags`, `include_operations` (filters over an already-defined spec) | `url`/`path`/`spec`, `auth`, `requires_approval`/`approval_operations` |
| `tools.workflows` | Full CRUD -- reuses `Workflow` whole (composes existing base-defined tools by name; carries no URL or secret) | -- |
| `llm` | `default_model` (a selection against BASE's `model_list`), `temperature`, `max_tokens`, `system_prompt`, `litellm.fallback_models`/`timeout`/`max_retries` | `litellm.model_list` (the registry of destinations + `api_key`/`api_base`), `litellm.mode`, `litellm.endpoint` |
| `metadata` | `description` | everything else |
| top-level | -- | `security`, `oidc`, `service_tokens`, `authorization`, `conversation_store` -- rejected both by `extra="forbid"` and an explicit `reject_base_only_keys` validator |

Two asymmetries fall out of this: **selection vs. definition** (choosing an existing base-vetted tool/model/peer by name is safe; defining or repointing where a call goes, or which credential it carries, is base-only), and the **new-entity asymmetry** (creating a `manual_tool`/`openapi_source`/`peer`/`model_list` entry is base-only because each requires a url/endpoint/api_base, whereas creating an `AgentDef` or `Workflow` is runtime-safe because neither carries any destination or secret).

**Source:** `packages/forge-config/src/forge_config/overlay.py`

## Enforcement layers

`apply_overlay_mutation` in `forge_gateway.routes.admin` is the single choke point every mutating route funnels through (tool/agent/peer CRUD, `PUT /config`, `POST /config/revert`). Under one `OverlayStore` lock acquisition it:

1. Rejects the whole section with `403` up front if it names a top-level base-only key (`_BASE_ONLY_TOP_LEVEL_SECTIONS`).
2. Restores redacted `SecretRef` placeholders in the incoming patch by structural JSON-path lookup against BASE (so a UI round-trip of a redacted `GET` never clobbers the real value), then rejects the mutation with `400` if any redaction sentinel could not be restored.
3. Runs `validate_overlay_content` (the `OverlayDocument` parse) -- a `400` with a "promote via git" message and the offending field paths if any base-only field is present.
4. Runs the blanket secret-ref guard (below) and an SSRF/trust guard over any `agents.peers` entry (`validate_peer_endpoint`, a `trust_level` ceiling of `low` without `infrastructure:write`, and a `spiffe_id` trust-domain check).
5. Write-time-scrubs any literal (already-resolved) secret value pasted directly instead of a reference.
6. Deep-merges the proposed overlay onto BASE, validates the **whole** resulting `ForgeConfig`, and runs the anti-escalation guard (`security.check_no_config_escalation`) before anything is persisted.
7. Atomically persists the overlay (`OverlayStore.write_overlay`) and appends a hash-chained audit entry. A durable-write failure returns `507` with `persisted=false` -- never swallowed.

`load_effective_config` re-runs `project_overlay_safe` on overlay content before every merge as independent defense-in-depth: even a hand-edited `overlay.yaml` on the PVC that smuggled a base-only field past the write path has it dropped before it ever reaches a running `ForgeConfig`.

The wholesale `PUT /v1/admin/config` path round-trips a full config-shaped payload (which, coming from a prior `GET`, always carries base-only fields like a tool's `url`). `split_overlay_editable` projects it down to the overlay-safe subset and separately detects any base-only field whose *value actually changed* (comparing redacted copies of both sides) -- an honest round-trip is tolerated, a real repoint/secret-plant is a `400` routing the caller to git.

**Source:** `packages/forge-gateway/src/forge_gateway/routes/admin.py` (`apply_overlay_mutation`, `_validate_overlay_peers`)

## The blanket secret-ref guard

After the field-level split, no overlay-editable field is a legitimate secret sink -- the only fields still editable are inert free text (`AgentDef.system_prompt`/`description`, a `ParameterDef.default`, a `WorkflowStep.params` value, `metadata.description`). `_reject_secret_refs_in_overlay` therefore rejects the mutation outright (`400`) if `_find_secret_ref_paths` finds **any** secret reference anywhere in the overlay content, in either encoding:

- a literal `${VAR}` or `${VAR:default}` string (matched by the same `_ENV_PATTERN` the loader uses to substitute env vars, including one embedded inside a larger string), or
- a structured `SecretRef` mapping (`{"source": "env" | "k8s_secret", "name": ..., "key": ...}`).

Secrets are therefore never editable at runtime, full stop -- there is no allowlist of "already-referenced" secrets a caller may still write; every reference must be promoted via git. Separately, `_scrub_resolved_secrets` rejects (`400`) any string leaf that is **exactly** an already-resolved BASE secret value (`os.environ.get(...)` for every `env` `SecretRef` reachable from BASE) -- catching a literal paste of the real key rather than a reference to it.

**Source:** `packages/forge-gateway/src/forge_gateway/routes/admin.py` (`_reject_secret_refs_in_overlay`, `_find_secret_ref_paths`, `_scrub_resolved_secrets`)

## Read-path redaction

`forge_gateway.redaction.redact_secrets` is applied to every admin `GET` that can expose config content (`GET /config`, `GET /config/base`, `GET /agents`, `GET /agents/{name}`, tool descriptions via `scrub_text`). It blanks values three ways:

1. **Structurally** -- a resolved `SecretRef` shape (`source`/`name`/`key`) is redacted by shape.
2. **By key name** -- any scalar leaf whose key exactly (case-insensitively) matches `SENSITIVE_KEY_NAMES` (`api_key`, `token`, `password`, `secret`, `authorization`, `client_secret`, `private_key`, ...) is blanked, regardless of type or nesting.
3. **By value** -- when a `known_values` set is supplied (every env-resolved secret value reachable from BASE, `_resolved_secret_values`, values under 8 characters excluded), any string that *contains* one of them is blanked as a substring match. This catches a secret baked into a non-sensitive free-text field, e.g. an agent `system_prompt`.

**Source:** `packages/forge-gateway/src/forge_gateway/redaction.py`

## Export-only git promotion

`GET /v1/admin/config/promotion/diff` builds a unified diff between BASE's editable sections and the overlay-merged result, plus a ready-to-paste PR body. Both sides are built from `load_raw_config_dict` -- the **unsubstituted** raw YAML dict -- so a secret still appears only as `${VAR}` text in the diff, never as its resolved value.

No git write happens anywhere in this pod, and the pod holds zero git credentials: promoting is always a human action -- copy the diff into a commit against `forge.yaml`, push, let ArgoCD reconcile. Once merged and reconciled, the promoted overlay entries become structural no-ops against the new BASE and are pruned automatically on the next load (`prune_noop_overlay`, keyed on whether the overlay's stamped `_base_rev` still matches BASE's current hash) -- `drift_from_git` returns to zero without any further action.

**Source:** `packages/forge-gateway/src/forge_gateway/routes/admin.py` (`get_promotion_diff`)

## Known residuals (non-blocking)

Two gaps are tracked for hardening rather than blocking; both require an attacker to already possess the secret's cleartext value, so neither is a novel disclosure primitive:

- **Write-scrub/read-redaction asymmetry.** `_scrub_resolved_secrets` (write time) rejects a leaf only on **exact equality** with a known secret value; `redact_secrets`'s value-scan (read time, most paths) matches on **substring containment**. A known secret embedded inside a larger free-text string (e.g. `"...the key is sk-abc123..."` in a `system_prompt`) can therefore pass the write-time scrub. `GET /config/promotion/diff` builds its diff text directly from raw editable-section dicts and does not call `redact_secrets` at all, so such a value would surface unredacted there (unlike `GET /config`/`GET /agents`, which would catch it via substring matching).
- **`GET /peers` is unredacted.** `list_peers` returns `AdminPeerResponse` fields (`name`, `endpoint`, `trust_level`, `capabilities`, `spiffe_id`) directly, with no `redact_secrets` pass. No current `PeerAgent` field is secret-shaped or secret-named, but `capabilities` round-trips visibly and the endpoint offers no structural guarantee against a future secret-bearing field being added without updating this route.

**Source:** `packages/forge-gateway/src/forge_gateway/routes/admin.py` (`get_promotion_diff`, `list_peers`)
