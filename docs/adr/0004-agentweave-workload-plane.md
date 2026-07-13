# ADR-0004: AgentWeave Workload Plane (SPIFFE identity + OPA authz + mTLS + audit) on the A2A surface

**Date**: 2026-07-13
**Status**: Proposed
**Relates to**: ADR-0001 (Dex OIDC human auth), ADR-0002 (user-issued API keys), ADR-0003 (spec-compliance gaps, WS-3/WS-4)
**Governing decision (owner, non-negotiable)**: Two auth planes. Dex OIDC = humans (north-south, live, enforce, unchanged). AgentWeave SPIFFE+OPA = AI workloads (east-west, A2A). This ADR designs **only** the workload plane and is strictly **additive** — it must not modify or risk the live human OIDC path.

---

## 1. Context

`docs/technical/security.md` / `docs/user/features/security.md` / `docs/developer/architecture.md` promise that every agent has a cryptographic SPIFFE identity, that agent-to-agent messages are authenticated/signed, that OPA authorizes agent calls, and that security events are audited. ADR-0003 confirmed the reality: `forge-security` ships a `MockIdentityProvider` (`CERT_NONE`) and thin re-implementations, and **never imports the real `agentweave` package** — which exists and works as a sibling at `/Users/ajgeddes/dev/claude-code/agentweave`.

Infra is now live on `hvs-k8s` (verified healthy):
- **SPIRE** trust domain `hvslocal`, CSI driver `csi.spiffe.io`, registration entry `spiffe://hvslocal/ns/dev-aj-geddes/sa/default` for the forge-ai pod. Workload API socket via CSI at `/spiffe-workload-api/spire-agent.sock` — note the CSI filename differs from agentweave's default, so `SPIFFE_ENDPOINT_SOCKET=unix:///spiffe-workload-api/spire-agent.sock` must be set.
- **OPA** at `http://opa.opa.svc.cluster.local:8181`, policy path `agentweave/authz/allow` (agentweave's zero-config default), starter default-deny rego deployed at `deploy/cluster/opa/policy/agentweave_authz.rego`.

### 1.1 What the real AgentWeave SDK actually provides (read from source)

This constrains every decision below. Verified by reading the SDK:

| Primitive | Real API | Notes |
|---|---|---|
| Identity | `agentweave.identity.SPIFFEIdentityProvider(endpoint=None, tls_min_version=TLSv1_3)`; `await initialize()`; `get_identity() -> str`, `get_svid() -> X509Svid`, `get_trust_bundle(td) -> X509Bundle`, `create_tls_context(server: bool) -> ssl.SSLContext`, `register_rotation_callback(cb)` | **X.509-SVID only.** Reads `SPIFFE_ENDPOINT_SOCKET`. mTLS context is `CERT_REQUIRED`, `check_hostname=False`, TLS 1.3. |
| **JWT-SVID** | **Does not exist anywhere in agentweave** (grep-confirmed) | py-spiffe supports it; agentweave does not wrap it. |
| **Payload signing** | **No signing primitive exists.** Integrity is the mTLS channel: `transport.channel.SecureChannel(identity, peer_spiffe_id, config)` enforces peer SPIFFE-ID match during the TLS handshake. | agentweave's answer to "signed messages" == authenticated mutual TLS. |
| Authz | `agentweave.authz.OPAProvider(endpoint, policy_path="agentweave/authz/allow", default_deny=True, ...)`; `await check(caller_id, resource, action, context) -> AuthzDecision(allowed, reason, policy_id, audit_id)` | Circuit breaker + decision cache built in. Builds input doc: `caller_spiffe_id`, `resource_spiffe_id`, `action`, `caller_trust_domain`, `resource_trust_domain`, `timestamp`, `context`. `default_deny=True` => OPA-unreachable returns deny. |
| Audit | `agentweave.observability.audit.AuditTrail(agent_name, backend, enabled)` with `FileAuditBackend(path)`, `StdoutAuditBackend()`, `MultiBackend([...])`; methods `record_peer_verification`, `record_auth_check`, `record_identity_rotation`, ... | JSON-lines. `FileAuditBackend` needs a writable path/volume. |
| A2A comms | `agentweave.comms.a2a.A2AServer` (FastAPI, JSON-RPC 2.0, `SPIFFEMiddleware` extracts peer SPIFFE ID from the client mTLS cert URI SAN, authz-gated); `A2AClient(identity_provider, ...)` builds an mTLS httpx client via `create_tls_context(server=False)` | Forge's current A2A is a **different, minimal 2-endpoint protocol** (`GET /a2a/agent-card`, `POST /a2a/tasks`), not agentweave's JSON-RPC A2A. |
| Test doubles | `agentweave.testing.mocks.MockIdentityProvider`, `MockAuthorizationProvider`, `MockTransport` | Real SPIRE/OPA not required for unit tests. |

### 1.2 What the Forge deployment looks like (read from `deploy/`)

The pod is reached at `https://forgeai.hvslocal` via an **HTTPRoute** whose `parentRef` is the shared Gateway API listener (`sectionName: https`). **TLS is terminated at the gateway**; the backend hop to the pod on `:8000` is plain HTTP. Consequence: a client mTLS certificate cannot survive to the pod on the existing human path — **mTLS peer verification is only possible on a listener that terminates TLS at the pod itself.**

The live human auth path (do not disturb): `forge_gateway.security.get_principal` -> `forge_security.oidc.resolve_principal` with a strict resolver order (cookie session -> `forge_sk_` service token -> Dex RS256 JWS), producing a `Principal(kind ∈ {"user","service","dev"})`. `POST /a2a/tasks` is currently guarded by `require_permission("agent:peer")` on this human plane (i.e. an operator/service-token caller today, not a workload SVID).

---

## 2. Decision Drivers

- **Security-first / fail-closed**: a fake SVID, an unreachable SPIRE, or an unreachable OPA must never allow a workload call. Never fail open.
- **Additive / zero-risk to humans**: the human OIDC plane (port `:8000`, resolver order, Principal) must be byte-for-byte unchanged. An incorrect workload design that lets a caller cross into the human plane, or that fails open, is unacceptable.
- **Design to agentweave's REAL API**, not an aspirational one (no JWT-SVID, no payload signing exist).
- **Dev velocity / testability**: unit tests must run without real SPIRE/OPA (inject agentweave mocks).
- **Deployment reality**: TLS terminates at the shared Gateway; mTLS must terminate at the pod.

---

## 3. Q1 (the crux): how does a workload caller authenticate on the A2A path?

### Options

**Option A — JWT-SVID as a bearer token over the existing HTTP `/a2a` endpoint.**
- **Pros**: fits the current transport; works straight through the TLS-terminating gateway; no second listener; verifiable against the SPIRE trust bundle.
- **Cons**: **agentweave does not implement JWT-SVID at all** — no minting, no verification, no JWKS/bundle validator. Choosing this means writing net-new SDK code (py-spiffe `JwtSource`/`JwtSvidValidator` wrapping) — i.e. *not* "use the real agentweave API." A bearer JWT-SVID is also replayable unless audience/channel-bound, which needs more machinery. Rejected for this ADR; see §11 (future, owner decision + agentweave enhancement).

**Option B — Dedicated pod-terminated X.509-SVID mTLS listener for the workload plane. (CHOSEN)**
- **Pros**: exactly what agentweave supports today (`create_tls_context(server=True)`, `SecureChannel`, `SPIFFEMiddleware` peer-cert extraction). mTLS terminates at the pod, so the client SVID is verifiable. `CERT_REQUIRED` + SPIRE trust bundle rejects any non-SPIRE client **at the TLS handshake** — the strongest possible fail-closed. Fully additive: a new port, a new ASGI app, a new in-cluster Service, none of it touching `:8000` or the HTTPRoute.
- **Cons**: a second listener in the process (uvicorn SSLContext wiring gotcha — see §7.3); requires an in-cluster east-west Service that is **not** fronted by the TLS-terminating Gateway; peers must reach the pod on the mTLS port directly.

**Option C — reuse the shared gateway `:8000` with mTLS.**
- **Cons**: impossible. The shared Gateway terminates TLS; the client cert never reaches the pod. Rejected.

### Decision

**Option B.** Run a **second ASGI listener inside the forge-gateway process on `:8443` ("a2a-mtls")**, with a server-side SSL context from `SPIFFEIdentityProvider.create_tls_context(server=True)` (`CERT_REQUIRED`, TLS 1.3, SPIRE trust bundle). This listener serves **only** the workload A2A surface. The caller's identity is the SPIFFE URI SAN of the **verified** client certificate. Expose `:8443` via a **new ClusterIP Service**, deliberately **not** attached to the HTTPRoute/shared Gateway (no L7 TLS termination in front of it). The human `:8000` path is untouched.

Rationale: this is the only option that (a) uses agentweave's real primitives, (b) fits the deployment (mTLS at the pod), and (c) gives handshake-level fail-closed against forged identities. Forge keeps its existing minimal 2-endpoint A2A protocol (not agentweave's JSON-RPC `A2AServer`) to keep the change additive and low-risk; migrating to agentweave's `A2AServer` is a separate future step (§11).

---

## 4. Q2: verified workload identity -> Principal (without weakening the human resolver)

### Two physically separated resolvers, one Principal type

```
:8000  (human plane, TLS terminated at Gateway, plain HTTP to pod)
        get_principal -> resolve_principal(cookie|forge_sk_|Dex-JWS)
        -> Principal(kind ∈ {user, service, dev})          [UNCHANGED]

:8443  (workload plane, mTLS terminated at pod, CERT_REQUIRED)
        get_workload_principal -> resolve_workload_principal(verified peer cert)
        -> Principal(kind="workload", sub=<spiffe_id>, spiffe_id=<spiffe_id>)   [NEW]
```

- **Additive Principal change** (`forge_security.oidc.principal`): extend `PrincipalKind` to `Literal["user","service","dev","workload"]` and add one optional field `spiffe_id: str | None = None`. Human paths never set it. This is the only change to a shared type, and it cannot alter existing human behavior (new enum member, new defaulted field).
- **No downgrade path exists by construction**:
  - An attacker on `:8000` cannot forge a workload identity: `resolve_principal` has **no input** that produces `kind="workload"` (no header, no body, no query param) — the workload resolver is only mounted on `:8443`.
  - An attacker on `:8443` cannot forge an SVID: the mTLS `CERT_REQUIRED` handshake against the SPIRE trust bundle rejects any cert not issued by SPIRE **before any request is processed**.
- **Identity extraction is deny-by-default**: after the handshake guarantees the peer cert chains to SPIRE, `resolve_workload_principal` parses the URI SAN (`spiffe://...`) using the same logic as agentweave's `SecureChannel._extract_spiffe_id_from_cert`. If no `spiffe://` SAN is present, **reject (401)** — never proceed with an unknown identity.
- **Workload permissions come from OPA, not the role/binding Authorizer** (§5). The workload Principal carries the raw SPIFFE ID; authorization is a per-request OPA decision, not a static role set.

---

## 5. Q3: OPA authorization — input contract, gate, coexistence

### Input contract

Forge calls `OPAProvider.check(caller_id, resource, action, context)`; agentweave builds the OPA `input` document. The contract Forge commits to:

| Field (built by agentweave from Forge's args) | Value Forge supplies |
|---|---|
| `caller_spiffe_id` | verified peer SVID SPIFFE ID (from the client cert SAN) |
| `resource_spiffe_id` | **this** agent's own SPIFFE ID (`await identity.get_identity()`) |
| `action` | canonical verb: `"a2a:task"` for `POST /a2a/tasks`; `"tools:invoke"` when a specific tool is the resource |
| `caller_trust_domain` / `resource_trust_domain` | derived by agentweave from the SPIFFE IDs |
| `timestamp` | set by agentweave |
| `context` | `{"task_type": <str>, "tool": <str|None>, "peer_trust_level": <high|medium|low>}` — `peer_trust_level` looked up from `agents.peers[].trust_level` when the caller matches a configured peer |

The OPA endpoint/policy path is agentweave's zero-config default: `POST http://opa.opa.svc.cluster.local:8181/v1/data/agentweave/authz/allow`.

### Gate

```
decision = await opa.check(caller_id=peer_spiffe_id,
                           resource=my_spiffe_id,
                           action="a2a:task",
                           context={...})
if not decision.allowed:
    audit.record_auth_check(..., decision="deny", reason=decision.reason)
    raise HTTPException(403, "forbidden")   # fail closed
```

`OPAProvider` is constructed with `default_deny=True`, so an unreachable OPA (or open circuit breaker) returns `allowed=False` -> **403** (fail closed). The human plane never calls OPA in this ADR, so an OPA outage cannot affect humans.

### Coexistence with the human Authorizer

The human plane keeps its role -> permission `Authorizer` (ADR-0001/0002) unchanged. The workload plane uses **OPA** as its authorizer. They do not overlap because they run on different listeners with different Principal kinds. ADR-0003's longer-term vision (OPA as the shared downstream authorizer over *either* identity) is deliberately **out of scope here** to avoid touching the human path; it is a future step.

### Starter allow rule (design artifact; developer adds to `deploy/cluster/opa/policy/`)

The deployed rego already allows same-trust-domain + `action ∈ {read, list, health_check}`, default-deny. Extend it for A2A (illustrative):

```rego
package agentweave.authz
import rego.v1
default allow := false

# Same-trust-domain low-risk read actions (already deployed).
allow if {
    input.caller_spiffe_id
    input.resource_spiffe_id
    input.caller_trust_domain == input.resource_trust_domain
    input.action in {"read", "list", "health_check"}
}

# A2A task / tool invocation: same trust domain AND caller is a known peer
# whose configured trust_level is at least "medium".
allow if {
    input.caller_trust_domain == input.resource_trust_domain
    input.action in {"a2a:task", "tools:invoke"}
    input.context.peer_trust_level in {"high", "medium"}
}
```

---

## 6. Q4: message signing — where it hooks in

**Decision: on the workload plane, "signed messages" == mutual TLS with SPIFFE peer verification.** agentweave has no payload-signing primitive; its integrity/authenticity guarantee is the mTLS channel (`SecureChannel` enforces the peer SPIFFE ID at handshake, `create_tls_context` gives `CERT_REQUIRED`). Because the workload path is pod-to-pod mTLS with **no** TLS-terminating intermediary, the channel is authenticated and integrity-protected end-to-end. This is the honest, agentweave-native answer.

Hook points:
- **Outbound** (`forge_agent.agent.peers.PeerCaller`): replace the bare `httpx.AsyncClient()` with an mTLS client built from `SPIFFEIdentityProvider.create_tls_context(server=False)` — i.e. use agentweave's `A2AClient(identity_provider=...)` or `SecureChannel(identity, peer_spiffe_id=<peer expected id>, config)`. Add an optional `spiffe_id` field to `PeerAgent` so the client can verify it reached the intended peer (`SecureChannel` raises `PeerVerificationError` on mismatch). **Drop the `caller_id` body field** from `PeerCaller.A2ATaskRequest` — identity now comes from the client cert; the server already ignores the body field (ADR-0001 removed it server-side), so it is dead and misleading.
- **Inbound** (`/a2a/tasks` on the mTLS listener): identity is the verified peer-cert SPIFFE ID; integrity is guaranteed by mTLS; nothing to verify in the body.

**Optional additive JWS layer (NOT primary; owner decision — §11):** if application-level non-repudiation beyond transport is wanted (e.g. to persist a signed record in the audit log), retain `forge_security.signing.MessageSigner` (Ed25519) and record the signature in the audit event. Binding an independent Ed25519 key to the SPIFFE identity, or reusing the SVID private key for app-layer signing, is an owner call. Keep this out of the critical path; mTLS is the mechanism of record.

---

## 7. Q5–Q7: audit, forge-security refactor, config + deploy

### 7.1 Q5 — Audit sink

Route workload-plane security events through `AuditTrail(agent_name=<config.metadata.name>, backend=...)`:
- `record_peer_verification(peer_id, status, reason)` — SVID verified / rejected.
- `record_auth_check(caller_id, action, resource, decision, duration, reason)` — OPA decision.
- `record_identity_rotation(...)` — wired to `SPIFFEIdentityProvider.register_rotation_callback`.

**Default backend: `StdoutAuditBackend`** (JSON-lines to stdout -> Promtail/Loki). This needs **no volume** and is safe with any replica count. Provide `FileAuditBackend(path)` as an opt-in for a local durable trail; if chosen, it needs a writable path — reuse the existing Longhorn PVC (`/app/data/audit/workload-audit.jsonl`; note the deployment already forbids `replicaCount>1` when `persistence.enabled`, so single-writer is guaranteed) or an `emptyDir`. Recommend stdout by default to avoid PVC coupling. `MultiBackend([Stdout, File])` if both are wanted.

### 7.2 Q6 — forge-security new module structure + public API

Add a `forge_security.workload` subpackage (mirrors the existing `forge_security.oidc` structure); do not entangle with the human resolver:

```
forge_security/
  oidc/                      # UNCHANGED (human plane)
    principal.py             # + kind="workload", + spiffe_id (only additive edit here)
  workload/                  # NEW (workload plane)
    __init__.py              # public API re-exports
    providers.py             # build_workload_plane(cfg, *, test_mode) -> WorkloadPlane
    resolver.py              # resolve_workload_principal(peer_cert_der, identity) -> Principal
    authz.py                 # authorize_workload(principal, action, resource, context, opa) -> None | raise
    mtls.py                  # server_ssl_context(identity), PeerCertMiddleware (extract SAN)
    audit.py                 # build_audit_trail(cfg) -> AuditTrail  (agentweave-backed)
    errors.py                # WorkloadAuthError (401), WorkloadForbidden (403), WorkloadUnavailable
```

`WorkloadPlane` is the single object forge-gateway consumes:

```python
@dataclass
class WorkloadPlane:
    identity: SPIFFEIdentityProvider          # or agentweave MockIdentityProvider in tests
    opa: OPAProvider                          # or MockAuthorizationProvider in tests
    audit: AuditTrail
    async def server_ssl_context(self) -> ssl.SSLContext: ...   # create_tls_context(server=True)
    async def my_spiffe_id(self) -> str: ...                    # identity.get_identity()
```

- `build_workload_plane(config.agentweave, *, test_mode=False)`:
  - production: real `SPIFFEIdentityProvider(endpoint=cfg.spiffe_endpoint)` (+ `await initialize()`), real `OPAProvider(endpoint=cfg.opa_endpoint, default_deny=True)`, `AuditTrail(...)`.
  - `test_mode=True` (or DI): inject `agentweave.testing.mocks.MockIdentityProvider` + `MockAuthorizationProvider` so unit tests need no SPIRE/OPA.
- **Retire the stub**: `forge_security.identity.MockIdentityProvider` (`CERT_NONE`) is deleted from the runtime path; `ForgeIdentityManager` becomes a thin adapter over the real provider (or is superseded by `WorkloadPlane`). `MessageSigner`/`ForgeKeypair` retained only for the optional JWS layer (§6), otherwise marked deprecated. The retired `SecurityGate` is untouched by this ADR (that is WS-2).

### 7.3 Q7 — Config + deploy (design only; developer applies — do not modify `deploy/`/`packages/` in this ADR)

**Config schema (`forge_config.schema`) — additive, un-inert `agentweave` for the workload plane only:**
- Keep existing `AgentWeaveConfig` fields. The `enabled` flag now gates the **workload plane** (SPIFFE + mTLS listener + OPA + workload audit). It still **cannot** disable the human OIDC plane — preserve the ADR-0001 guardrail: `agentweave.enabled` has no effect on `security.auth`/`oidc`.
- Add: `workload_listener_port: int = 8443`, `audit_backend: Literal["stdout","file"] = "stdout"`, `audit_path: str | None = None`.
- Add `PeerAgent.spiffe_id: str | None = None` (expected peer SVID for outbound `SecureChannel` verification).

**Deployment (`deploy/helm/forge/templates/deployment.yaml`):**
- **CSI volume** (ephemeral, read-only) for the Workload API socket:
  ```yaml
  volumes:
    - name: spiffe-workload-api
      csi: { driver: csi.spiffe.io, readOnly: true }
  volumeMounts:
    - name: spiffe-workload-api
      mountPath: /spiffe-workload-api
      readOnly: true
  ```
- **Env**: `SPIFFE_ENDPOINT_SOCKET=unix:///spiffe-workload-api/spire-agent.sock`, `OPA_ENDPOINT=http://opa.opa.svc.cluster.local:8181`.
- **Port**: add `containerPort: 8443` (`name: a2a-mtls`).
- **Service**: a new ClusterIP Service (or a second port on the existing Service) exposing `8443`, **not** referenced by the HTTPRoute (keeps it off the TLS-terminating Gateway; east-west only).
- **Audit**: default stdout (no volume). File backend -> mount `/app/data/audit` (existing PVC) or an `emptyDir`.
- **values-hvs-k8s.yaml**: flip `security.agentweave.enabled: true`, `trust_domain: hvslocal`, `opa_endpoint`, `spiffe_endpoint` socket; enable the CSI mount toggle. The forge-ai SPIRE entry already exists, so mounting the CSI socket yields an SVID immediately.

**uvicorn SSLContext gotcha (flag for developer):** uvicorn builds its own SSLContext from cert/key/ca **file paths**, whereas `SPIFFEIdentityProvider.create_tls_context(server=True)` returns a ready `ssl.SSLContext` backed by rotating in-memory SVIDs. Options: (a) run the mTLS listener under a server that accepts a prebuilt `SSLContext` (e.g. hypercorn), (b) assign the provider's context onto the uvicorn `Server.config.ssl` before `serve()`, or (c) pass the SVID temp-file paths the provider already writes and refresh them via `register_rotation_callback`. Register a rotation callback either way so the listener picks up rotated SVIDs.

---

## 8. Q8: rollout & fail-safe

### Workload-plane default and fail-closed matrix

| Condition | Human plane (`:8000`) | Workload plane (`:8443`) |
|---|---|---|
| `agentweave.enabled=false` | normal (OIDC enforce) | **mTLS listener not started**; no workload A2A surface |
| `agentweave.enabled=true`, all healthy | normal | A2A **requires** a valid SPIRE SVID (default) + OPA allow |
| Fake / foreign-CA SVID presented | unaffected | **rejected at TLS handshake** (`CERT_REQUIRED` + SPIRE bundle) — no request processed |
| SPIFFE socket unreachable at startup | **normal (starts and serves)** | `initialize()` raises -> **mTLS listener NOT started**, log CRITICAL, workload health component = unhealthy |
| OPA unreachable at request time | unaffected (never calls OPA) | `default_deny=True` -> **403** (fail closed) |
| SVID valid but no `spiffe://` SAN | unaffected | **401** (deny unknown identity) |

**Key fail-safe design choice (owner-visible):** the **workload-plane health is a separate `/health` component that does NOT gate the pod's human readiness.** A SPIRE/OPA outage takes down agent-to-agent traffic but must not flip `/health/ready` to NOT READY and pull the human UI/API out of the Service. This is the concrete expression of "additive / never risk the human path." (Contrast: the human auth subsystem *does* gate readiness, per ADR-0001.)

### Rollout without breaking the live deployment (it is enforce and serving)

1. **Ship code** with `agentweave.enabled=false`: the mTLS listener is off, zero runtime change; unit-test everything with agentweave mocks.
2. **Deploy CSI mount + env** (`SPIFFE_ENDPOINT_SOCKET`, `OPA_ENDPOINT`), still `enabled=false`; verify the pod receives an SVID (`spiffe://hvslocal/ns/dev-aj-geddes/sa/default`).
3. **Flip `enabled=true`**; verify OPA reachable and the starter A2A policy loaded; the `:8443` mTLS listener comes up; smoke-test a peer call.
4. The human plane is untouched at every step.

---

## 9. Consequences

**Positive**: delivers the documented workload security story on real agentweave primitives; handshake-level fail-closed against forged identity; strictly additive; unit-testable without infra; OPA-driven, policy-as-code authorization for agents.

**Negative / limitations**: a second in-process listener + a new east-west Service to operate; peers must address the pod on `:8443` directly (not via the public host); "signing" is transport-level mTLS, not an application JWS envelope (acceptable — matches agentweave); OPA authorizes only workloads here (unifying with humans is future work).

**Risks + mitigations**:
- *uvicorn/SSLContext + SVID rotation mismatch* -> use a server accepting a prebuilt context or refresh temp files via rotation callback (§7.3); test rotation.
- *Accidentally exposing `:8443` through the Gateway* (would terminate TLS and break mTLS) -> the new Service is deliberately not in any HTTPRoute `backendRef`; add a deploy-render test asserting this.
- *Workload health accidentally gating human readiness* -> keep it a separate, non-gating health component; test.

**Tech debt**: JWT-SVID over the shared HTTP endpoint (would need an agentweave enhancement) and OPA-authorizes-humans unification are deferred (§11).

---

## 10. Validation criteria

- A peer with a valid SPIRE SVID and an OPA-allowed action completes `POST /a2a/tasks` on `:8443`; audit shows `PEER_VERIFICATION=success` + `AUTH_CHECK=allow`.
- A client without a SPIRE cert cannot complete the handshake on `:8443`.
- OPA down => workload 403; SPIRE down => `:8443` absent, `:8000` still Ready.
- Every existing human-plane test still passes unchanged; no `:8000` request can yield `kind="workload"`.

---

## 11. Open items needing an owner decision / new infra

1. **JWT-SVID bearer transport (Option A)** — would let A2A stay on the shared HTTP endpoint but requires a **new agentweave capability** (JWT-SVID mint/verify) + audience/channel binding. Owner decision + SDK work.
2. **Application-level JWS signing (§6)** — beyond mTLS, for persisted non-repudiation. Owner decision on key management (independent Ed25519 vs SVID key reuse).
3. **OPA as the shared authorizer for the human plane too** (ADR-0003 vision) — deferred to protect the human path; separate ADR.
4. **Migrate Forge's custom 2-endpoint A2A to agentweave's JSON-RPC `A2AServer`** — larger change; kept out to stay additive.
5. **Audit durability** — stdout (default) vs `FileAuditBackend` on the PVC; if file, confirm the single-writer/replica constraint is acceptable.

---

## 12. Per-package TDD task breakdown (test-first; named security tests in **bold**)

### forge-config
- Add `AgentWeaveConfig.workload_listener_port`, `audit_backend`, `audit_path`; add `PeerAgent.spiffe_id`; un-inert `agentweave.enabled` for the workload plane while preserving the human-plane guardrail.
- Tests: `test_agentweave_config_workload_fields_parse`, `test_peer_agent_spiffe_id_optional`, **`test_agentweave_enabled_cannot_disable_human_oidc`** (setting `agentweave.enabled=false` leaves `auth.mode=enforce`/OIDC intact).

### forge-security (new `workload/` subpackage)
- `providers.build_workload_plane`, `resolver.resolve_workload_principal`, `authz.authorize_workload`, `mtls.server_ssl_context`/`PeerCertMiddleware`, `audit.build_audit_trail`; extend `Principal` (`kind="workload"`, `spiffe_id`).
- Tests: **`test_fake_svid_rejected`** (peer cert not chaining to the SPIRE bundle is refused; and a cert with no `spiffe://` SAN -> 401), **`test_unreachable_spire_fails_closed_workload_only`** (SPIFFE `initialize()` failure -> no workload plane built, human plane unaffected), **`test_opa_deny_returns_403`**, **`test_opa_unreachable_default_deny_403`** (`OPAProvider(default_deny=True)`), **`test_signature_verification`** (mTLS peer-SPIFFE match via `SecureChannel`; optional JWS verify if adopted), `test_workload_principal_has_spiffe_id_and_kind_workload`, **`test_human_resolver_cannot_produce_workload_principal`** (no input to `resolve_principal` yields `kind="workload"`).

### forge-gateway
- Start the `:8443` mTLS listener in lifespan (only when `agentweave.enabled`); mount the workload A2A router with `PeerCertMiddleware` -> `resolve_workload_principal` -> `authorize_workload`; emit audit; register a non-gating workload health component.
- Tests: **`test_human_path_unaffected`** (full existing OIDC/session/service-token suite passes; a `:8000` request never produces `kind="workload"`), `test_a2a_mtls_requires_client_cert`, `test_a2a_task_authorized_by_opa`, **`test_a2a_task_denied_by_opa_403`**, `test_workload_listener_absent_when_agentweave_disabled`, **`test_workload_health_does_not_gate_human_readiness`**, `test_audit_event_emitted_for_workload_authz`.

### forge-agent
- `PeerCaller` uses an mTLS client from `create_tls_context(server=False)` / `SecureChannel(peer_spiffe_id=...)`; drop the `caller_id` body field.
- Tests: `test_peer_call_uses_mtls_context`, **`test_peer_call_verifies_peer_spiffe_id`** (`PeerVerificationError` on mismatch), `test_peer_request_omits_caller_id`.

### deploy (chart render / policy tests)
- CSI mount + env + `:8443` port + new Service + OPA policy extension + values enable.
- Tests: `test_deployment_mounts_csi_spiffe_socket`, `test_spiffe_endpoint_socket_env_set`, **`test_a2a_mtls_service_not_on_httproute`** (the `:8443` Service is absent from every HTTPRoute `backendRef`), `test_opa_policy_allows_a2a_task_same_trust_domain`, **`test_opa_policy_default_deny`**.
