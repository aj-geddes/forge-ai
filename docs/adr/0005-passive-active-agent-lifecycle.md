# ADR-0005: Passive / Active Agent Lifecycle Model (reactive vs. autonomous agents with governance guardrails)

**Date**: 2026-07-13
**Status**: Proposed
**Relates to**: ADR-0001 (Dex OIDC human plane), ADR-0002 (user-issued API keys), ADR-0003 (spec-compliance / personas / conversation store WS-7), ADR-0004 (AgentWeave SPIFFE + OPA workload plane)
**Governing thesis (owner)**: Forge AI is a platform for designing SEVERAL agents *easily*, in a *compliant and secure* manner. An agent may be declared **passive** (reactive) or **active** (autonomous). An autonomous agent that handles *unknown* tasks is only acceptable if governance — least privilege, human approval for irreversible actions, full audit, budgets, and a kill switch — is a first-class part of the model, not a bolt-on. This ADR designs the model only; it changes no schema and writes no runtime code.

**Owner's definitions this ADR designs to (non-negotiable):**
- **PASSIVE agent** = waits on an event, input, or trigger, then responds. Reactive; ~today's behavior.
- **ACTIVE agent** = performs a list of KNOWN tasks, AND handles UNKNOWN tasks that arise while doing the known tasks. Autonomous; proactive.

---

## 1. Context

Today every Forge agent is **passive by construction**. `ForgeAgent` (`packages/forge-agent/src/forge_agent/agent/core.py`) only ever runs when *something calls it*: `run_conversational` (chat/SSE via `routes/conversational.py`), `run_structured` (programmatic via `routes/programmatic.py`, MCP via `routes/mcp.py`, and A2A via `routes/a2a.py::run_a2a_task`). There is no component that decides on its own to do work. A persona (`AgentDef` in `packages/forge-config/src/forge_config/schema.py:806`) carries `name`, `description`, `system_prompt`, `model`, `tools[]` (a tool-name allow-list), and `max_turns` — and nothing about *when* or *how autonomously* it runs.

Three existing mechanisms make an autonomous mode tractable *without inventing parallel infrastructure*:

1. **Per-agent least-privilege tool scoping already works.** `AgentDef.tools` flows into `ForgeAgent._filter_tools` / `_merge_tool_filters`; an agent scoped to a tool subset provably cannot reach out-of-scope tools (verified). This is the single most important safety primitive for autonomy — an agent that handles *unknown* tasks can still only ever touch its *known* tools.
2. **A durable, cross-replica run store already exists.** ADR-0003 WS-7's `ConversationStore` (`packages/forge-agent/src/forge_agent/agent/store.py`) is an async `Protocol` with `InMemoryConversationStore` (default) and `RedisConversationStore` (durable, cross-replica, sliding-window, TTL). Autonomous run/task state can reuse this exact abstraction rather than a new datastore.
3. **A precedent for an in-process background async component already exists.** ADR-0004's `:8443` workload listener (`packages/forge-gateway/src/forge_gateway/workload.py`, started by `app.py::_init_workload_plane`) is a second in-process component: started as a background `asyncio` task in the gateway lifespan, with a graceful `stop()` handle, its own **non-gating** health component, and a hard rule that its failure never affects the human `:8000` plane. An autonomous "active" runtime is the same shape of thing.

Governance primitives also already exist and should be *reused, not re-created*: OIDC/RBAC permissions (`Permission` enum, `require_permission`), the request rate limiter (`security.rate_limit_rpm`), Prometheus metrics (`metrics_registry.py`), the recent-activity feed (`activity.py`, `GET /v1/admin/activity`), PydanticAI turn budgets (`UsageLimits(request_limit=max_turns)`, already wired via `max_turns_override`), and — for the workload plane — the AgentWeave `AuditTrail` and OPA policy engine (ADR-0004).

**What is genuinely new and must be designed:** (a) a `mode` declaration and its companion fields; (b) the autonomous *runtime* (what actually decides to do work and drives the plan→act→observe→handle-unknown→stop loop); (c) **human-approval gates** for irreversible/outward-facing actions; (d) run/task state + budgets + a kill switch; (e) observability for autonomous runs.

---

## 2. Decision Drivers

- **Governance-first / fail-closed.** An autonomous agent is a liability unless bounded. Missing/unreachable approval, an exhausted budget, or an unknown safety classification must **stop** the agent, never let it proceed. This mirrors ADR-0001/0004's "absence of configuration can never mean absence of a control."
- **Backward compatibility is absolute.** Every existing `forge.yaml` (single-agent, no `mode`) must parse and behave *exactly* as today. Default = **passive**. An absent field can never silently make an agent autonomous.
- **Least privilege by inheritance.** Unknown-task handling must be *incapable* of exceeding the persona's tool scope — reuse the existing scoping, do not add an escape hatch.
- **Reuse forge's mechanisms, don't fork infra.** Run state → `ConversationStore`/Redis. Background component → the ADR-0004 lifespan-task pattern. Audit → activity feed + AgentWeave `AuditTrail`. Budgets → PydanticAI `UsageLimits` + LiteLLM usage. Approvals/observability → the admin API + RBAC.
- **"Easy platform" thesis.** Declaring an active agent must be a few config fields, hot-reloadable like every other config — not a new Dockerfile, a new CronJob manifest, or a new deploy. This is a hard constraint on the runtime choice (§5).
- **Additive / never risk the human plane.** Exactly as ADR-0004: the active runtime's health must be a **non-gating** component; a broken supervisor must never flip `/health/ready` to NOT READY or pull the UI/API out of service.

---

## 3. The `mode` declaration and companion fields (config schema — proposed shapes)

All additions are to `packages/forge-config/src/forge_config/schema.py`, all additive and default-safe. **No field below changes the meaning of an existing config.**

### 3.1 The mode enum and the `AgentDef` extension

```python
class AgentMode(str, Enum):
    PASSIVE = "passive"   # reactive; waits on a trigger. DEFAULT.
    ACTIVE = "active"     # autonomous; runs known tasks + handles emergent unknown tasks.

class AgentDef(BaseModel):
    name: str
    description: str = ""
    system_prompt: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)   # UNCHANGED: least-privilege scope
    max_turns: int = 10                               # UNCHANGED: per-task turn budget

    # --- NEW (ADR-0005), all default-safe ---
    mode: AgentMode = AgentMode.PASSIVE               # default preserves today's behavior
    triggers: list[TriggerDef] = Field(default_factory=list)   # passive: how it is invoked
    active: ActiveConfig | None = None                # active: known tasks + governance
```

Validation (a new `model_validator` on `AgentDef`):
- `mode == active` **requires** `active is not None` and `len(active.tasks) >= 1` — an "active" agent with no known tasks is a declaration with nothing to do; reject at load time.
- `mode == passive` with a non-null `active` block is rejected (misconfiguration; the block would be silently ignored).
- `triggers` are meaningful for **both** modes but the schedule/webhook trigger *types* are Phase 2 (§9); a Phase-1 loader accepts them but the runtime only honors `chat`/`api`/`a2a` (which are implicit today).

### 3.2 Passive companion: `TriggerDef`

Formalizes *what may invoke a passive agent*. The first three types describe **already-existing** invocation paths (declared here so they are visible/auditable, not new behavior); the last two are new capabilities (Phase 2).

```python
class TriggerType(str, Enum):
    CHAT = "chat"          # existing: /v1/chat (conversational)
    API = "api"            # existing: /v1/agent (programmatic) + /mcp
    A2A = "a2a"            # existing: /a2a/tasks (human :8000 and workload :8443)
    WEBHOOK = "webhook"    # NEW (Phase 2): inbound HTTP callback
    SCHEDULE = "schedule"  # NEW (Phase 2): cron-driven invocation

class TriggerDef(BaseModel):
    type: TriggerType
    # webhook-only:
    path: str | None = None                  # mount path suffix, e.g. "github-push"
    secret: SecretRef | None = None          # HMAC verification secret (required for webhook)
    allowed_methods: list[HTTPMethod] = Field(default_factory=lambda: [HTTPMethod.POST])
    # schedule-only:
    cron: str | None = None                  # 5-field cron expression
    intent: str | None = None                # what to run when the schedule fires
    params: dict[str, Any] = Field(default_factory=dict)
```

**In scope now vs. later:** `chat`/`api`/`a2a` are already the live invocation surfaces and need no new runtime — declaring them is documentation + a UI affordance. `webhook`/`schedule` are **Phase 2** (they need the same background scheduler the active runtime introduces, plus an inbound webhook router with HMAC verification and the existing SSRF/authz guards). A scheduled *passive* trigger is mechanically identical to a single-task active run with no emergent-task handling, so it reuses the §5 supervisor.

### 3.3 Active companion: `ActiveConfig`, `KnownTask`, budgets, approval policy

```python
class KnownTask(BaseModel):
    name: str                                # stable id (idempotency key namespace)
    intent: str                              # natural-language spec (-> run_structured intent)
    params: dict[str, Any] = Field(default_factory=dict)
    max_turns: int | None = None             # per-task override of AgentDef.max_turns

class BudgetConfig(BaseModel):
    max_steps: int = 25                      # total act->observe iterations across the whole run
    max_emergent_tasks: int = 5              # hard cap on UNKNOWN tasks spawned during a run
    max_tool_calls: int = 100                # total tool invocations across the whole run
    max_tokens: int | None = None            # LLM token budget across the whole run (LiteLLM usage)
    wall_clock_seconds: int = 900            # hard timeout for a single run (15 min default)
    max_concurrent_runs: int = 1             # per-agent run concurrency (default: no overlap)

class ApprovalPolicy(BaseModel):
    # Deny-by-default posture: everything NOT explicitly auto-approvable is treated
    # as reversible only if it is a read/list tool; anything outward-facing needs approval.
    require_approval_tools: list[str] = Field(default_factory=list)  # explicit gate list
    auto_approve_tools: list[str] = Field(default_factory=list)      # explicit allow list
    default_requires_approval: bool = True   # UNLISTED tool with side effects -> gate (fail-safe)
    approval_timeout_seconds: int = 86400    # a parked approval expires (run terminates) after 24h

class ActiveConfig(BaseModel):
    enabled: bool = False                    # per-agent kill switch (default OFF)
    tasks: list[KnownTask] = Field(min_length=1)
    schedule: str | None = None              # optional cron cadence for the whole run; None = manual
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
```

And a global gate on the agents block (the master switch, mirrors ADR-0004's `agentweave.enabled` and ADR-0002's `user_tokens.enabled`):

```python
class AgentsConfig(BaseModel):
    default: str = "assistant"
    agents: list[AgentDef] = ...
    peers: list[PeerAgent] = ...
    active_runtime_enabled: bool = False     # NEW: master switch for ALL active agents (default OFF)
```

**Two-key rule for autonomy:** an agent runs autonomously only when `agents.active_runtime_enabled == true` **AND** that agent's `active.enabled == true`. Either being false = the agent behaves passively (its persona is still callable via chat/api/a2a exactly as today). This makes "turn off ALL autonomy" and "turn off this one agent" both a single, greppable config edit that hot-reloads.

---

## 4. Passive triggers — scope

| Trigger | Status | Runtime needed | Notes |
|---|---|---|---|
| `chat` | Exists | none | `POST /v1/chat` → `run_conversational`. Declaration is documentation + UI badge. |
| `api` | Exists | none | `POST /v1/agent`, `/mcp` → `run_structured`. |
| `a2a` | Exists | none | `POST /a2a/tasks` on `:8000` (OIDC) and `:8443` (SPIFFE mTLS, ADR-0004). |
| `webhook` | **Phase 2** | inbound router + HMAC verify | New `POST /v1/hooks/{path}`; HMAC via `TriggerDef.secret`; reuse `validate_peer_endpoint`-style guards; maps the request body to `run_structured(intent, params)`. |
| `schedule` | **Phase 2** | the §5 supervisor's cron scheduler | Fires `run_structured(intent, params)` on a cadence; a passive schedule is an active run with `max_emergent_tasks = 0`. |

Rationale for the split: `chat`/`api`/`a2a` require zero new runtime and can ship in Phase 0 as pure declaration. `webhook`/`schedule` both require a long-running scheduler component — the same one the active runtime introduces — so they are deferred to after that component exists and is proven, avoiding two parallel scheduling mechanisms.

---

## 5. The active runtime (the crux): how an active agent actually RUNS

### 5.1 Options considered

**Option A — In-process `asyncio` supervisor (a background task in the gateway lifespan). CHOSEN.**
An `ActiveAgentSupervisor` object, built and started in `app.py::lifespan` (exactly where `_init_workload_plane` starts the `:8443` listener), holds a graceful `stop()` handle and a **non-gating** health component. It owns a cron scheduler and a bounded work queue, and drives the autonomy loop by calling the *existing* `ForgeAgent.run_structured(...)` with the persona's tool scope and turn budget. Run/task state persists to the existing `ConversationStore`/Redis.
- **Pros**: reuses the proven ADR-0004 lifespan-background-task pattern and the ADR-0003 Redis run store; hot-reloadable with config (an edit to `active.tasks` re-plans on the next cadence, same as tool hot-reload); no new deploy artifact, image, or manifest per task — directly serves the "easy platform" thesis; shares the live tool registry, secret resolver, LLM router, audit, metrics, and activity feed with the request path, so a tool works identically whether called by a human or autonomously; unit-testable in-process with `TestModel`.
- **Cons**: a long-running component in the request-serving process (must be strictly non-gating and resource-bounded); multi-replica requires a single-runner guard (§5.4) so a scheduled run doesn't fire on every replica.

**Option B — Kubernetes `CronJob`/`Job` per schedule.**
- **Pros**: OS-level isolation; k8s owns retries/backoff and resource limits; a runaway run is bounded by the pod's cgroup.
- **Cons**: **breaks the thesis.** Deployment is ArgoCD GitOps (REQUIRED) — Jobs are declared in git and reconciled, so an operator declaring an active agent *in `forge.yaml`* could not spawn a Job at runtime without a git commit + image build + ArgoCD sync. It cannot reach the live in-memory tool registry, the Redis conversation store wiring, the AgentWeave audit trail, or the activity feed without re-plumbing all of them into a separate entrypoint. Config hot-reload (a core platform feature) does not apply to a Job spec. It is the *opposite* of "design several agents easily." Rejected as the primary mechanism; a future heavy/long-batch escape hatch (§10) may revisit it.

**Option C — In-process scheduler only (e.g. APScheduler), no autonomy loop.**
- **Cons**: a scheduler is a strict *subset* of Option A — it answers "when does a run start" but not "how does a run handle known + unknown tasks within budget and approval gates." Choosing C would still require building the autonomy loop, the budgets, and the approval machinery on top. Rejected as insufficient; its scheduling role is absorbed into Option A's supervisor.

### 5.2 Decision

**Option A.** A single `ActiveAgentSupervisor` (new module `packages/forge-agent/src/forge_agent/active/supervisor.py`), started as a background `asyncio` task in the gateway lifespan **only when `agents.active_runtime_enabled` is true**, mirroring `_init_workload_plane`. It never blocks `:8000` startup and its health is a separate non-gating `/health` component. Autonomous work is driven through the **existing** `ForgeAgent.run_structured` (so tool scoping, secret resolution, LLM routing, turn budgets, and the activity/metrics seams are all reused unchanged). Run/task/approval state persists to a new Redis keyspace via the existing `ConversationStore` backend selection.

Justification: it is the only option that (a) keeps "declare an agent in config, it just runs" true (the platform thesis), (b) reuses forge's existing run store, audit, metrics, activity feed, and tool scoping instead of forking them, and (c) matches an already-shipped, already-reviewed in-process-background-component pattern (ADR-0004).

### 5.3 The autonomy loop (plan → act → observe → handle unknown → decide)

Per run, per agent (pseudocode; the developer implements against `ForgeAgent`):

```
run = RunRecord(id, agent, status=PLANNING, budget=agent.active.budget, tools=persona.tools)
queue = deque(agent.active.tasks)                  # KNOWN tasks, in order
emergent_count = 0
while queue and not run.terminated:
    check_budget(run)                              # steps / tool_calls / tokens / wall_clock
    if kill_switch_engaged(agent): terminate(run, "killed"); break
    task = queue.popleft()
    if task.idempotency_key in run.completed: continue      # RESTART-SAFE: skip done work
    result = await agent.run_structured(
        intent=task.intent, params=task.params,
        max_turns_override=task.max_turns or agent.max_turns,
        tool_names_filter=persona.tools,           # LEAST PRIVILEGE, inherited by all tasks
    )
    # PydanticAI already does plan/act(tools)/observe INSIDE run_structured's tool-calling loop.
    checkpoint(run, task, result)                  # persist to Redis after every task
    # HANDLE UNKNOWN: the model may surface follow-up tasks it discovered while working.
    for emergent in extract_emergent_tasks(result):
        if emergent_count >= agent.active.budget.max_emergent_tasks:
            audit(run, "emergent_cap_reached"); break     # bounded autonomy: stop, don't escalate silently
        emergent_count += 1
        queue.append(emergent)                     # SAME scope + SAME approval gates apply
terminate(run, reason=run.termination_reason or "completed")
```

Key properties:
- **Plan/act/observe is not re-invented.** A single `run_structured` call already runs PydanticAI's tool-calling loop (model plans → calls a scoped tool → observes the result → continues), bounded by `max_turns`. The supervisor's loop is the *outer* orchestration across the known-task list and the emergent queue.
- **Unknown-task handling is provably in-policy.** Every task — known or emergent — is executed with `tool_names_filter=persona.tools`. An emergent task *cannot* acquire a capability the persona lacks, because the scoping is inherited, not re-derived. Emergent tasks are bounded by `max_emergent_tasks`; hitting the cap is a **stop-and-audit**, not a silent continuation.
- **Emergent-task extraction is a structured output, not free reasoning.** `extract_emergent_tasks` reads a typed field from a `run_structured(output_schema=...)` result (e.g. `follow_up: list[KnownTask]`), so the model proposes tasks through a validated channel rather than the supervisor parsing prose.

### 5.4 Run/task state, idempotency, restart-safety

- **Store**: a new `ActiveRunStore` (new module `forge_agent/active/store.py`) over the same backend selection as `ConversationStore` — `InMemoryActiveRunStore` (dev) and `RedisActiveRunStore` (durable, cross-replica), keyspace `forge:activerun:{run_id}` and an index `forge:activeruns:{agent}`. Reuses the exact serialization discipline of `RedisConversationStore` (typed JSON, sliding bounds, optional TTL).
- **Checkpoint after every task** (and every approval transition). A `RunRecord` carries `completed: set[task_key]`, the emergent queue, the running budget counters, and `status ∈ {planning, running, awaiting_approval, paused, completed, failed, killed}`.
- **Idempotency**: each task's key is `{run_id}:{task.name}` (known) or `{run_id}:emergent:{n}` (emergent). On restart, the supervisor loads in-flight runs (`status ∈ {running, awaiting_approval, paused}`) and resumes, skipping any `task_key ∈ completed`. A task that was mid-flight when the pod died is re-run (at-least-once); tools that are *not* idempotent are exactly the tools that should carry an **approval gate** (§6), which is single-use per approval id and therefore not double-executed.
- **Single-runner guard (multi-replica)**: acquire a Redis lock (`SET forge:activelock:{agent} {replica} NX EX …`, renewed on a heartbeat) before starting/resuming a run for an agent. Only the lock holder schedules that agent. Note the deployment already forbids `replicaCount > 1` when `persistence.enabled` (ADR-0004 §7.1), so single-writer is the common case; the lock is defence-in-depth for a future scaled-out deployment.

### 5.5 Termination

A run terminates on the first of: **all known + emergent tasks completed**; **any budget exhausted** (`max_steps` / `max_tool_calls` / `max_tokens` / `wall_clock_seconds`); **the kill switch** engaged (global `active_runtime_enabled → false`, per-agent `active.enabled → false`, or an explicit admin kill — §6); **an approval parked longer than `approval_timeout_seconds`**; or **an unrecoverable error**. Every termination is audited with its reason and leaves a final immutable `RunRecord` for the run history (§7).

---

## 6. Governance / safety model (central, not optional)

An active agent handling *unknown* tasks needs guardrails at five layers. Each maps to an existing forge mechanism where one exists; genuinely new machinery is called out.

### 6.1 Least-privilege tool scoping — REUSE (already enforced)

The persona's `tools[]` allow-list is applied to **every** task via `tool_names_filter=persona.tools`. Emergent/unknown tasks inherit it and cannot exceed it. No new mechanism; this is the load-bearing wall of the whole safety story, and it already exists and is verified.

### 6.2 Human-approval gates for irreversible / outward-facing actions — NEW

This is the draft→approve→publish control (e.g. the planned social-media publishing).

- **Classification**: a tool is approval-gated if it is in `ApprovalPolicy.require_approval_tools`, OR (`default_requires_approval == true` AND it is not read-only AND not in `auto_approve_tools`). Fail-safe: an *unlisted* side-effecting tool is gated by default. (A tool can also carry an optional `writes: bool` / `outward: bool` annotation on its config for auto-classification; explicit lists win.)
- **Mechanism (`ToolGate`)**: the active runtime wraps each approval-gated tool with a gate (new `forge_agent/active/gate.py`) at registry-build time, *only for supervisor-driven runs* (the human request path is unchanged). When the autonomy loop invokes a gated tool:
  1. If a valid, matching `Approval` (bound to this `run_id` + tool name + a hash of the arguments, single-use) exists → execute exactly once, then consume the approval.
  2. Otherwise → **do not execute**. Record an `ApprovalRequest{id, run_id, agent, tool, arguments, draft}` to the run store, raise `ApprovalRequired`, which the supervisor catches to **checkpoint and park** the task (`status = awaiting_approval`). The tool call becomes a *draft* awaiting a human decision.
- **Admin surface (new endpoints, RBAC-guarded)**: `GET /v1/admin/approvals` (pending, requires `config:read`), `POST /v1/admin/approvals/{id}/approve` and `/reject` (requires a new `Permission.AGENT_APPROVE = "agent:approve"`, added to the closed permission set and to the `admin`/an `approver` role). Approve mints the single-use `Approval` bound to that request's argument hash and resumes the run; reject terminates the parked task with an audited reason.
- **Idempotency**: the approval is bound to the argument hash and consumed on execution, so a restart between "approved" and "executed" cannot double-publish — the gate re-checks "already consumed" from the store before executing.

### 6.3 Full audit of autonomous decisions and actions — REUSE + extend

- Every autonomous decision/action is audited: run start/stop (+reason), each task start/complete, each tool call, each emergent-task spawn, each approval request/grant/reject, each budget-limit hit, each kill.
- **Two sinks, both existing**: (1) the recent-activity feed (`activity.py::recent_activity.record(...)` with `interface="active"` and `session_id=run_id`) — tool calls made via `run_structured` already flow through the same seam, so they appear in `GET /v1/admin/activity` for free; the supervisor adds the run/task/approval lifecycle events. (2) When the workload plane is enabled (ADR-0004), route autonomous security-relevant events (approval grants, kills, denials) through the AgentWeave `AuditTrail` (`record_auth_check`-style JSON-lines to stdout/Loki) so autonomous actions land in the same tamper-evident trail as A2A calls.

### 6.4 Rate / spend / turn / step budgets — REUSE + extend

| Budget | Enforced by | Source |
|---|---|---|
| Turns per task | `run_structured(max_turns_override=...)` → PydanticAI `UsageLimits(request_limit=...)` | EXISTS (`core.py::_build_usage_limits`) |
| Total steps (outer loop iterations) | supervisor loop counter vs `budget.max_steps` | NEW (supervisor) |
| Total tool calls | supervisor counts via the activity/metrics seam vs `budget.max_tool_calls` | NEW (counter) |
| Token/spend | LiteLLM usage accumulated across `run_structured` calls vs `budget.max_tokens` | NEW (read existing LiteLLM usage) |
| Wall-clock | `asyncio.wait_for(run, budget.wall_clock_seconds)` | NEW (supervisor) |
| Concurrency | per-agent `budget.max_concurrent_runs` + the §5.4 Redis lock | NEW (supervisor) |
| Inbound request rate (webhook/api triggers) | existing `security.rate_limit_rpm` limiter | EXISTS |

Exceeding any budget is **fail-closed**: terminate the run, audit the reason, emit a metric. Budgets are the primary defence against runaway loops and cost blowout.

### 6.5 Kill switch / pause — NEW (thin)

- **Config kill switch (declarative, hot-reload)**: `agents.active_runtime_enabled = false` stops *all* active agents; `active.enabled = false` stops one. On hot-reload the supervisor sees the change and drains/terminates affected runs (checkpointing first).
- **Runtime kill switch (imperative, immediate)**: `POST /v1/admin/active/kill` (all) and `POST /v1/admin/active/runs/{id}/kill` (one), plus `.../pause` and `.../resume`, requiring `agent:approve` (or `config:write`). Kill cancels the run's asyncio task, checkpoints `status=killed`, and audits. This is the "stop it now" button independent of a git/config round-trip.

### 6.6 Bounded autonomy — what it may decide alone vs. must escalate

| The active agent MAY decide on its own | The active agent MUST escalate (or is stopped) |
|---|---|
| Call any tool in its persona scope that is read-only or `auto_approve` | Call any approval-gated tool → **park for human approval** (§6.2) |
| Spawn emergent tasks up to `max_emergent_tasks`, all within scope | Spawn beyond `max_emergent_tasks` → **stop + audit** (no silent escalation) |
| Iterate plan→act→observe within all budgets | Exceed any budget → **terminate + audit** |
| Retry a transient, reversible tool failure within `max_steps` | Anything outside persona scope → **impossible by construction** (scoping) |

"Escalate" never means "acquire more privilege." It means park-for-approval, or stop-and-audit. There is no path by which an autonomous run gains a capability its persona was not granted.

### 6.7 Governance mapping summary

| Control | Existing forge mechanism | New in ADR-0005 |
|---|---|---|
| Least privilege | `AgentDef.tools` + `_filter_tools` | inherited by emergent tasks (config only) |
| Human approval | RBAC (`Permission`, `require_permission`), admin API pattern | `ToolGate`, `ApprovalRequest`/`Approval`, `agent:approve` perm, `/v1/admin/approvals` |
| Audit | activity feed, AgentWeave `AuditTrail` | run/task/approval lifecycle events (`interface="active"`) |
| Budgets | PydanticAI `UsageLimits`, `rate_limit_rpm` | step/tool/token/wall-clock/concurrency caps in supervisor |
| Kill switch | config hot-reload | `active_runtime_enabled`/`active.enabled` flags + `/v1/admin/active/kill|pause|resume` |
| Identity of the run | OIDC/service-token principal; OPA (workload) | active runs execute under a defined service principal (§11 open item) |

---

## 7. Observability

- **Metrics** (`metrics_registry.py`, new counters/histograms on the default registry):
  `forge_active_runs_total{agent,status}`, `forge_active_run_duration_seconds{agent}`,
  `forge_active_tasks_total{agent,kind=known|emergent,status}`, `forge_active_steps_total{agent}`,
  `forge_active_tool_calls_total{agent,tool}` (or reuse `forge_tool_invocations_total` since active tool calls go through the same seam), `forge_approval_requests_total{agent,status=pending|approved|rejected|expired}`,
  `forge_active_budget_exhausted_total{agent,reason}`, `forge_active_runs_active{agent}` (gauge).
- **Activity feed**: autonomous tool calls already surface in `GET /v1/admin/activity` because `run_structured` hits the same `recent_activity.record` seam; they carry `interface="active"` and `session_id=run_id`, so the UI can filter autonomous activity distinctly.
- **Run history** (new admin endpoints reading the `ActiveRunStore`): `GET /v1/admin/active/runs` (list, filter by agent/status) and `GET /v1/admin/active/runs/{id}` (full record: plan, known + emergent tasks, per-task tool calls, approvals with decisions, budget consumption, termination reason). This is the auditable "what did the autonomous agent do" surface.
- **UI**: a `passive`/`active` badge on each agent (from `mode`), an "active runs" page (status, budget bars, kill/pause controls), and an "approvals" inbox (draft → approve/reject). All read/write through the admin API above with the existing zustand/TanStack Query stack.

---

## 8. Consequences

**Positive**: delivers autonomy as a *governed* capability, not a raw one; reuses the ADR-0004 background-component pattern, the ADR-0003 Redis store, and the existing scoping/audit/metrics/activity seams rather than forking infrastructure; declaring an active agent stays a config edit (thesis-preserving); backward compatible (default passive, two master switches default OFF); unknown-task handling is provably confined to persona scope; the human `:8000` plane and existing passive behavior are untouched.

**Negative / limitations**: a long-running component now lives in the request-serving process (mitigated: non-gating health, hard budgets, single-runner lock); at-least-once task execution means non-idempotent side effects must be approval-gated (by design — that is exactly the gate's job); multi-replica autonomy needs the Redis lock; the approval inbox introduces a human-in-the-loop latency for outward actions (intended).

**Risks + mitigations** (expanded in §11 security review): runaway loop → multi-dimensional budgets + kill switch; cost blowout → token/spend budget + concurrency cap; tool misuse / exfiltration → inherited least-privilege scope + approval gates + full audit; acting without authorization → two-key enable + approval gates + defined run principal.

**Tech debt / deferred**: webhook + schedule triggers (Phase 2); OPA-authorized active runs (unify with ADR-0004 workload authz — §10); a heavy/long-batch escape hatch to k8s Jobs (§10); per-run principal delegation semantics (§11 open item).

---

## 9. Phased rollout (MVP vs. later)

| Phase | Ships | MVP? | Gated by |
|---|---|---|---|
| **0 — Declaration** | `AgentMode`, `AgentDef.mode` (default passive), `triggers`/`active` **schema shapes**, validators, UI `passive`/`active` badge. **No runtime behavior change** — an `active` agent is declared but inert (logged), behaves passively. | **MVP** | nothing (pure additive schema + UI) |
| **1 — Active runtime (flagged)** | `ActiveAgentSupervisor` behind `agents.active_runtime_enabled` (default OFF) + per-agent `active.enabled` (default OFF). Known-task-list execution, emergent-task handling, `ActiveRunStore` (Redis), all §6.4 budgets, `ToolGate` + approvals inbox + `agent:approve`, kill/pause, audit, run history, active metrics. **Manual "run now" trigger only** (admin action). | MVP+1 | two-key flag; non-gating health |
| **2 — Triggers & schedules** | `webhook` trigger (inbound router + HMAC + SSRF/authz guards) and `schedule`/`cron` triggers (supervisor scheduler) for both passive and active; scheduled autonomous runs. | later | Phase 1 proven |
| **3 — Workload-authorized autonomy** | Active runs authorized via OPA on the ADR-0004 workload plane; per-run SPIFFE principal. | later | ADR-0004 live; separate ADR |

Phase 0 is deliberately behavior-free so the declaration model can land and be adopted (personas gain a visible, auditable mode) with zero risk. Phase 1 is the substantive, security-reviewed change and is doubly gated OFF by default. Nothing autonomous can run until an operator flips *both* keys.

---

## 10. Alternatives considered (runtime placement) and why not now

- **k8s `CronJob`/`Job` per active agent** — rejected as primary (§5.1 Option B): incompatible with the config-driven, hot-reloadable, ArgoCD-GitOps platform thesis and cannot reach the live tool registry / run store / audit without re-plumbing. **Kept as a future escape hatch** only for genuinely heavy/long batch runs that shouldn't share the gateway process — a separate decision if/when a workload needs it.
- **A separate "worker" Deployment** sharing Redis with the gateway — viable long-term for scale, but premature: it duplicates config loading, tool building, secret resolution, and audit wiring for a capability that starts single-runner. Revisit when active-run volume justifies a dedicated pod. The `ActiveAgentSupervisor` is deliberately factored (in `forge_agent`, not `forge_gateway`) so it *could* be hosted by a separate entrypoint later without a rewrite.
- **OPA-authorized autonomy from day one** — deferred to Phase 3 to avoid coupling the first autonomy release to the workload-plane rollout; RBAC + approval gates are sufficient governance for Phase 1.

---

## 11. Security review of the model (autonomous-agent risk analysis)

| Risk | How it manifests here | Mitigation in this design |
|---|---|---|
| **Runaway loop** | Emergent tasks spawn emergent tasks; the plan/act loop never converges. | `max_steps`, `max_tool_calls`, `wall_clock_seconds`, `max_emergent_tasks` (all fail-closed → terminate + audit); per-task `max_turns` via PydanticAI `UsageLimits`; runtime kill switch. Emergent extraction is a *bounded, typed* channel, not open recursion. |
| **Cost blowout** | Autonomous LLM/tool calls run up token/API spend unattended. | `max_tokens` budget from LiteLLM usage; `max_tool_calls`; `max_concurrent_runs` (default 1, no overlap); wall-clock timeout; metrics + `forge_active_budget_exhausted_total` for alerting. |
| **Tool misuse** | The agent uses a scoped tool in a harmful way, or an emergent task tries a broader capability. | Least-privilege scope inherited by *every* task (emergent tasks provably cannot exceed persona `tools[]`); side-effecting tools default to approval-gated (`default_requires_approval=true`); every call audited. |
| **Data exfiltration** | An outward-facing tool (publish, webhook-out, email) sends sensitive data. | Outward/side-effecting tools are approval-gated (draft→approve→publish); egress is limited to the persona's scoped tools; existing SSRF guard (`validate_peer_endpoint`) applies to outward HTTP; secrets never enter drafts un-redacted (reuse admin redaction). |
| **Acting without authorization** | The agent takes a consequential action no human sanctioned. | Two-key enable (`active_runtime_enabled` + `active.enabled`, both default OFF); approval gate for irreversible actions; bounded autonomy (escalate = park/stop, never gain privilege); the run executes under a defined principal, not ambient authority. |
| **Approval bypass / replay** | A parked action executes without a valid approval, or twice. | `Approval` is bound to `run_id` + tool + **argument hash**, single-use, consumed on execution; the gate re-checks "already consumed" from the durable store before executing, so a restart between approve and execute cannot double-publish; unlisted side-effecting tools fail *closed* (gated), never open. |
| **Restart / idempotency hazard** | Pod dies mid-task; on resume the task re-runs a non-idempotent side effect. | At-least-once with per-task idempotency keys; completed tasks skipped on resume; the *only* non-idempotent actions are the outward ones — which are exactly the approval-gated, single-use ones, so they are not silently re-executed. |
| **Prompt injection into emergent tasks** | A tool result contains adversarial text that makes the model propose malicious follow-up tasks. | Emergent tasks are still scope-confined and approval-gated; `max_emergent_tasks` caps blast radius; all spawns are audited and visible in run history; a human sees any outward action as a draft before it happens. |
| **Multi-replica double-fire** | Two replicas each start the same scheduled run. | Redis single-runner lock per agent (§5.4); deployment already single-replica when persistence is enabled (ADR-0004). |
| **Supervisor failure taking down the pod** | A crash in the autonomy component affects the human API/UI. | Non-gating health component (ADR-0004 pattern); supervisor failure logs CRITICAL and marks the active subsystem unhealthy but never flips `/health/ready`; the `:8000` plane is unaffected. |

**Residual owner decisions (open items):**
1. **Run principal / delegation** — under which identity does an autonomous run act (a dedicated service principal? the persona owner's delegated authority?), and how is that recorded in audit. Recommend a dedicated per-agent service principal with the persona's scope; finalize before Phase 1.
2. **Approval routing** — who is notified/authorized to approve for a given agent (a role binding vs. per-agent approver list). Recommend an `approver` role via the existing binding mechanism.
3. **OPA-authorized autonomy** (Phase 3) — whether active runs must pass an OPA decision like workload A2A does.
4. **Heavy/long-batch escape hatch** (§10) — if/when to offload a specific workload to a k8s Job.

---

## 12. Per-package TDD task breakdown (test-first; security tests in **bold**) — Phase 1 unless noted

### forge-config (Phase 0)
- Add `AgentMode`, `AgentDef.mode`/`triggers`/`active`, `TriggerDef`/`TriggerType`, `KnownTask`, `BudgetConfig`, `ApprovalPolicy`, `ActiveConfig`, `AgentsConfig.active_runtime_enabled`; `AgentDef` validator (active requires ≥1 task; passive rejects an `active` block).
- Tests: `test_agentdef_defaults_to_passive`, **`test_existing_single_agent_config_unchanged`** (no `mode` field parses and behaves as today), `test_active_requires_tasks`, `test_passive_rejects_active_block`, `test_active_runtime_disabled_by_default`, `test_trigger_types_parse`.

### forge-agent (Phase 1)
- `active/supervisor.py` (`ActiveAgentSupervisor`: scheduler, work queue, autonomy loop, budgets, kill), `active/store.py` (`ActiveRunStore`: in-memory + Redis, `RunRecord`/`TaskRecord`), `active/gate.py` (`ToolGate`, `ApprovalRequest`/`Approval`, argument-hash binding), emergent-task extraction via typed `run_structured` output.
- Tests: `test_supervisor_runs_known_tasks_in_order`, `test_emergent_task_enqueued_within_scope`, **`test_emergent_task_cannot_exceed_persona_tools`**, **`test_emergent_cap_stops_not_escalates`**, **`test_budget_exhaustion_terminates_fail_closed`** (each of steps/tool_calls/tokens/wall_clock), `test_run_state_checkpointed_after_each_task`, **`test_restart_skips_completed_tasks`**, **`test_gated_tool_parks_for_approval`**, **`test_approval_single_use_bound_to_argument_hash`**, **`test_unlisted_side_effecting_tool_gated_by_default`**, `test_kill_switch_cancels_and_checkpoints`, `test_single_runner_redis_lock`.

### forge-gateway (Phase 1)
- Start/stop `ActiveAgentSupervisor` in `lifespan` (only when `active_runtime_enabled`), non-gating `health.set_active_health(...)`; admin endpoints `/v1/admin/approvals[...]`, `/v1/admin/active/runs[...]`, `/v1/admin/active/{kill,pause,resume}`; new `Permission.AGENT_APPROVE`; active metrics; route active tool calls through the existing activity seam.
- Tests: **`test_active_supervisor_absent_when_runtime_disabled`**, **`test_active_health_does_not_gate_human_readiness`**, **`test_approve_requires_agent_approve_permission`**, `test_run_now_starts_run`, `test_kill_endpoint_terminates_run`, `test_run_history_endpoint_returns_record`, `test_active_tool_calls_appear_in_activity_feed`, **`test_disabling_runtime_hot_reload_drains_runs`**.

### forge-ui (Phase 0 badge; Phase 1 pages)
- `passive`/`active` badge from `mode`; active-runs page (status/budget/kill/pause); approvals inbox (draft → approve/reject).
- Tests (vitest): `test_mode_badge_renders`, `test_approvals_inbox_lists_pending`, `test_kill_button_calls_endpoint`.

### Phase 2 (later)
- forge-gateway inbound `POST /v1/hooks/{path}` with HMAC verify + SSRF/authz; supervisor cron scheduler for `schedule` triggers.
- Tests: **`test_webhook_rejects_bad_hmac`**, **`test_webhook_endpoint_ssrf_guarded`**, `test_scheduled_run_fires_on_cron`, **`test_scheduled_run_single_fire_across_replicas`**.

---

## 13. Validation criteria

- A default `forge.yaml` and every pre-existing config parse and behave identically (no `mode` ⇒ passive; no supervisor started).
- With both keys OFF, no `ActiveAgentSupervisor` exists and `/health/ready` is unchanged.
- With both keys ON: a declared active agent runs its known tasks in order; an emergent task runs only within persona scope; an approval-gated tool parks a draft and executes exactly once after approval; each budget, when exceeded, terminates the run fail-closed with an audit event and a metric; the kill switch stops a run immediately.
- A supervisor crash never flips `/health/ready` to NOT READY.
- Every autonomous tool call appears in `GET /v1/admin/activity` (`interface="active"`) and the run history; every approval/kill/budget event is audited.
- A pod restart mid-run resumes without re-executing a completed or already-approved-and-executed action.
</content>
</invoke>
