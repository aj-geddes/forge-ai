# ADR-0003: Raising the Codebase to the Documented Spec

- Status: Proposed
- Date: 2026-07-13
- Supersedes/relates: ADR-0001 (Dex OIDC Authentication), ADR-0002 (User-Issued API Keys)
- Authoritative source of truth for this ADR: the product **documentation** under `docs/`.
  The owner's directive is to **raise the code to meet the docs**, not edit the docs down —
  except where a doc is factually impossible or describes a mechanism that was deliberately
  removed as a security fix (flagged explicitly in section 6).

---

## 1. Executive Summary

### How far is the code from the documented product?

**Structurally close, substantively far on security and infrastructure.** The "happy path"
of the product — config-driven tool building, LLM routing, the four-tab UI, REST/MCP/A2A
surfaces, health/HPA/PDB, hot-reload — is genuinely delivered. What is largely *undelivered*
is the entire **security identity/authorization story the docs are built around**, and a
meaningful slice of the **production infrastructure** (Redis wiring, half the Helm chart, the
profile matrix, the metrics port).

Across the audit there are **34 confirmed gaps** (13 security, 4 config, 3 tools, 5 infra, 9
UI; the arch-api area produced none). They cluster into six themes:

1. **AgentWeave / SPIFFE / OPA is a look-alike shell (the dominant theme).** `agentweave` is
   declared as a dependency but **never imported anywhere in Forge** (`grep` confirms zero
   imports). `forge-security` ships thin reimplementations of identity, signing, trust, audit
   and rate-limit — but only SSRF protection, secret resolution/redaction, CORS and a
   gateway-local rate limiter are actually wired at runtime. Identity is a `MockIdentityProvider`
   with `CERT_NONE`; OPA/SPIFFE/signing/audit are inert. The real SDK exists as a working
   sibling at `../agentweave/agentweave` (`identity/spiffe.py`, `authz/opa.py`,
   `observability/audit.py`, signing, mTLS transport) — exactly the primitives the docs
   promise — and none of it is used.

2. **Two parallel, unreconciled security implementations.** The docs describe a `SecurityGate`
   pipeline (JWT → trust policy → authz → audit) guarding all agent routes. That class still
   exists but is retired and **never instantiated**. The routes are actually guarded by an
   undocumented **Dex OIDC / Principal resolver** (`forge_gateway.security`, added in
   ADR-0001/0002). The documented pipeline describes dead code; the live guard is undocumented.
   This is the central architectural reconciliation (section 5).

3. **Docs that are now factually wrong because the code intentionally changed** (the security
   hardening in ADR-0001). `security.jwt_secret` (HS256) is not just ignored — a spec-compliant
   config **fails to load**. The `agentweave.enabled=false` "open dev mode" no longer opens
   anything. The admin-API `require_admin_key` constant-time key check no longer exists. These
   were removed **on purpose** and should stay removed; the docs must be corrected (section 6).

4. **Infrastructure over-promises.** Redis is documented as wired session storage/caching but
   **nothing in the codebase ever connects to Redis** (`ConversationContext` is an in-memory
   `defaultdict`). Five documented Helm templates don't exist (`redis-deployment`,
   `servicemonitor`, `ingress`, `litellm-deployment`, `gateway-deployment`); the chart ships a
   different set (HTTPRoute/ExternalSecret/PVC). The three-profile matrix (small/medium/large)
   collapsed to one workload. Metrics are documented on `:9090` (with a ServiceMonitor) but
   served on `:8000/metrics` with **zero application metrics registered**.

5. **UI promises that outrun the backend.** Chat does not stream token-by-token and never shows
   tool arguments/results (both documented). "Add Peer" is deliberately disabled (no backend
   endpoint). The documented API-key login screen was replaced wholesale by Dex OIDC, so
   `getting-started.md` is factually wrong about how a user signs in. The Visual config editor
   omits whole documented field groups.

6. **Tool-builder correctness gaps.** The documented workflow `condition` example
   (`"contact.city is not None"`) **always evaluates false** — only bare `{{var}}` truthiness
   works. `$ref` resolution is absent (breaks the petstore spec the docs cite), remote YAML and
   inline `spec` aren't parseable, `result_path` is dot-notation not JSONPath, and
   `error_path`/`status_field` are inert schema fields.

**Bottom line:** the product *demoable today* is real; the product *as documented* — a
SPIFFE-identified, OPA-authorized, audited, Redis-durable, multi-profile agent platform — is
roughly **half built**, with the security half being the deepest and most infra-dependent lift.

---

## 2. Themes → scale at a glance

| Theme | Confirmed gaps | Dominant effort | Needs runtime infra? |
|---|---|---|---|
| AgentWeave/SPIFFE/OPA/signing/audit security | 7 (security) + config overlap | XL | **Yes** — SPIRE server + agent socket, OPA server |
| SecurityGate ↔ OIDC reconciliation | 3 (conflicts) | M–L | No |
| Doc-wrong / deliberately-removed (rule needed) | 3 doc-wrong + 2 conflicts | S | No |
| Infrastructure (Redis, Helm, profiles, metrics) | 5 | L | **Yes** — Redis, Prometheus Operator, Ingress, LiteLLM |
| UI feature completeness | 9 | M–L | No (except AgentWeave-dependent trust/health checks) |
| Tool-builder correctness | 3 | M | No |

---

## 3. Prioritized, grouped workstreams

Effort scale: S ≈ <1 day, M ≈ 1–3 days, L ≈ 3–8 days, XL ≈ 2+ weeks with infra standup.
**INFRA is called out loudly** — several of these do not "work" from code alone.

### WS-1 — Reconciliation decision + doc corrections  ·  Priority 1  ·  Effort S  ·  Infra: none

The cheapest, highest spec-compliance-per-effort work, and it unblocks everything else by
settling the security-model story (section 5) and correcting docs that describe removed code.

**Build:**
- Adopt the two-plane security model (section 5) as the canonical narrative.
- Rewrite `docs/technical/security.md`: replace the `jwt_secret`/HS256 section with the OIDC
  RS256 + JWKS model (`forge_security.oidc.OIDCTokenVerifier`); replace "Layer 1" admin API-key
  `_validate_key`/constant-time section with the OIDC/service-token model; rewrite the
  "Development Mode" section to describe `auth.mode=dev_insecure` + `FORGE_DEV_INSECURE=1`
  instead of `agentweave.enabled=false`; correct the rate-limit source/pipeline placement to the
  live limiter (or reconcile in WS-2).
- Correct `docs/user/faq.md` (admin-key, dev-mode, agentweave-disable answers) and every
  `docs/developer/api-reference.md` "Authentication: SecurityGate" label to reflect the real
  guard.
- Correct `docs/user/configuration.md` + `docs/technical/data-model.md`: mark `jwt_secret` and
  `security.api_keys` as deprecated, point to OIDC/service-tokens.

**Satisfies:** security gaps `jwt_secret` (conflicts, doc-side), `agentweave.enabled dev mode`
(doc-wrong), admin API-key (doc-wrong), rate-limit source (doc-wrong), api-reference labels
(conflicts, doc-side); config `api_keys` minor.

**Files:** `docs/technical/security.md`, `docs/user/faq.md`, `docs/developer/api-reference.md`,
`docs/user/configuration.md`, `docs/technical/data-model.md`.

> These require an **owner ruling** (section 6) before merging, because they change the contract
> rather than the code. Do NOT restore HS256 `jwt_secret` or the `agentweave.enabled` open toggle
> in code — that reintroduces the ADR-0001 bypass.

### WS-2 — Make the SecurityGate pipeline real atop the verified Principal  ·  Priority 2  ·  Effort M  ·  Infra: none (OPA optional, see WS-4)

Turn the documented four-step pipeline from dead code into a live post-authentication stage that
runs *downstream* of whichever identity source authenticated the caller (OIDC Principal today,
SPIFFE SVID after WS-3).

**Build:**
- Introduce a gateway dependency that, after `get_principal` resolves an identity, runs
  `TrustPolicyEnforcer.evaluate(origin=..., identity=...)` → authz decision → `AuditLogger.log_tool_call`.
- Implement `TrustPolicyEnforcer` behavior: honor `trust_policy` (strict = deny-by-default /
  require an authz decision / deny unknown origins; permissive = allow-by-default) and enforce
  `allowed_origins` glob (fnmatch) **server-side**, not only via CORS.
- Consolidate rate limiting onto a single limiter: either fold
  `forge_security.SlidingWindowRateLimiter` into `TrustPolicyEnforcer` step-2 in the live path,
  or standardize on `forge_gateway.rate_limit` and delete the inert one. Update WS-1 docs to
  match whichever wins.
- Emit audit events for tool calls on `/v1/run`, `/v1/chat`, `/a2a/*`, `/v1/agent/invoke` from
  the live path.

**Satisfies:** security gaps SecurityGate-guards-routes (conflicts), trust policy strict/permissive
(stubbed), audit logging (partial), TrustPolicyEnforcer origin (partial), rate-limit pipeline
placement (doc-wrong, code-side).

**Files:** `packages/forge-gateway/src/forge_gateway/security.py`, `.../middleware/`,
`.../routes/{conversational,programmatic,a2a,persona}.py`, `packages/forge-security/src/forge_security/{trust,audit,rate_limit,middleware}.py`.

### WS-3 — Wire the real AgentWeave SDK (workload identity + signing + audit backend)  ·  Priority 3  ·  Effort XL  ·  **Infra: SPIRE server + SPIRE agent socket (spiffe_endpoint), OPA server**

The dominant theme. Replace the look-alike shell with the sibling SDK on the agent-to-agent plane.

**Build:**
- Add `agentweave` as a real (imported) dependency. Instantiate
  `agentweave.identity.SPIFFEIdentityProvider` from `config.agentweave.spiffe_endpoint` and inject
  it into `ForgeIdentityManager`; keep `MockIdentityProvider` for tests only.
- Route audit through `agentweave.observability.audit` (`FileAuditBackend`) as the WS-2 audit sink.
- Wire `MessageSigner` (backed by the SVID keypair) into the **A2A outbound client** and peer-ping
  path; add **signature verification on inbound A2A** messages.
- Enforce `agentweave.enabled` as the on/off switch for the *workload* plane (separate from the
  human OIDC plane — see section 5); when disabled, skip SPIFFE/signing but keep OIDC.
- Consume `peer.trust_level` (high/medium/low) in the A2A path: gate which capabilities/tools a
  peer of a given trust level may invoke, feeding the trust policy from WS-2.

**Satisfies:** security gaps AgentWeave-framework (partial→delivered), SPIFFE identity (stubbed),
message signing (stubbed); config `agentweave` block (stubbed), `peer.trust_level` (partial); UI
per-peer trust enforcement (stubbed), health-check AgentWeave subsystem (partial).

**Files:** `packages/forge-security/src/forge_security/{identity,signing,audit}.py`,
`packages/forge-agent/src/forge_agent/agent/`, `packages/forge-gateway/.../routes/a2a.py`,
`pyproject.toml` workspace deps.

> **Infra is load-bearing:** SPIFFE identity does not exist without a running SPIRE server and an
> agent socket mounted at `spiffe_endpoint`. Signing/verification works code-only, but *identity*
> and mTLS do not. Stand up SPIRE (in-cluster StatefulSet + agent DaemonSet) before this is more
> than unit-testable.

### WS-4 — OPA authorization provider  ·  Priority 4  ·  Effort L  ·  **Infra: OPA server (opa_endpoint)**

**Build:** Construct `agentweave.authz.OPAProvider` from `config.agentweave.opa_endpoint` and pass
it as the WS-2 pipeline's authz decision point (`SecurityGate.from_config(authz_provider=...)` seam).
OPA sits downstream of *both* identity planes: it authorizes an OIDC Principal or a SPIFFE SVID
uniformly. Ship a starter Rego policy bundle. Decide whether OIDC role→permission (ADR-0002) is a
*fallback* when OPA is absent or is *replaced* by OPA when configured.

**Satisfies:** security gap OPA authorization (missing).
**Files:** `forge_security/middleware.py`, gateway security wiring, new `deploy/opa/policies/`.
**Infra:** an OPA server reachable at `opa_endpoint` (in-cluster Deployment + bundle sidecar).

### WS-5 — Tool-builder correctness  ·  Priority 3  ·  Effort M  ·  Infra: none

Pure code fixes; high value, no infra. Good to run in parallel with WS-1/WS-2.

**Build:**
- Real sandboxed boolean expression evaluator for workflow `condition` (e.g. `simpleeval`) so
  `"contact.city is not None"`, comparisons, `is/is not None`, `and/or/not`, and membership work
  against accumulated workflow context. **Must make the documented example execute.**
- `$ref` resolution in the OpenAPI loader (unblocks the petstore spec the docs cite).
- Remote **YAML** spec parsing (fall back on JSON-decode failure / content-type) and **inline
  `spec`** detection/parse (string that parses as JSON/YAML or starts with `{`/`openapi:`).
- Real JSONPath for `result_path` (e.g. `jsonpath-ng`) supporting indexing/wildcards, OR narrow
  the doc to "dotted object paths only".
- Implement `error_path` and `status_field` in `_apply_response_mapping` (and document them), or
  remove the dead schema fields.

**Satisfies:** tools gaps condition (conflicts), result_path JSONPath (partial),
error_path/status_field (stubbed), remote YAML (minor), inline spec (minor); config `condition`
(conflicts) — same root cause.
**Files:** `packages/forge-agent/src/forge_agent/builder/` (workflow, openapi, manual builders).

### WS-6 — Config wiring gaps  ·  Priority 4  ·  Effort M  ·  Infra: none

**Build:**
- `agents.default`: in `resolve_persona`/route handlers, when no agent is requested, resolve
  `config.agents.default` to its `AgentDef` so its system_prompt/model/tools/max_turns apply.
- `k8s_secret` resolution at runtime: register `K8sSecretResolver` into the resolver the
  gateway/agent actually construct — either have them build
  `forge_security.ForgeCompositeSecretResolver`, or register `K8sSecretResolver` onto the
  `forge_config` `CompositeSecretResolver` conditionally "when running in-cluster".

**Satisfies:** config `agents.default` (minor), `k8s_secret` (minor). (`peer.trust_level` is in WS-3.)
**Files:** `packages/forge-config/src/forge_config/secret_resolver.py`, gateway/agent bootstrap,
`packages/forge-gateway/.../routes/persona.py`.

### WS-7 — Redis-backed session store  ·  Priority 5  ·  Effort L  ·  **Infra: Redis (in-cluster Deployment/PVC or external)**

**Build:** Add an async redis client dependency and a `RedisConversationStore` behind
`ConversationContext`, selected by config; wire session/LiteLLM config to it; keep the in-memory
store as the default/dev fallback. Provides the documented durability-across-restarts and
cross-replica sharing.
**Satisfies:** infra Redis session storage (missing).
**Files:** `packages/forge-agent/.../agent/` (context store), config schema, gateway lifespan.
**Infra:** a Redis instance; the docker-compose Redis already runs but nothing talks to it.

### WS-8 — Helm chart + profiles + metrics completion  ·  Priority 5  ·  Effort L  ·  **Infra: Prometheus Operator, Ingress controller, Redis, LiteLLM**

**Build:**
- Add the missing templates the docs enumerate: `redis-deployment.yaml` (+Service +PVC, four
  modes), `servicemonitor.yaml` (gated on `serviceMonitor.enabled`, interval/scrapeTimeout),
  `ingress.yaml` (gated on `ingress.enabled`, ingressClassName/host/TLS/annotations),
  `litellm-deployment.yaml` (+Service, dedicated mode), `gateway-deployment.yaml` (gated by
  profile, large).
- Restore the profile matrix: add `values.medium.yaml`; gate the gateway split, dedicated LiteLLM,
  Redis PVC, and ServiceMonitor in `values.prod.yaml`.
- Inject a conditional LiteLLM **sidecar** container into `deployment.yaml` (port 4000 when
  `litellm.mode=sidecar`).
- Bind a real **metrics listener on :9090** (e.g. `prometheus_client.start_http_server` or a
  second uvicorn port), register actual application metrics, add the `metrics` Service port. OR,
  if `:8000/metrics` is authoritative, drop `EXPOSE 9090` and correct docs + ServiceMonitor —
  **owner ruling** (section 6).
- Re-add `LOG_LEVEL` configurability (read at startup, `values.dev.yaml` sets DEBUG).

**Satisfies:** infra Helm templates (conflicts), profile matrix (conflicts), metrics :9090
(conflicts), LiteLLM sidecar/dedicated (partial), ServiceMonitor (minor), Ingress (minor),
LOG_LEVEL (minor).
**Files:** `deploy/helm/forge/templates/*`, `deploy/helm/forge/values*.yaml`,
`packages/forge-gateway/.../{app.py,routes/metrics.py}`, `Dockerfile`.
**Infra:** Prometheus Operator (ServiceMonitor CRD), an Ingress controller, Redis (WS-7), a
LiteLLM proxy image.

### WS-9 — UI: chat streaming + tool call detail  ·  Priority 4  ·  Effort L  ·  Infra: none

**Build:**
- Wire `ChatPage` to POST `stream:true` and consume the `text/event-stream` SSE (append chunks,
  stop on `[DONE]`).
- Return structured per-call records (name, arguments, result) from the agent/gateway on the chat
  response; render argument/result blocks in the expandable `ToolCallDetails`.
**Satisfies:** UI streaming (conflicts), tool-call args/results (partial).
**Files:** `packages/forge-ui/src/features/chat/`, `packages/forge-agent/.../agent/`,
`packages/forge-gateway/.../routes/conversational.py`.

### WS-10 — UI: Peers + Visual editor completeness  ·  Priority 5  ·  Effort L  ·  Infra: none

**Build:**
- `POST /v1/admin/peers` (append to `agents.peers`, validate, SSRF-check endpoint, persist +
  hot-reload); enable `AddPeerDialog` to call it.
- Ping: measure duration, return `latency_ms`, render latency on reachable / error string on
  unreachable.
- Visual Editor: add form controls + zod schema + (de)serialize for `model_list`,
  `fallback_models`, `timeout`, `max_retries`; a repeatable **Agent Definitions** editor
  (description/system_prompt/model/tools/max_turns); an **API Keys** subsection bound to
  `security.api_keys`; message-signing + authorization-provider controls in the AgentWeave
  subsection.
- Register discrete component statuses (`llm`, `tool_registry`, `agentweave`) via
  `set_component_status` during lifespan for the dashboard subsystem checks.
**Satisfies:** UI Add Peer (conflicts), ping latency/error (minor), Visual Editor LLM fields
(partial), Agent Definitions (missing), API Keys control (missing), AgentWeave subsection (minor),
health subsystem checks (partial).
**Files:** `packages/forge-gateway/.../routes/{admin,peers,health}.py`,
`packages/forge-ui/src/features/{peers,config,dashboard}/`.

### WS-11 — UI: login-model reconciliation  ·  Priority 6  ·  Effort M  ·  Infra: none — **owner ruling**

`getting-started.md` documents an API-key login that was replaced by Dex OIDC (ADR-0001).
Either (a) restore an API-key sign-in path in `LoginPage` + gateway alongside OIDC, or (b) rule
that OIDC is the product and rewrite `getting-started.md`. Recommendation: **(b)** — the OIDC
login is working and was the owner's Priority #1; do not reintroduce a second credential path.
**Files:** `docs/user/getting-started.md` (option b) or `packages/forge-ui/src/features/login/` +
gateway (option a).

---

## 4. Recommended execution order (spec-compliance per effort)

1. **WS-1** (doc reconciliation, S) — settles the story, corrects wrong docs, unblocks decisions.
   Run first; requires the owner rulings in section 6.
2. **WS-5** (tool-builder, M) and **WS-2** (SecurityGate atop Principal, M) — high value, no infra,
   parallelizable. WS-5 fixes the embarrassing "documented example always false" bug.
3. **WS-9** (chat streaming/tool detail, L) and **WS-6** (config wiring, M) — visible product wins,
   no infra, parallelizable.
4. **WS-10 / WS-11** (UI completeness + login ruling) — no infra.
5. **WS-7 → WS-8** (Redis, then Helm/profiles/metrics) — first infra tranche: Redis, then the
   chart that also provisions LiteLLM/ServiceMonitor/Ingress.
6. **WS-3 → WS-4** (AgentWeave SPIFFE identity/signing/audit, then OPA authz) — **last and largest**,
   because they are XL/L *and* gated on standing up SPIRE and OPA. Do the code (SDK import, signing,
   provider seams) early so it is unit-testable with the mock, but the real behavior lands only when
   the infra exists.

**Rationale:** the largest single theme (AgentWeave) is also the most infra-blocked, so it should
not gate the many cheap, infra-free wins. Front-load the doc fixes and pure-code corrections;
back-load the infra-dependent security plane.

---

## 5. The central architectural reconciliation: Dex OIDC vs AgentWeave/SPIFFE/OPA

**The tension:** the docs specify AgentWeave (SPIFFE identity, message signing, trust domains, OPA
authz) as *the* security model. This session shipped **Dex OIDC** (live, working, delivered the
owner's Priority #1 login) — which the security docs never mention — and *retired* the SecurityGate
pipeline the docs describe.

**They are not competitors; they are two planes of a defense-in-depth model:**

| Plane | Question it answers | Direction | Mechanism | Status |
|---|---|---|---|---|
| **Human / edge auth** | *Which user is this?* | North-south (browser → gateway) | **Dex OIDC** (RS256/JWKS), cookie session, user-issued API keys | **Live (ADR-0001/0002)** |
| **Workload / agent auth** | *Which agent/workload is this?* | East-west (agent ↔ agent, A2A) | **AgentWeave SPIFFE** SVID + mTLS + message signing | **Stubbed (WS-3)** |
| **Authorization** | *Is this identity allowed to do this?* | Both | **OPA** policy over the verified identity (Principal OR SVID) | **Missing (WS-4)** |
| **Trust / audit / rate-limit** | *Origin allowed? logged? throttled?* | Both | `TrustPolicyEnforcer` + audit + limiter, post-auth | **Inert pipeline (WS-2)** |

**Recommendation — complementary layers, neither subsumes the other, keep the working auth:**

1. **Keep Dex OIDC as the human/edge authenticator. Do not rip it out and do not restore HS256
   `jwt_secret`.** It is the owner's delivered Priority #1 and restoring the old HS256/open-dev
   paths reintroduces the ADR-0001 bypass. Restoring it would be a *regression disguised as
   spec-compliance*.

2. **Build AgentWeave/SPIFFE as the workload plane on the A2A surface (WS-3)** — outbound peer
   calls and `/a2a/*` inbound. This is what the docs actually mean by "each agent has a
   cryptographic identity" and "outgoing messages are signed": that is machine-to-machine, and OIDC
   does not cover it. The two planes do not overlap.

3. **Make OPA and the SecurityGate pipeline the shared downstream authorization/trust/audit stage
   (WS-2 + WS-4)**, fed by *whichever* identity authenticated the caller — an OIDC `Principal` for
   human/API-key callers, a SPIFFE `SVID` for agent callers. This resurrects the documented
   four-step pipeline as *real code running after* identity resolution, so the diagram in
   `technical/security.md` becomes accurate rather than describing dead code.

4. **`agentweave.enabled` becomes the on/off switch for the *workload* plane only** (SPIFFE +
   signing + A2A verification), not a global "open the gateway" toggle. The human plane is always
   authenticated via OIDC (except `auth.mode=dev_insecure`). Update the docs accordingly (WS-1).

**What "build to the doc spec" concretely means here:** implement the AgentWeave workload plane and
OPA/trust/audit pipeline the docs describe (WS-2/3/4), position Dex OIDC as the human-auth layer the
docs should now also describe, and correct the specific doc passages that describe *removed* HS256/
open-dev/admin-key mechanisms (WS-1 + section 6) — **without discarding the working OIDC auth.**

---

## 6. Gaps where the DOCUMENTATION is wrong/impossible — owner must rule

These are flagged `doc-wrong` or are `conflicts` whose correct resolution is a **doc edit, not a
code change**, because the code diverged deliberately as an ADR-0001 security fix. Restoring the
documented behavior would reintroduce a vulnerability. **Recommended ruling: fix the docs, keep the
code.**

1. **`agentweave.enabled=false` = open dev mode** (`security.md`, `faq.md`, `technical/security.md`)
   — *doc-wrong.* The toggle no longer opens the gateway; the real switch is
   `auth.mode=dev_insecure` + `FORGE_DEV_INSECURE=1` (an intentional double-gate). **Rule: rewrite
   the docs.**

2. **Admin `require_admin_key` constant-time API-key auth** (`technical/security.md` Layer 1,
   `faq.md`) — *doc-wrong.* The `_validate_key`/`hmac.compare_digest` mechanism and "403 when no
   keys defined" semantics no longer exist; admin routes use the OIDC permission check. **Rule:
   rewrite the docs; mark `api_keys` deprecated.**

3. **Rate-limit source file + pipeline placement** (`technical/security.md`,
   `features/security.md`) — *doc-wrong.* Rate limiting *is* delivered (429, per-identity) but via
   `forge_gateway.rate_limit`, not the documented `forge_security` `SlidingWindowRateLimiter` /
   TrustPolicyEnforcer step-2. **Rule: consolidate onto one limiter (WS-2) and correct the doc's
   source/placement.**

4. **`security.jwt_secret` HS256 verification** (`configuration.md`, `data-model.md`,
   `technical/security.md`) — *conflicts, doc-side fix.* A spec-compliant config with `jwt_secret`
   currently **fails to load**. Restoring HS256 `verify_aud=False` reintroduces the ADR-0001 bypass.
   **Rule: delete the `jwt_secret` rows, document OIDC RS256/JWKS; and (code hygiene) make an
   unknown/legacy `jwt_secret` a warning-and-ignore rather than a hard load failure so old configs
   don't break.**

5. **api-reference "Authentication: SecurityGate" labels** (`developer/api-reference.md`) —
   *conflicts, doc-side fix once WS-2 lands.* If WS-2 re-integrates the pipeline atop the Principal,
   the labels become true; until then they are inaccurate. **Rule: update labels to the real guard.**

**Not doc-wrong (build the code):** everything in WS-2/3/4/5/6/7/8/9/10 — those docs are the
contract and the code is at fault.

---

## 7. Appendix — confirmed gap count by area

- security: 13 · config: 4 · tools: 3 · arch-api: 0 · infra-ops: 5 · features-ui: 9
- **Total confirmed gaps: 34** (plus numerous minor gaps folded into the workstreams above).
