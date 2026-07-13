# ADR-0002: User-Issued API Keys (Self-Service Service Tokens after Dex Login)

**Date**: 2026-07-12
**Status**: Proposed
**Deciders**: Platform owner (AJ Geddes)
**Builds on**: [ADR-0001 — Dex OIDC Authentication](./0001-dex-oidc-authentication.md) (LIVE, `mode: enforce`)
**Depends on**: nothing new at the identity layer — a minted token flows through the *existing*
`resolve_principal` service-token path unchanged.

---

## 1. Context

### 1.1 The goal

Dex OIDC login is now live (ADR-0001): a user authenticates in the browser and the gateway
issues an encrypted `httpOnly` **session cookie** (BFF). That cookie is deliberately
**non-portable** — it cannot leave the browser session, which is exactly what makes it safe. But
that same property makes it useless for **machine use**: a CLI, an MCP client, or a CI job cannot
carry a browser cookie. Today the only machine credential is a **static service token whose
SHA-256 lives in `forge.yaml` in git** (ADR-0001 §5) — minting one is a human editing YAML,
committing, and waiting for an ArgoCD sync. That is fine for a break-glass admin token; it is
absurd as the path for a logged-in user who just wants a `forge_sk_…` for their laptop.

This ADR closes that gap: **a user, from an authenticated Dex session, mints a long-lived Forge
service token, sees the raw value exactly once, and uses it as
`Authorization: Bearer forge_sk_…`** on the API — with no git commit, no redeploy, and no new
identity-layer code.

### 1.2 What already exists (the foundation — not redesigned here)

| Fact | Detail | Source |
|---|---|---|
| Service-token verifier | `ServiceTokenVerifier(tokens)` — prefix check, `sha256(presented)`, constant-time `hmac.compare_digest` loop, `expires_at` check | `forge_security/oidc/service_tokens.py` |
| Resolver routing | `Bearer forge_sk_…` → service-token path → `Principal(kind="service", sub="svc:<id>", roles=…)`. **No change needed** | `forge_security/oidc/resolver.py` |
| Merge point | `app._init_auth`: `all_tokens = list(sec.service_tokens.tokens) + _resolve_legacy_service_tokens(config)` → `ServiceTokenVerifier(all_tokens)` | `forge_gateway/app.py:371` |
| Digest is not a secret | SHA-256 of a 256-bit random value is not brute-forceable — safe to persist in the clear | ADR-0001 §5 |
| Authorization | `Authorizer.permissions_for_roles(roles)` expands roles → permissions; deny-by-default | `forge_security/oidc/authorizer.py` |
| Choke point | Every protected route resolves via `security.get_principal`; `require_permission(p)` gates each route | `forge_gateway/security.py` |
| Auth routes precede SPA | `/v1/auth/me`, `/auth/*` are registered before the `/{path:path}` catch-all | `forge_gateway/routes/auth.py` |
| UI auth is stateless | No client-held token; `GET /v1/auth/me` *is* the state; `api.post` auto-attaches `X-CSRF-Token` | `forge-ui/src/api/auth.ts` |

### 1.3 The hard problem: durable persistence with no database

A **static** token's digest lives in git and is loaded at startup. A **user-minted** token's
digest is created at runtime and must **survive a pod restart** while being readable by
`ServiceTokenVerifier`. The deployment reality (verified against `values-hvs-k8s.yaml` and the
Helm chart):

- **No database.** Redis is deployed-but-unused; there is no wired datastore.
- **Single replica** (`agent.replicaCount: 1`, `autoscaling.enabled: false`, no PDB in the small
  profile). ADR-0001 already flags in-memory-per-pod state as a scaling hazard (the tx-replay
  note, §9.1).
- **`forge.yaml` is a read-only configMap mount** (`deployment.yaml:73`, `readOnly: true`) — the
  app cannot write there.
- **Secrets flow one-way**: OpenBao → External Secrets → k8s Secret → env. ESO sync is
  **read-only into the Secret**; the app has no write path back.
- **Pod runs as non-root** (uid/gid 999, `fsGroup: 999`).
- **Available durable primitives**: a Longhorn PVC (default storage class, RWO, replicated); a
  k8s Secret the app could `patch` (needs RBAC + a serviceaccount write grant); OpenBao writes
  (needs the app to hold its own write credential); Redis (needs wiring + a persistence story).

### 1.4 Scope

In scope: the persistence choice; the mint/list/revoke API; the anti-escalation rule; token
lifecycle and revocation; ownership/audit; the UI surface; the Helm/PVC change; rollout.

Out of scope: replacing the whole service-token model; multi-replica coordination (documented as
the scaling escape hatch, §4.6); per-tool scoping of minted tokens (a minted token carries a role
set, same granularity as ADR-0001); `last_used_at` tracking (§7.4, deferred to avoid a disk write
per request).

---

## 2. Decision Drivers

1. **Security first — this feature mints credentials.** The anti-escalation rule (§6) and
   revocation (§7) are load-bearing, not nice-to-haves. A leaked or over-scoped minted token is a
   real incident.
2. **Boring and auditable over clever.** Fewest moving parts; every rule expressible as a pytest.
3. **No new infrastructure and no new *write* credential.** A new long-lived write credential
   (OpenBao write token, or a serviceaccount with `patch secrets`) is itself an escalation
   primitive and a new thing to leak. Prefer a design that adds *no* new credential.
4. **Reuse the ADR-0001 identity layer unchanged.** A minted token must be indistinguishable to
   the resolver from a static one. No resolver change, no new `Principal.kind`.
5. **Fail closed and fail safe.** A corrupt or unavailable store must never (a) silently wipe
   tokens, (b) grant access it shouldn't, or (c) take down OIDC / static-token auth.
6. **Single-operator reality, with a documented path to multi-replica.**

---

## 3. Decision Summary

| # | Question | Decision |
|---|---|---|
| 1 | **Where do minted digests live?** | **A Longhorn PVC-backed append/rewrite JSON store** (`/app/data/user_tokens.json`), read at startup and mutated in-process. Rejected: k8s-Secret-patch, OpenBao-write, Redis (§4). |
| 2 | **At-rest format** | Only the **SHA-256 digest + metadata** — same non-secret property as static tokens. The raw token is never stored anywhere (§4.3). |
| 3 | **How the verifier sees both** | `ServiceTokenVerifier` gains an optional `store`; static tokens checked first (unchanged constant-time loop), then an O(1) digest-keyed lookup in the store's in-memory index. Live reference ⇒ mint/revoke are effective **immediately**, no reload (§4.4). |
| 4 | **Endpoints** | `POST /v1/auth/tokens` (mint, raw token returned **once**), `GET /v1/auth/tokens` (list own, metadata only), `DELETE /v1/auth/tokens/{id}` (revoke). Owner-scoped; admin may act across owners (§5). |
| 5 | **Anti-escalation** | A minted token's permissions **MUST be a subset** of the minter's current permissions, checked at the permission level. **Only `kind="user"` principals may mint** — a service token cannot mint another (§6). |
| 6 | **Lifecycle** | `forge_sk_<id>_<43-char b64url of 32 random bytes>`; user-minted tokens **must expire** (default 30 d, cap 90 d); revocation is immediate; roles are **frozen at mint** (§7). |
| 7 | **Deploy** | One Longhorn PVC (RWO, 1 Gi) + a writable `/app/data` mount; Deployment `strategy: Recreate` when persistence is on (RWO handoff). **No RBAC, no serviceaccount, no OpenBao change** (§9). |
| 8 | **Compat** | Static tokens unchanged; the dynamic store starts empty; `user_tokens.enabled` defaults **false** so no PVC-less deploy breaks (§10). |

---

## 4. Decision 1 — Persistence

### 4.1 Options considered

**Option A — Longhorn PVC-backed file store.** A JSON file on a replicated Longhorn volume,
mounted read-write at `/app/data`, loaded at startup and rewritten atomically on each mutation.

- **Pros**: durable (Longhorn replicates 3×, survives pod restart/reschedule); **adds no new
  credential** — a file the pod already has filesystem access to, no RBAC, no OpenBao write token;
  backed up by the platform's existing Longhorn snapshot/backup story; **blast radius is a file
  of digests** (non-secrets), so even a full volume compromise yields no usable tokens; fewest
  moving parts — stdlib `json` + `os.replace`, no client library, no network dependency on the
  request path.
- **Cons**: RWO binds to one node — breaks if `replicas > 1` (see §4.6); a rolling update needs
  `strategy: Recreate` to hand the volume over (§9); the app owns atomic-write + load correctness.

**Option B — App-patched k8s Secret.** The app calls the Kubernetes API to `patch` a Secret
holding the token records.

- **Rejected.** Requires granting the pod's serviceaccount **`patch`/`get` on Secrets** — an RBAC
  grant that is itself an escalation primitive (a compromised pod can then read/rewrite Secrets;
  scoping by `resourceNames` helps but is fragile). ESO already **owns** the `forge-ai-secrets`
  Secret — a second writer to a different Secret means two controllers touching the same resource
  class. Secrets are base64, **not encrypted**, cap at ~1 MB, and every write amplifies through
  etcd. More moving parts, larger blast radius, for no durability gain over a PVC.

**Option C — App writes to OpenBao.** The app holds an AppRole/token with write policy on
`kv/users/aj-geddes/app/forge-ai` and stores records there.

- **Rejected.** Requires the pod to hold a **new long-lived write credential to the secrets
  manager** — the single largest blast-radius increase on the table, and a new secret to rotate.
  ESO's sync is one-way (OpenBao → Secret); this would bypass it with a parallel write path.
  Storing **non-secret digests** in a secrets manager is over-engineering. Every mint becomes a
  network round-trip to OpenBao on the critical path.

**Option D — Redis.** Wire the deployed-but-unused Redis as the token store.

- **Rejected for now, but it is the multi-replica successor (§4.6).** Wiring it adds a stateful
  dependency on the auth path, a connection/auth story, and a **persistence configuration**
  (AOF/RDB) that must be verified durable — "deployed" ≠ "configured to survive a restart with
  our data intact." That is strictly more moving parts than a file for a single replica. Its one
  decisive advantage — shared state across pods — is worth nothing at `replicaCount: 1`.

### 4.2 Decision — Option A (Longhorn PVC file store)

**Chosen.** At single-replica scale, a replicated file of digests is the *least-moving-parts
durable option that adds no new credential and no new network dependency*. Every rejected
alternative buys durability we already have (Longhorn replicates) at the cost of a new write
credential (B, C) or a new stateful dependency (D). Driver #3 (no new write credential) is
decisive: this feature mints credentials, so the *last* thing it should do is introduce a fresh
high-value write credential into the pod.

The security posture is preserved end-to-end: **the file contains only SHA-256 digests +
metadata** — the exact same "not a secret" property that lets static token digests live in git
(ADR-0001 §5). A stolen `user_tokens.json` is as useless as a stolen `forge.yaml`.

### 4.3 On-disk / at-rest format

Single JSON document at `security.service_tokens.user_tokens.store_path`
(default `/app/data/user_tokens.json`), file mode `0600`:

```json
{
  "version": 1,
  "tokens": [
    {
      "id": "u_01J9ZC3K8Q",
      "secret_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      "owner_sub": "CgdhamdlZGRlcxIGZ2l0aHVi",
      "owner_email": "ageddes75@gmail.com",
      "label": "laptop CLI",
      "roles": ["user"],
      "created_at": "2026-07-12T10:00:00Z",
      "expires_at": "2026-08-11T10:00:00Z",
      "revoked_at": null,
      "revoked_by": null
    }
  ]
}
```

- `id` — server-generated, unique within the store (a ULID-like `u_<base32>`). It becomes
  `Principal.sub = "svc:u_01J9ZC3K8Q"` via the **unchanged** resolver, so the audit log names the
  token; the record links it to a human via `owner_sub`/`owner_email`.
- **The raw token is never stored** — only its `secret_sha256`. Mint is the one and only moment
  the raw value exists in the response; it is not recoverable afterward.
- Revocation is a **tombstone** (`revoked_at`/`revoked_by` set), not a deletion — retained for
  audit and for the admin "who revoked what" view. A future compaction job may purge tombstones
  and expired records past a retention window (noted as a follow-on; at single-operator scale the
  file stays tiny).

### 4.4 How `ServiceTokenVerifier` consults static **and** dynamic tokens

`ServiceTokenVerifier` gains an optional `store: UserTokenStore | None`. The verify order:

```
verify(presented) -> ServiceTokenPrincipalInfo
  1. prefix check: must start with "forge_sk_"          -> else 401 invalid_credential_format
  2. digest = sha256(presented).hexdigest()
  3. STATIC path (unchanged): loop static tokens, hmac.compare_digest(digest, cfg.secret_sha256)
        match -> expiry check -> return (token_id, roles)
  4. DYNAMIC path (only if no static match and store is present):
        record = store.get_by_digest(digest)            # O(1) dict lookup on the in-memory index
        record is None            -> 401 invalid_token
        record.revoked_at is set  -> 401 invalid_token
        record.expires_at passed  -> 401 token_expired
        else -> return (token_id=record.id, roles=record.roles)
  5. no match anywhere -> 401 invalid_token
```

**Why a dict lookup (not a constant-time loop) is safe for the dynamic path:** the lookup key is
the *digest*, and a digest is non-reversible (ADR-0001 §5). A timing side-channel that reveals
"this digest is present" leaks nothing an attacker can use — they still cannot produce the
256-bit preimage. This is exactly how GitHub validates hashed tokens by index. The static path
keeps its `hmac.compare_digest` loop unchanged (small list, no behavioural change, no risk).

The verifier holds a **live reference** to the store. Therefore a mint or revoke mutates the
in-memory index and the next request sees it **immediately** — no `_init_auth`, no config
reload, no restart. This is the property that makes revocation instant (§7.3).

`configure_auth`'s health check (`has_service_tokens`) is widened to
`bool(static tokens) or store is not None` — a machine-only deployment with only user tokens
still reports healthy. (In the live deploy OIDC is enabled, so `healthy` is already true.)

### 4.5 Concurrency and atomic write (single replica)

- One `asyncio.Lock` in the store serialises **read-modify-write** (mint/revoke). At
  `replicaCount: 1` there is exactly one writer process; the lock guards concurrent async tasks
  within it.
- Writes are atomic: serialise the full document to a temp file **in the same directory**,
  `flush()` + `os.fsync()`, then `os.replace(tmp, path)` (atomic rename on POSIX). A crash
  mid-write leaves either the old complete file or the new complete file — never a torn one.
- Readers (verify) never take the lock. Writers build the new in-memory index and **atomically
  swap the dict reference** (`self._index = new_index`); readers see either the old or the new
  dict, never a partially-mutated one (safe under the GIL for a single reference assignment).

### 4.6 Single-replica assumption and the scaling escape hatch

This is the same class of hazard ADR-0001 flagged (per-pod in-memory state, TD-001). Made
explicit here:

- **RWO breaks at `replicas > 1`.** A second pod on another node cannot mount the volume; two
  pods on the same node writing one file would race. **If `agent.replicaCount > 1` or
  `autoscaling.enabled: true`, this design is invalid as written.**
- **Mitigation by construction:** the store sits behind a narrow `UserTokenStore` protocol
  (`get_by_digest`, `list_for_owner`, `list_all`, `add`, `revoke`, `load`). Swapping the file
  implementation for a `RedisUserTokenStore` (Option D) is then a single localised change with no
  touch to the verifier, resolver, or endpoints.
- **Documented trigger (TD-006):** "Before enabling a second replica or the HPA, migrate
  `UserTokenStore` to Redis (Option D) and verify Redis persistence (AOF) is durable." A Helm
  guard should refuse to render `replicaCount > 1` while `persistence.enabled` and the file store
  is selected (fail loud, not silently corrupt).

### 4.7 Startup load and corruption handling

- At `_init_auth`, if `user_tokens.enabled`, the store is constructed and `load()` reads the file.
- **Missing file** ⇒ start with an empty store (first-ever mint creates it). Not an error.
- **Corrupt/unparseable file** ⇒ **fail safe, not fail silent**: log at ERROR, leave the file
  untouched (for forensics), and mark the store **unavailable**. Consequence: the token endpoints
  return `503 token_store_unavailable`, and the dynamic verify path is skipped — **static tokens
  and OIDC keep working** (fail-closed for the new feature, not for the whole gateway). It must
  never (a) delete a corrupt file or (b) start empty and thereby silently revoke every user
  token.

---

## 5. Decision 2 — Endpoints

All three are registered **before** the `/{path:path}` SPA catch-all (alongside the existing
`/v1/auth/me`). All resolve the caller via `get_principal` (so 401/403/429/503 semantics are
inherited from ADR-0001). `POST`/`DELETE` are cookie-authenticated state-changing calls from the
UI and therefore pass through the existing `CSRFMiddleware`; a bearer-authenticated (OIDC)
`kind="user"` caller is CSRF-exempt as usual.

### 5.1 `POST /v1/auth/tokens` — mint

**Request**
```json
{
  "label": "laptop CLI",           // required, 1-64 chars, human-readable
  "roles": ["user"],               // optional; default = the minter's current roles
  "expires_in_seconds": 2592000    // optional; default 30d; must be <= max (90d) and >= 1h
}
```

**Response — `201 Created` (the ONLY time the raw token is ever returned)**
```json
{
  "id": "u_01J9ZC3K8Q",
  "token": "forge_sk_u_01J9ZC3K8Q_9xQ7v1p...43chars",
  "label": "laptop CLI",
  "roles": ["user"],
  "created_at": "2026-07-12T10:00:00Z",
  "expires_at": "2026-08-11T10:00:00Z"
}
```

**Errors**: `400 invalid_label`; `400 ttl_out_of_range`; `400 unknown_role`;
`403 escalation_denied` (requested perms ⊄ minter perms, §6); `403 service_tokens_cannot_mint`
(caller `kind != "user"`); `403 forbidden` (caller has zero roles — an authenticated stranger);
`503 token_store_unavailable`.

### 5.2 `GET /v1/auth/tokens` — list (metadata only, never the secret)

**Response — `200`**
```json
{
  "tokens": [
    {
      "id": "u_01J9ZC3K8Q",
      "label": "laptop CLI",
      "roles": ["user"],
      "created_at": "2026-07-12T10:00:00Z",
      "expires_at": "2026-08-11T10:00:00Z",
      "revoked_at": null,
      "owner_email": "ageddes75@gmail.com"   // present only in the admin/all view
    }
  ]
}
```
Default scope = **the caller's own** tokens (matched on `owner_sub`). An **admin** (caller holds
the full permission set) may pass `?all=true` to list across all owners. **No secret, ever** —
there is no field that could carry it.

### 5.3 `DELETE /v1/auth/tokens/{id}` — revoke

**Response — `204 No Content`**. Effective **immediately** (§7.3).

- Owner may revoke their own token. An **admin** may revoke any token.
- A non-owner, non-admin caller targeting someone else's `id` gets **`404 not_found`** (not 403)
  — so token ids of other users cannot be probed by enumeration.
- Already-revoked or unknown `id` ⇒ `404 not_found` (idempotent-ish; no information leak).

---

## 6. Decision 3 — Anti-escalation and who may mint (LOAD-BEARING)

### 6.1 A minted token's permissions must be a subset of the minter's

The check is performed **at the permission level**, not the role level — roles are just permission
bundles, and comparing permissions handles the `admin`→`*` wildcard correctly:

```
requested_roles   = body.roles or minter.roles          # default: act-as-me
for r in requested_roles: assert r in cfg.authorization.roles      # else 400 unknown_role
requested_perms   = authorizer.permissions_for_roles(requested_roles)
if not requested_perms <= minter.permissions:                      # frozenset subset
    raise 403 escalation_denied
```

Consequences:
- A `user`-role caller (perms: `config:read, metrics:read, agent:invoke, tools:invoke`) may mint
  `["viewer"]` (subset) or `["user"]` (equal) but **not** `["admin"]` (`*` ⊄ user perms).
- An `admin` caller (holds `*`) may mint anything.
- A caller with **zero roles** (authenticated but unbound) cannot mint — their permission set is
  empty, so any non-empty request fails, and an empty request yields a useless token; the
  endpoint short-circuits with `403 forbidden`.

Note on drift: minted tokens store **roles** (consistent with static tokens), and roles are
expanded to permissions at *use* time against the *current* `authorization.roles`. If an admin
later redefines a role to hold more permissions, existing tokens of that role gain them — but
that is an admin/git act on config, identical to how static config tokens already behave, and is
out of an ordinary user's control. The anti-escalation *check* is a point-in-time bound at mint;
role definitions remain an admin responsibility.

### 6.2 Only `kind="user"` principals may mint — no chaining

A **service token cannot mint another token.** The endpoint rejects any caller whose
`principal.kind != "user"` with `403 service_tokens_cannot_mint`. Rationale: if a leaked service
token could mint fresh tokens, revoking the leaked one would not stop the attacker — they would
have minted replacements with independent lifetimes. Restricting minting to interactive user
principals (session cookie or OIDC bearer) means **every minted token traces back to a human Dex
login**, and cutting off that human (revoke + offboard) cuts off their ability to mint more.

### 6.3 What permission gates minting?

**None beyond being an authenticated user with at least one role** — deliberately. Minting a token
whose permissions are `⊆` your own grants **no new authority**; it is a second credential for
yourself, bounded by §6.1. Adding a dedicated `tokens:manage` permission was **considered and
rejected**: it would expand ADR-0001's *closed* permission set (forcing binding/validator churn)
without adding safety, since the anti-escalation subset check is the real control. **Admin-scope
operations** (`?all=true` listing, revoking another user's token) require the caller to hold the
**full permission set** (the `admin` role's `*`) — a role-name-independent, greppable check
(`principal.permissions >= ALL_PERMISSIONS`). If a future need for finer delegation appears,
adding `tokens:read`/`tokens:write` is a clean additive change; it is not warranted now.

---

## 7. Decision 4 — Token lifecycle

### 7.1 Format

```
forge_sk_<id>_<43-char base64url of 32 random bytes>
         └id┘ └──────────── 256 bits of entropy ────────────┘
```

- Prefix `forge_sk_` is **unchanged** — the resolver disambiguates on it (ADR-0001 §5.1).
- `<id>` is the server-generated store id (`u_<base32 ULID>`), guaranteed unique in the store and
  guaranteed **not** to collide with static token ids (a startup validator asserts the two id
  namespaces are disjoint; the `u_` prefix reserves the dynamic namespace).
- Secret = `secrets.token_urlsafe(32)` → 43 chars, 256 bits. Stored only as its SHA-256.

### 7.2 Expiry — user tokens MUST expire

Unlike static break-glass tokens (which may be non-expiring), **user-minted tokens are required to
expire** — bounded blast radius is the whole point:

| Knob (`security.service_tokens.user_tokens`) | Default | Meaning |
|---|---|---|
| `default_ttl_seconds` | `2592000` (30 d) | Applied when the request omits `expires_in_seconds`. |
| `max_ttl_seconds` | `7776000` (90 d) | Hard cap. A request above this ⇒ `400 ttl_out_of_range`. |
| (min) | `3600` (1 h) | A request below this ⇒ `400 ttl_out_of_range`. |

A request for a **non-expiring** user token is rejected. The cap is what bounds the persistence
window of the §7.5 offboarding limitation.

### 7.3 Revocation is immediate

`DELETE` writes the tombstone to disk (atomic, §4.5) and swaps the in-memory index in the same
locked operation. Because the verifier holds a **live reference** to the store, the **next request
carrying that token fails `401 invalid_token`** — no reload, no restart, no propagation delay.
**The store is the single source of truth**; there is no cache to invalidate elsewhere (single
replica). This is a strictly better revocation story than session cookies get (ADR-0001 §4.3
admits sessions cannot be revoked before expiry) — service tokens *can*.

### 7.4 `last_used_at` — deferred

Tracking last-use would mean a **disk write on every authenticated request** — contention on the
single-writer lock and PVC write amplification for a cosmetic field. **Omitted in v1.** A future
enhancement can update it asynchronously/throttled (e.g. at most once per minute per token) or
carry it in the observability layer instead. Noted, not built.

### 7.5 Fate of a token when the owner's access changes (a real security question)

**Decision: roles are frozen at mint; a minted token does NOT auto-revoke when the owner's
bindings change.** Two reasons this is the pragmatic choice:

1. Re-resolving the owner's *current* roles at token-use time would require their live `groups`
   claim, which we do not have without a fresh Dex login — we store only `owner_sub`/`owner_email`
   at mint. Group-based bindings simply cannot be re-evaluated offline. A partial re-check
   (sub/email bindings only) would be inconsistent and misleading.
2. Frozen roles are predictable and auditable.

**The accepted risk:** a de-provisioned user's minted tokens keep working until `expires_at` or
explicit revocation — which is in tension with ADR-0001's "offboarding a GitHub user offboards
them from Forge." **Mitigations, all present in this design:**
- Mandatory expiry with a **90-day cap** bounds the window.
- **Immediate admin revocation** (§7.3) is the offboarding lever.
- **TD-007 (offboarding runbook):** removing a user's access MUST include
  `GET /v1/auth/tokens?all=true` → `DELETE` each token with that `owner_sub`. A convenience
  admin endpoint `DELETE /v1/auth/tokens?owner=<sub>` (revoke-all-for-owner) is recommended as a
  fast-follow.
- Rotating the **session encryption key** (ADR-0001's "log everyone out" lever) does **not** cover
  service tokens — this is called out explicitly so no one assumes it does.

---

## 8. Decision 5 — Ownership and audit

Every record carries: `owner_sub` (stable primary key), `owner_email` (convenience/display),
`label`, `roles`, `created_at`, `expires_at`, `revoked_at`, `revoked_by`. List/revoke are
**owner-scoped** by `owner_sub`; admins (full permission set) may act across owners.

Audit (reusing ADR-0001's `AuditLogger`, principal-named, **never the raw token**):
- `token_minted` — `owner_sub`, `token_id`, `label`, `roles`, `expires_at`, request origin.
- `token_revoked` — `token_id`, `revoked_by` (the acting principal's sub), whether self or admin.
- At **use**, the existing per-request audit already logs `Principal.sub = svc:<token_id>`; the
  `token_id` correlates back to the record's `owner_*` for "who was this really."

---

## 9. Decision 6 — UI surface (BFF, session-cookie)

A new **"API Keys"** screen under the existing authenticated app shell (gated cosmetically on the
caller being an authenticated user, per the existing `useAuth`/`RequirePermission` pattern):

- **Mint**: a form (label + optional TTL + optional role multiselect constrained to the user's
  own roles). On submit → `api.post("/v1/auth/tokens", …)` (auto-attaches `X-CSRF-Token`,
  `credentials: "same-origin"`). The `201` response's raw `token` is shown **once** in a
  copy-to-clipboard modal with an explicit "you will not see this again" warning. **The raw token
  is never written to any store, TanStack Query cache, `localStorage`, or state that outlives the
  modal** — it lives only in the component's ephemeral render state and is dropped on close.
- **List**: `useQuery(["tokens"])` → `GET /v1/auth/tokens`, rendered as a table (label, roles,
  created, expires, status). Metadata only — the API never returns a secret to render.
- **Revoke**: a per-row button → `api.delete("/v1/auth/tokens/{id}")` with a confirm dialog;
  invalidate the `["tokens"]` query on success.
- Consistent with ADR-0001: **no client-held credential** — the session cookie does all the auth;
  the page never touches an `Authorization` header.

New files (illustrative): `forge-ui/src/features/tokens/ApiKeysPage.tsx`,
`forge-ui/src/api/tokens.ts`, a nav entry in `Sidebar.tsx`, and a route in `App.tsx`.

---

## 10. Decision 7 — Deploy

- **PVC**: a Longhorn `PersistentVolumeClaim`, `accessModes: [ReadWriteOnce]`,
  `storageClassName: longhorn`, `resources.requests.storage: 1Gi` (digests are tiny; 1 Gi is
  slack). New template `deploy/helm/forge/templates/pvc.yaml`, gated on `persistence.enabled`.
- **Mount**: a **writable** `volumeMount` at `/app/data` in `deployment.yaml` (the existing
  `config` mount is `readOnly` — the data volume is a *separate*, writable mount). `fsGroup: 999`
  is already set, and Longhorn honours `fsGroup`, so uid/gid 999 can write. **Human check (D-2):**
  confirm the Longhorn CSI provisioner applies `fsGroup` ownership (`fsGroupChangePolicy`) in this
  cluster; if not, an init step to `chown` the mount may be needed.
- **Rollout strategy**: set the Deployment `strategy.type: Recreate` **when `persistence.enabled`**
  — an RWO volume cannot be mounted by the new pod while the old pod still holds it during a
  rolling update. Recreate terminates the old pod first. (Alternative: convert to a StatefulSet;
  Recreate is the smaller change and sufficient at one replica.)
- **Values** (`values-hvs-k8s.yaml`): enable `persistence` and the `user_tokens` config block.
- **NO new RBAC, NO serviceaccount change, NO OpenBao path, NO ESO entry** — the payoff of the
  Option A choice (§4.2). This ships as a new image tag + a values change, cut over the **same
  ArgoCD GitOps way** as ADR-0001 (commit, push, sync; never `kubectl apply`).
- **Multi-replica guard** (§4.6): a `helm` template assertion (or CI check) that fails if
  `replicaCount > 1`/`autoscaling.enabled` while the file store is selected.

New config in `values-hvs-k8s.yaml` under `forgeConfig.security.service_tokens`:
```yaml
service_tokens:
  enabled: true
  tokens: [ ... ]                    # break-glass etc. — UNCHANGED
  user_tokens:
    enabled: true
    store_path: /app/data/user_tokens.json
    default_ttl_seconds: 2592000     # 30d
    max_ttl_seconds: 7776000         # 90d
```

---

## 11. Decision 8 — Rollout and compatibility

- **Static tokens are untouched.** The break-glass token and any CI tokens keep working exactly as
  today — they take the static path in the verifier, which is unchanged.
- **The dynamic store starts empty.** No migration, no backfill. First mint creates the file.
- **`user_tokens.enabled` defaults to `false`** in the schema, so a PVC-less deployment (local
  dev, the current live pod before the PVC lands) parses and runs unchanged — the feature is
  strictly opt-in and additive.
- **Rollout order**: (R1) merge code with `user_tokens.enabled: false` — zero runtime change.
  (R2) add the PVC + writable mount + `strategy: Recreate` to the chart, still
  `user_tokens.enabled: false` — the volume mounts but nothing uses it. (R3) flip
  `user_tokens.enabled: true` in `values-hvs-k8s.yaml`, bump `image.tag`, commit → ArgoCD sync.
  (R4) verify: log in, mint via the UI, `curl -H "Authorization: Bearer forge_sk_…"` a protected
  route, delete a pod, confirm the token still works (survives restart), then revoke and confirm
  it is rejected.
- **Rollback**: set `user_tokens.enabled: false` and/or revert `image.tag`. The PVC persists
  (Longhorn retains it); re-enabling later restores the same tokens.

---

## 12. Implementation Plan (TDD, per package)

Order: `forge-config` → `forge-security` → `forge-gateway` → `forge-ui` → `deploy`. Test-first;
the named cases are acceptance criteria. **Security-critical tests are marked ★.**

### 12.1 `forge-config`

**Tasks**: add `UserTokenConfig` (`enabled: bool = False`, `store_path: str`,
`default_ttl_seconds: int`, `max_ttl_seconds: int`) as a field on `ServiceTokenConfig`; validators
(`min ≤ default ≤ max`; `store_path` absolute).

**Tests** (`test_user_token_config.py`)
- `test_user_tokens_disabled_by_default`
- `test_default_ttl_must_not_exceed_max_ttl_raises_config_error`
- `test_store_path_must_be_absolute`
- `test_static_tokens_still_parse_unchanged` (regression — ADR-0001 config untouched)

### 12.2 `forge-security`

**Tasks**: new `forge_security/oidc/user_tokens.py` — `UserTokenRecord` (Pydantic v2),
`UserTokenStore` protocol + `FileUserTokenStore` (atomic write, `asyncio.Lock`, load,
corrupt-safe), digest-keyed in-memory index; extend `ServiceTokenVerifier` with an optional
`store` and the §4.4 dynamic path.

**Tests** (`test_user_token_store.py`)
- `test_add_then_get_by_digest_returns_record`
- ★ `test_minted_token_persists_across_store_reload` — write, construct a fresh store from the
  same path, token still resolves (**survives restart**)
- ★ `test_revoked_token_is_rejected` — tombstoned record → `get_by_digest` treats as revoked
- ★ `test_expired_record_is_rejected`
- `test_atomic_write_leaves_no_partial_file_on_simulated_crash` (write to temp + replace)
- ★ `test_concurrent_mint_and_revoke_do_not_corrupt_store` (interleaved tasks under the lock)
- ★ `test_corrupt_store_file_marks_unavailable_and_does_not_delete_it`
- `test_missing_store_file_starts_empty`
- `test_digest_index_swap_is_atomic_for_concurrent_readers`

**Tests** (`test_service_token_verifier_with_store.py`)
- ★ `test_verifier_accepts_both_static_and_dynamic_tokens`
- `test_static_token_still_verified_when_store_present` (static path unchanged)
- ★ `test_dynamic_revoked_token_rejected_401_invalid_token`
- ★ `test_dynamic_expired_token_rejected_401_token_expired`
- `test_dynamic_token_resolves_to_svc_prefixed_sub` (resolver contract unchanged)

### 12.3 `forge-gateway`

**Tasks**: `routes/tokens.py` (mint/list/revoke), registered before the SPA catch-all; wire the
store in `_init_auth` and pass it to `ServiceTokenVerifier`; a `TokenMinter` service holding the
anti-escalation logic; audit events.

**Tests** (`test_token_endpoints.py`)
- ★ `test_mint_with_permission_exceeding_owner_is_rejected_403_escalation_denied` — **cannot
  escalate** (user role minting `["admin"]`)
- `test_mint_with_subset_roles_succeeds`
- `test_mint_defaults_to_minter_roles_when_none_requested`
- ★ `test_service_token_principal_cannot_mint_403` — **no chaining**
- `test_unbound_user_with_zero_roles_cannot_mint_403`
- ★ `test_mint_returns_raw_token_once_and_list_never_returns_it` — **show-once**
- `test_requested_ttl_over_cap_rejected_400` / `test_requested_ttl_under_min_rejected_400`
- `test_default_ttl_applied_when_expires_in_omitted`
- `test_list_is_scoped_to_owner_by_default`
- `test_admin_can_list_all_with_all_true`
- ★ `test_user_cannot_revoke_another_users_token_returns_404` — **owner isolation**
- `test_admin_can_revoke_any_token`
- ★ `test_revoked_token_is_immediately_rejected_on_next_request` — no reload needed
- `test_mint_and_revoke_require_csrf_header_when_cookie_authenticated`
- `test_token_store_unavailable_returns_503_and_does_not_break_static_tokens`

**Tests** (`test_token_end_to_end.py`) — through the real resolver
- ★ `test_minted_token_authorizes_a_protected_route_as_service_principal`
- `test_minted_token_survives_a_simulated_pod_restart` (rebuild verifier from persisted file)

### 12.4 `forge-ui`

**Tasks**: `features/tokens/ApiKeysPage.tsx`, `api/tokens.ts`, nav + route.

**Tests** (vitest)
- `test_mint_shows_raw_token_once_in_modal`
- `test_raw_token_is_not_persisted_after_modal_closes`
- `test_role_selector_is_constrained_to_current_user_roles`
- `test_revoke_button_calls_delete_and_refreshes_list`
- `test_list_never_renders_a_secret_field`

### 12.5 `deploy`

**Tasks**: `templates/pvc.yaml`; writable `/app/data` mount; `strategy: Recreate` under
persistence; `values-hvs-k8s.yaml` enablement; multi-replica guard.

**Tests / checks**
- `helm template` renders a Longhorn RWO PVC and a writable `/app/data` mount when
  `persistence.enabled`.
- `helm template` sets `strategy.type: Recreate` when `persistence.enabled`.
- CI guard: `helm template` **fails** when `replicaCount > 1` (or `autoscaling.enabled`) while the
  file store is selected (§4.6).
- `helm template` renders **no** new RBAC/serviceaccount/OpenBao/ESO entries (asserts the
  Option-A "no new credential" property).

---

## 13. Consequences

**Positive**
- A logged-in user self-serves a machine credential in seconds — no git commit, no redeploy.
- Zero identity-layer change: minted tokens ride the **existing** `resolve_principal` service-token
  path; the resolver, `Principal`, and authorization are untouched.
- **Instant revocation** for service tokens (better than sessions, which cannot be revoked before
  expiry).
- **No new credential and no new network dependency** on the auth path (the whole point of the
  PVC choice) — smallest possible blast-radius increase for a credential-minting feature.
- At-rest data is digests-only: a stolen store file is as useless as a stolen `forge.yaml`.

**Negative / accepted limitations**
- **RWO ties this to a single replica** (§4.6). — **TD-006** (migrate to Redis before scaling).
- **Minted tokens survive owner offboarding** until expiry/revoke (§7.5). — **TD-007**
  (offboarding runbook + revoke-all-for-owner endpoint).
- **No `last_used_at`** in v1 (§7.4) — no "is this token still in use?" signal yet.
- **Corrupt store disables *minting*** (fail-closed for the feature) but is a manual-recovery
  event (§4.7).

**Risks and mitigations**

| Risk | Mitigation |
|---|---|
| A user mints a token above their own access | Permission-level subset check at mint; ★-tested (§6.1) |
| A leaked token mints replacements | Only `kind="user"` may mint — no chaining (§6.2) |
| Token id enumeration reveals others' tokens | Non-owner revoke/list returns `404`, never `403` (§5) |
| Pod restart loses tokens | Longhorn-replicated PVC; load at startup; ★-tested (§12.2) |
| Corrupt file silently revokes everyone | Fail-safe: mark unavailable, never wipe; static/OIDC unaffected (§4.7) |
| Someone enables a 2nd replica | Helm/CI guard fails the render (§4.6) |
| Offboarded user retains access via a token | Capped TTL + admin revoke + runbook (§7.5, TD-007) |

**Tech debt taken**: TD-006 (single-replica store), TD-007 (offboarding revocation). Both have a
trigger and an owner; neither is on the critical path of "users can mint a token."

---

## 14. Validation Criteria

Ship-blocking:
- [ ] ★ `test_mint_with_permission_exceeding_owner_is_rejected_403_escalation_denied` passes.
- [ ] ★ `test_service_token_principal_cannot_mint_403` passes.
- [ ] ★ `test_revoked_token_is_immediately_rejected_on_next_request` passes.
- [ ] ★ `test_expired_user_token_rejected` passes.
- [ ] ★ `test_minted_token_persists_across_store_reload` passes.
- [ ] ★ `test_user_cannot_revoke_another_users_token_returns_404` passes.
- [ ] ★ `test_mint_returns_raw_token_once_and_list_never_returns_it` passes.
- [ ] `grep` confirms the resolver, `principal.py`, and `authorizer.py` are unchanged by this ADR.
- [ ] `helm template` shows no new RBAC/serviceaccount/OpenBao entries; a Longhorn RWO PVC and a
      writable `/app/data` mount are present; `strategy: Recreate` is set.
- [ ] Coverage of `forge_security/oidc/user_tokens.py` ≥ 90 %.

Ongoing / reconsider when:
- Monitor `token_minted` / `token_revoked` audit rates for anomalies.
- **Reconsider this ADR when**: a second replica or the HPA is needed (TD-006 → Redis store); a
  shared cache/DB lands (revisit `last_used_at` and cross-pod revocation); or per-tool scoping of
  minted tokens is required (extend the record beyond a role set).

---

## 15. Human decisions / platform-side items required

- **D-1 (accept the offboarding limitation §7.5):** confirm that a capped-TTL + manual/admin-revoke
  offboarding story is acceptable, or prioritise the `revoke-all-for-owner` fast-follow (TD-007).
- **D-2 (Longhorn fsGroup):** confirm the Longhorn CSI provisioner applies `fsGroup: 999`
  ownership so uid/gid 999 can write `/app/data` (else add a `chown` init step). *Could not verify
  live — no cluster shell in this session.*
- **D-3 (PVC size/retention):** confirm 1 Gi and the Longhorn reclaim/backup policy for
  `user_tokens.json` (it should be included in the platform's Longhorn backup schedule).
- **D-4 (multi-replica trigger owner):** assign TD-006 an owner so the Redis migration precedes any
  scale-up rather than lagging it.
