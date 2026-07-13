# ADR-0001: Dex OIDC Authentication and Authorization for Forge AI

**Date**: 2026-07-12
**Status**: Proposed
**Deciders**: Platform owner (AJ Geddes)
**Supersedes**: the `security.jwt_secret` (HS256) and `security.api_keys` mechanisms
**Implements**: closure of a live unauthenticated deployment at `https://forgeai.hvslocal`

---

## 1. Context

### 1.1 The problem

Forge AI is deployed and serving at `https://forgeai.hvslocal`. It is **unauthenticated**. The
pod logs say so out loud:

```
SecurityGate not configured — running in DEVELOPMENT mode.
All agent routes allow unauthenticated access.
```

Anyone who can reach the VPN can `POST /v1/chat/completions` and drive the agent — which means
they can spend LLM tokens, invoke every tool on the tool surface (HTTP calls to whatever
OpenAPI sources are configured, with *Forge's* credentials), and talk to peer agents. The agent
is a confused deputy with no doorman.

### 1.2 Why it is open (root causes, verified in the code)

Four independent defects compound into "no auth at all":

1. **The gate fails open.** `forge_gateway/app.py::_init_security_gate` wraps construction in
   `try/except Exception` and calls `set_security_gate(None)` on *any* failure.
   `set_security_gate(None)` sets `_dev_mode = True`, and
   `forge_gateway/security.py::require_security` short-circuits:

   ```python
   if _dev_mode or _security_gate is None:
       return CallerIdentity(identity=_DEV_MODE_IDENTITY, dev_mode=True)
   ```

   An exception, a missing config, or `agentweave.enabled: false` all silently mean *no auth*.

2. **The live config disables it.** `deploy/helm/forge/values-hvs-k8s.yaml` sets
   `security.agentweave.enabled: false` (because there is no SPIRE/OPA), which is the exact
   branch that hands `None` to `set_security_gate`. It *also* sets
   `security.api_keys.enabled: false`, which means `require_admin_key` raises
   `403 "Admin API key authentication is not configured"` for **everyone**. So the true live
   posture is: **agent routes fully open, admin routes fully bricked.** The "static admin API
   key" is not protecting `/v1/admin/*` — nothing is reaching it at all, and the UI's
   `LoginPage` cannot succeed against this deployment.

3. **Authentication is voluntary.** Even when `jwt_secret` *is* configured,
   `forge_security/middleware.py::_verify_jwt` catches `jwt.exceptions.DecodeError` and
   **returns the raw string as the identity**. A caller who sends `X-Caller-ID: admin` — not a
   JWT, so it never decodes — is authenticated as `admin`. The auth check is opt-in *by the
   attacker*. `security.py::_extract_caller_id` cheerfully reads identity from a
   `caller_id` **query parameter**.

4. **The crypto is wrong for our IdP anyway.**
   `jwt.decode(token, self._jwt_secret, algorithms=["HS256"], options={"verify_aud": False})`
   is a symmetric shared secret with **audience validation disabled**. Dex issues **RS256**
   tokens verified against a JWKS. This code cannot validate a Dex token, and `verify_aud=False`
   means any token from any Dex client (e.g. ArgoCD's) would be accepted if it could validate
   at all — a cross-client token-replay hole waiting to happen.

Additionally: **AgentWeave is declared as a dependency of `forge-security` but never imported
anywhere.** Identity is always `MockIdentityProvider`; the OPA authorization provider is never
wired. The entire `agentweave` config block is decoration. **This design treats AgentWeave as
non-existent** and does not build on it.

### 1.3 What we have to work with (verified against the live cluster)

| Fact | Value |
|---|---|
| Discovery | `https://dex.hvslocal/dex/.well-known/openid-configuration` |
| `issuer` | `https://dex.hvslocal/dex` |
| `jwks_uri` | `https://dex.hvslocal/dex/keys` |
| Signing algs | `["RS256"]` — RS256 **only** |
| `response_types_supported` | `["code"]` — authorization code only |
| PKCE | `code_challenge_methods_supported: ["S256", "plain"]` |
| Scopes | `openid`, `email`, `groups`, `profile`, `offline_access` |
| Client storage | Kubernetes CRD (`oauth2clients.dex.coreos.com`, ns `dex`); also `dex-static-clients` / `dex-clients` Secrets |
| Upstream IdP | GitHub (`github-oauth` connector) — so `groups` are GitHub orgs/teams |
| Precedent | ArgoCD already federates to this Dex |

Forge-side constraints:

- The **same FastAPI app serves the React SPA and the API on port 8000, same origin**
  (`app.py` mounts `/assets` and a `/{path:path}` SPA fallback). This is decisive for Q1.
- Deployment is **ArgoCD GitOps** — commit and push, never `kubectl apply`.
- Secrets flow **OpenBao → External Secrets → k8s Secret → `envFrom` → env var**, and
  `forge-config`'s `EnvSecretResolver` reads `SecretRef{source: env, name: VAR}`. There is
  **no** resolver registered for `SecretSource.K8S_SECRET`, so env is the only working path.
- `security.allowed_origins` defaults to `["*"]` and `app.py` passes it to `CORSMiddleware`
  with `allow_credentials=True`. Starlette reflects the request origin in that combination.
  Today that is merely bad; **the moment we introduce a session cookie it becomes a
  full credential-bearing cross-origin hole.** It must be fixed as part of this work.

### 1.4 Scope

In scope: authentication and authorization for every externally reachable surface of the
gateway (conversational, programmatic, MCP, A2A, admin, metrics), the browser login flow, the
machine-client story, config schema, and the rollout.

Out of scope: tool-level (per-tool) authorization policy, multi-tenancy, outbound
credential delegation ("call this tool *as* the user"), and audit-log persistence. Each is
noted in §12 as a follow-on.

---

## 2. Decision Drivers

1. **Security first** — this is remediation of a live open endpoint. Prefer boring, auditable,
   standards-compliant mechanisms over clever ones.
2. **Fail closed** — no code path may degrade to "allow" on error.
3. **No new infrastructure** — Dex, OpenBao, ESO, ArgoCD exist. Redis, SPIRE, OPA do not.
   Do not require them.
4. **Do not brick local dev** — `uv run pytest` and a laptop `uvicorn` must still work.
5. **Machine clients must work** — MCP, A2A and CI cannot do an interactive browser flow.
6. **Single-operator reality** — this is a small platform. Two auth mechanisms are the budget.
7. **Testable** — every decision below must be expressible as a pytest case.

---

## 3. Decision Summary

| # | Question | Decision |
|---|---|---|
| 1 | Browser auth pattern | **BFF**: gateway is a *confidential* client, does code+PKCE server-side, issues an encrypted `httpOnly` session cookie. **No tokens in the browser.** |
| 2 | Machine clients | **Forge-issued service tokens** (`forge_sk_…`, SHA-256 hash in config, per-token roles). **Not** Dex client-credentials (Dex does not properly support it). |
| 3 | Authorization | **Role bindings** from `groups`/`email`/`sub` claims → roles → permissions. **Deny by default.** The shared static admin key is retired. |
| 4 | Fail-closed | Enforcement is a **declared mode**, not an accident of initialization. `mode: enforce` is the default; total auth failure ⇒ **unready + 503**, never "allow". `dev_insecure` requires config **and** an env var. |
| 5 | JWKS | Async TTL cache, `kid` lookup, rate-limited refresh on `kid` miss, stale-if-error grace. Validate **iss, aud, exp, nbf, nonce**; algorithm allow-list `["RS256"]`. |
| 6 | Config schema | New `security.auth`, `security.oidc`, `security.service_tokens`, `security.authorization` blocks. Two OpenBao keys. |
| 7 | Migration | `jwt_secret` **removed**; `api_keys` **deprecated with a shim**. Rollout is guarded by a break-glass service token. |

---

## 4. Decision 1 — Browser Auth: BFF, not a public SPA client

### Options

**Option A — SPA as a public client, authorization code + PKCE, tokens in the browser.**
The SPA redirects to Dex, exchanges the code with PKCE (no client secret), holds the `id_token`
/ `access_token` in memory (or worse, `localStorage`), and sends `Authorization: Bearer` on
every call. Refresh via `offline_access` + a rotating refresh token, or silent renewal in a
hidden iframe.

- **Pros**: the gateway stays stateless; no session cookie, therefore no CSRF class of bug;
  it is the pattern every SPA tutorial shows; the same bearer token works for `curl`.
- **Cons**:
  - **XSS ⇒ token exfiltration.** Forge's SPA renders *LLM output and tool results* — the
    single most attacker-influenceable content imaginable. A prompt-injected tool response that
    lands in the DOM is a plausible XSS vector. With tokens reachable from JS, one XSS yields a
    **portable bearer token** the attacker can use from their own machine, outside the browser,
    for the token's full lifetime. With `offline_access`, indefinitely.
  - **Refresh is genuinely hard in 2026.** Iframe-based silent renewal depends on third-party
    cookies. Refresh-token-in-browser is explicitly discouraged by
    *OAuth 2.0 for Browser-Based Applications*, which recommends a backend when one exists.
  - Requires an OIDC client library in the SPA (`oidc-client-ts`), token lifecycle code, and
    a request interceptor — all of it security-critical code we would own.

**Option B — Backend-for-Frontend (BFF).** The gateway is a **confidential** Dex client. The
browser never sees an OAuth token. `/auth/login` 302s to Dex; `/auth/callback` exchanges the
code **server-side** (client secret + PKCE); the gateway then sets its own **encrypted,
`httpOnly`, `Secure`, `SameSite=Lax`** session cookie. The SPA just calls the API — same origin,
cookie rides along, no `Authorization` header, no OIDC library.

- **Pros**:
  - **XSS cannot steal the credential.** An `httpOnly` cookie is unreadable from JS. XSS still
    permits *session riding* (the attacker's injected JS can call the API as the user from
    within that page), but the credential is **non-portable and non-durable** — it dies with
    the browser session and cannot be replayed from elsewhere. That is a material reduction in
    blast radius, and it is the reduction that matters here.
  - **The precondition is already satisfied.** The gateway *already serves the SPA on the same
    origin*. There is no CORS to negotiate, no cross-site cookie, no separate BFF service to
    deploy. The usual objection to BFF ("now I need another server") does not apply — the
    server is already there, already terminating the same hostname.
  - **The client secret means the client is confidential** — a stolen `client_id` alone cannot
    complete a code exchange.
  - **Zero OIDC code in the SPA.** The React app's entire auth surface becomes: "on 401,
    `window.location = '/auth/login'`" plus a `GET /v1/auth/me` for UI gating. That is a
    dramatic reduction in security-critical frontend code.
  - Refresh becomes a server-side concern, or is avoided entirely (see below).
- **Cons**:
  - Introduces **CSRF** as a risk class (ambient credentials always do). Mitigated below —
    and mitigated with mechanisms we must have regardless.
  - Requires a **session cookie key** shared across replicas, and a cookie
    encrypt/decrypt/rotate implementation.
  - Session revocation before expiry is not possible with a stateless cookie (see §4.3).

### Decision

**Option B — BFF.** The deciding argument is not abstract: the gateway *already is* the
same-origin backend for this SPA, so BFF costs us one route pair and a cookie codec, while the
public-client pattern costs us an OIDC library in the browser, a refresh-token lifecycle, and a
**portable credential sitting in JS reach of a UI that renders LLM output**. For a system whose
job is to execute tools on the user's behalf, an exfiltratable token is the wrong risk to take
to save a day of work.

### 4.1 Session cookie contents — and *no refresh tokens*

The session cookie is a **stateless, authenticated-encrypted blob** (Fernet / `MultiFernet`
from `cryptography`, already a `forge-security` dependency — AES-128-CBC + HMAC-SHA256, with a
built-in timestamp). It contains **only identity claims**:

```json
{
  "v": 1,
  "sub": "CgdhamdlZGRlcxIGZ2l0aHVi",
  "email": "ageddes75@gmail.com",
  "name": "AJ Geddes",
  "preferred_username": "aj-geddes",
  "groups": ["hvs-platform:admins"],
  "iat": 1783000000,
  "exp": 1783028800,
  "sid": "01J8…"
}
```

**We do not request `offline_access` and we do not store refresh tokens.** Rationale: the only
thing a refresh token buys is a longer session without a redirect — and because Dex maintains
its *own* SSO session, an expired Forge session is repaired by a **redirect that the user
experiences as a flicker**, not a re-login. Storing a long-lived refresh token in a cookie (or
anywhere) to avoid a 300 ms redirect once every 8 hours is a bad trade. Not requesting a scope
we do not need is the more defensible position at review time.

Consequence: the `id_token` is validated once at callback, its claims are copied into the
session, and it is **discarded**. Session lifetime is Forge's, not Dex's.

- **Absolute lifetime**: 8 h (`exp` in the blob, and `Max-Age` on the cookie).
- **Idle timeout**: 1 h — enforced by re-issuing the cookie with a sliding `iat` on each
  authenticated request, and rejecting when `now - iat > idle_timeout`.
- **Logout**: `POST /auth/logout` clears the cookie. Note Dex exposes **no
  `end_session_endpoint`** in its discovery document, so this is a *local* logout: the Dex SSO
  session survives, and clicking "Sign in" again re-authenticates without a password prompt.
  **This must be stated in the UI** ("Signed out of Forge") so it is not mistaken for a global
  logout. Documented limitation, not a bug.

### 4.2 CSRF

Three layers, defence in depth:

1. **`SameSite=Lax`** on the session cookie. This alone blocks cross-site `POST` — the entire
   state-changing API surface. It must be `Lax` and **not** `Strict`, because `Strict` would
   also suppress the cookie on the top-level GET navigation *back from Dex*, breaking the
   callback.
2. **Double-submit token.** A second, **non-`httpOnly`** cookie `forge_csrf` (random 32 bytes,
   set at login) must be echoed in an `X-CSRF-Token` header on every **cookie-authenticated
   non-safe method** (`POST`/`PUT`/`PATCH`/`DELETE`). Compared with `hmac.compare_digest`.
   Missing/mismatched ⇒ **403 `csrf_failed`**.
3. **Origin check.** For cookie-authenticated non-safe methods, require an `Origin` (or
   `Referer`) header whose origin is in `security.allowed_origins`. Absent/mismatched ⇒ 403.

**Bearer-authenticated requests (service tokens, OIDC bearer) skip CSRF entirely** — there is no
ambient credential, so there is nothing to forge.

**Hard prerequisite:** `CORSMiddleware` must never be configured with `allow_origins=["*"]`
while `allow_credentials=True`. A config validator (§8.4) rejects that combination at load
time.

### 4.3 Accepted limitation: no server-side session revocation

A stateless cookie cannot be revoked before `exp`. Mitigations: short lifetimes (8 h absolute /
1 h idle), and **rotating the session encryption key invalidates every session immediately** —
that is the break-glass "log everyone out" lever, executed by writing a new
`session_encryption_key` to OpenBao. Tracked as **TD-001** (§12): add a `sid` deny-list if and
when a shared cache exists. The `sid` claim is written into the cookie *now* precisely so that
this is a purely additive change later.

---

## 5. Decision 2 — Machine Clients: Forge-issued Service Tokens

MCP clients, A2A peers, and CI cannot complete a browser redirect. They need a
non-interactive credential.

### Options

**Option A — Dex client-credentials grant.** Rejected. Dex **does not properly support it**.
The maintainers' position is that Dex has no meaningful token to issue for a client with no
user, and the grant is gated behind an opt-in
(`DEX_CLIENT_CREDENTIAL_GRANT_ENABLED_BY_DEFAULT`) that is not enabled on this platform. The
verified discovery document advertises `response_types_supported: ["code"]`. Building the
security fix for a live open endpoint on top of a grant the IdP semi-supports is exactly the
"clever over boring" trade we said we would not make.

**Option B — Dex token-exchange / password grant.** Rejected. Token exchange needs a connector
and a subject token we do not have; the password grant is deprecated by OAuth 2.1 and would
require putting a GitHub-backed password somewhere. Neither is boring.

**Option C — Keep the existing `api_keys` mechanism as-is.** Rejected. It is a *shared* secret
with **no principal identity** (audit logs read "someone with the key"), **no scoping** (the key
is all-or-nothing), and no expiry. It is also currently disabled and non-functional.

**Option D — Forge-issued service tokens.** **Chosen.** An opaque, high-entropy bearer token
minted out-of-band, bound to a named principal with an explicit role set and optional expiry.

### Decision — Option D

**Token format** (prefix is load-bearing — it is how the resolver disambiguates credential
types without guessing):

```
forge_sk_<token_id>_<43 chars base64url of 32 random bytes>
   e.g.  forge_sk_ci-deployer_9xQ7v1p...   (256 bits of entropy)
```

**Storage**: the config stores the **SHA-256 hex digest** of the full token, *not* the token.

```yaml
service_tokens:
  enabled: true
  tokens:
    - id: ci-deployer
      description: "GitHub Actions deploy pipeline"
      secret_sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
      roles: ["admin"]
      expires_at: "2026-12-31T00:00:00Z"   # optional, RFC 3339
```

A SHA-256 digest of a 256-bit random value is **not a secret** — it is not brute-forceable and
carries no exploitable material. It can therefore live in `forge.yaml` **in git**, which means
service tokens are reviewable in a PR: adding a machine principal is a visible, diffable act.
This is strictly better than an opaque OpenBao blob. (Plain SHA-256 rather than bcrypt/argon2
is correct *only because* the input is full-entropy random and server-generated — this is the
same reasoning as GitHub PATs. A validator rejects any token config whose presented value is
not ≥ 32 bytes.) Verification is `hmac.compare_digest(sha256(presented).hexdigest(), configured)`.

Backward compatibility: `security.api_keys.keys` (`SecretRef`s) is still accepted for one
minor release and is internally translated into a synthetic service token
`id: legacy-api-key, roles: ["admin"]` with a **`DeprecationWarning` logged at every startup**.
See §11.

**Why not JWTs signed by Forge?** Because that means Forge owns a signing key, a rotation story,
and a JWKS of its own, to gain stateless verification we do not need at 60 rpm. Opaque tokens
with a hash comparison are ~15 lines and auditable at a glance.

**A2A peers**: peers authenticate with a service token carrying the `agent:peer` permission —
one token per peer, so the audit log names the peer. Mutual-TLS/SPIFFE would be better; it is
not available (SPIRE does not exist here), and it is recorded as a follow-on.

### 5.1 There is exactly one bypass-free credential resolver

This is the linchpin of the whole design. All three mechanisms funnel through **one** function
that resolves a request to a `Principal` or raises. **There is no fallthrough branch, ever.**

```
resolve_principal(request) -> Principal        # raises AuthError(401)

  1. If session cookie present  -> session path      (Principal.kind = "user")
  2. Elif Authorization: Bearer <t>:
       a. t startswith "forge_sk_"               -> service-token path  (kind = "service")
       b. t has 3 dot-separated segments (JWS)   -> OIDC bearer path    (kind = "user")
       c. otherwise                              -> 401 invalid_credential_format
  3. Else -> 401 missing_credentials
```

**`X-Caller-ID` and the `caller_id` query parameter are deleted as authentication inputs.**
They are the current bypass and they have no legitimate use. (`X-Caller-ID` may survive as a
*correlation* hint written to logs, but it may never influence identity. Preferably delete it.)

---

## 6. Decision 3 — Authorization: Claims → Roles → Permissions

Authentication answers *who*. It must not be mistaken for *may*. Being an authenticated GitHub
user on this Dex is not remotely the same as being allowed to drive the agent — Dex will
happily authenticate anyone the GitHub connector accepts.

### 6.1 Permissions (closed set)

| Permission | Guards |
|---|---|
| `agent:invoke` | `routes/conversational.py`, `routes/programmatic.py` — chat + completions + runs |
| `tools:invoke` | the `/mcp` mount (`security.py::enforce_asgi_security`) |
| `agent:peer` | `routes/a2a.py` (task endpoints; the agent-card discovery endpoint stays public) |
| `config:read` | `GET` on `routes/admin.py`, `routes/persona.py` |
| `config:write` | mutating methods on `routes/admin.py`, `routes/persona.py` |
| `metrics:read` | `routes/metrics.py` (see §6.5) |

`/health/*` is **always public** — kubelet probes must not carry credentials.

### 6.2 Roles (built-in defaults, overridable)

```yaml
roles:
  viewer: ["config:read", "metrics:read"]
  user:   ["config:read", "metrics:read", "agent:invoke", "tools:invoke"]
  admin:  ["*"]
```

### 6.3 Bindings: claims → roles

```yaml
bindings:
  - role: admin
    groups: ["hvs-platform:admins"]
    emails: ["ageddes75@gmail.com"]
  - role: user
    groups: ["hvs-platform:engineers"]
```

Semantics:

- A principal receives the **union** of roles from **every** binding it matches; its permission
  set is the union of those roles' permissions.
- Matching is **exact string, case-sensitive** for `groups` and `subs`.
  **`emails` match case-insensitively** (RFC 5321 mailboxes are case-insensitive in practice and
  IdPs are inconsistent) — and email is only usable as an identifier at all because this Dex's
  upstream is a single GitHub org with verified addresses. `sub` is the stable primary key;
  `email` is a **convenience** binding, and this is called out in §12 as a residual risk if the
  upstream connector ever changes.
- **A principal matching no binding has zero permissions and receives `403 forbidden`** — it is
  authenticated but not authorized. `default_role` exists in the schema but defaults to `null`
  (deny). Setting `default_role: user` is a deliberate, greppable act of trusting everyone Dex
  authenticates.
- **Service tokens declare their roles inline** and do not participate in binding evaluation.

### 6.4 Group-string format — an operational unknown you must close

Dex's GitHub connector emits groups as `org` or `org:team` depending on its `orgs`/`teams`
configuration. **We must not guess.** Mitigation is built into the design: the
`GET /v1/auth/me` endpoint returns the caller's **raw claims alongside their resolved roles and
permissions**:

```json
{
  "kind": "user",
  "sub": "CgdhamdlZGRlcxIGZ2l0aHVi",
  "email": "ageddes75@gmail.com",
  "preferred_username": "aj-geddes",
  "groups": ["hvs-platform:admins"],
  "roles": ["admin"],
  "permissions": ["*"]
}
```

**Rollout step R4 (§11) is: log in once, read `/v1/auth/me`, and write the *observed* group
strings into `bindings`.** Until that is done, bind `admin` by `emails` (or by `subs`), which is
knowable in advance. This is why `emails` exists in the schema at all — it is the bootstrap.

### 6.5 `/metrics` — an honest compromise

The pod is annotated `prometheus.io/scrape: "true"` and Prometheus scrapes **pod IP:8000**
directly, bypassing the HTTPRoute. If we require `metrics:read` on `/metrics`, scraping breaks.
Decision: `security.authorization.metrics_public` (default `true`) leaves `/metrics`
unauthenticated **on the pod port**, and the **HTTPRoute is amended to not expose `/metrics`
publicly** (a path-based rule). This is recorded as a known exposure with a follow-on to move
metrics to a second container port. Do not gold-plate this in the first pass; do not silently
ignore it either.

### 6.6 Fate of the static admin key

**Retired as a human credential.** Admin UI access becomes OIDC + the `admin` role, full stop.
The concept survives only as a **service token with explicit roles** for CI. A single
break-glass admin service token is minted during rollout and stored in OpenBao — it is the
recovery path if OIDC itself breaks (§11), and it is the reason we can safely default to
`enforce`.

---

## 7. Decision 4 — Fail Closed

The current failure mode is not a bug in a branch; it is a **category error**: enforcement is
*inferred* from whether initialization happened to succeed. It must instead be **declared** and
then satisfied — a system that cannot enforce what it declared must refuse to serve, not quietly
serve without it.

### 7.1 Enforcement mode is declared, not inferred

```yaml
security:
  auth:
    mode: enforce        # enforce | dev_insecure   (DEFAULT: enforce)
```

`enforce` is the **schema default**. A `forge.yaml` with no `security` block at all is
`enforce`. **The absence of configuration can never mean the absence of authentication.** This
single inversion is the most important line in this ADR.

### 7.2 `dev_insecure` requires two independent switches

`dev_insecure` activates **only if both** are true:

1. `security.auth.mode: dev_insecure` in the config file, **and**
2. environment variable **`FORGE_DEV_INSECURE=1`** is set in the process environment.

Why both: a config file can be copied, templated, or accidentally deployed. The env var is set
by a human in a shell or a `docker run -e` — and the Helm chart **never renders it**
(enforced by a `helm template | grep` assertion in CI, §10.5). Neither switch alone opens the
door.

> Note: an earlier draft guarded this with `metadata.environment == "development"`. That guard
> is **rejected**: the live `values-hvs-k8s.yaml` literally sets `environment: development`, so
> it would have permitted `dev_insecure` in the cluster. It is an untrustworthy signal.

When active, `dev_insecure`:
- authenticates every request as `Principal(kind="dev", sub="dev-anonymous", roles=["admin"])`;
- logs `CRITICAL` at startup and **every 60 s thereafter**;
- sets `X-Forge-Insecure-Mode: true` on **every response**;
- reports `auth_mode: dev_insecure` in `GET /health/ready` **and** in the UI as a red banner.

It should be impossible to be in this mode and not know it.

### 7.3 The failure matrix — exactly what happens, and what is returned

`enforce` mode. "Auth subsystem" = OIDC verifier + service-token verifier.

| Condition | Readiness | Response | Status | Body (`error`) |
|---|---|---|---|---|
| **No credentials** presented | ready | deny | **401** | `missing_credentials` + `WWW-Authenticate: Bearer` |
| Credential shape unrecognised | ready | deny | **401** | `invalid_credential_format` |
| Session cookie fails decrypt/auth-tag | ready | deny + **clear cookie** | **401** | `invalid_session` |
| Session past absolute `exp` or idle window | ready | deny + clear cookie | **401** | `session_expired` |
| Service token unknown / hash mismatch | ready | deny | **401** | `invalid_token` |
| Service token past `expires_at` | ready | deny | **401** | `token_expired` |
| Bearer JWT: bad signature | ready | deny | **401** | `invalid_token` |
| Bearer JWT: `alg` not in `["RS256"]` | ready | deny | **401** | `invalid_token` (alg rejected **before** any key lookup) |
| Bearer JWT: `iss` ≠ configured issuer | ready | deny | **401** | `invalid_issuer` |
| Bearer JWT: `aud` lacks our `client_id` | ready | deny | **401** | `invalid_audience` |
| Bearer JWT: expired / not-yet-valid (60 s leeway) | ready | deny | **401** | `token_expired` |
| Bearer JWT: no `kid` in header | ready | deny | **401** | `invalid_token` |
| Bearer JWT: `kid` unknown **after** a forced JWKS refresh | ready | deny | **401** | `unknown_key` |
| **JWKS unreachable, warm cache present** | ready | **allow** (verify against cached keys, up to `stale_grace`) | — | metric `forge_jwks_stale=1`, WARN |
| **JWKS unreachable, warm cache present, past `stale_grace`** | ready | deny | **503** | `identity_provider_unavailable` |
| **JWKS unreachable, cache cold** (startup) | **ready** (service tokens still work) | deny **OIDC paths only** | **503** | `identity_provider_unavailable` |
| Authenticated, but principal has **no matching binding** | ready | deny | **403** | `forbidden` (`no_role_binding`) |
| Authenticated + role, but role lacks the permission | ready | deny | **403** | `forbidden` (`insufficient_permission`) |
| CSRF token missing/mismatched (cookie auth, unsafe method) | ready | deny | **403** | `csrf_failed` |
| Rate limit exceeded | ready | deny | **429** | `rate_limited` |
| **`enforce` and NO auth mechanism can operate** (no OIDC config *and* no service tokens; or session key unresolvable) | **NOT READY** | deny **everything** except `/health/*` | **503** | `auth_unavailable` |

Design notes on the interesting rows:

- **JWKS unreachable with a warm cache ⇒ keep verifying.** RSA public keys do not become
  invalid because our network blipped. Failing valid users during a Dex outage would be
  fail-*closed* in the letter and self-harm in the spirit. The `stale_grace` bound (24 h)
  keeps it honest, and the metric makes it visible.
- **Infrastructure failure returns 503, never 401.** A 401 asserts "*your* credential is bad" —
  it is a lie when the truth is "*our* IdP is unreachable", and it sends users into a login loop
  that hammers a service that is already down. This distinction must be tested.
- **Total auth failure ⇒ readiness `false`, not process exit.** The pod stays alive (liveness
  green) so its logs are readable, but it is removed from the Service endpoints. Crucially, in a
  rolling update the **new pod never becomes Ready, so the old pod keeps serving** and the bad
  config never takes traffic. Fail-closed *and* non-destructive. Exiting the process would give
  a `CrashLoopBackOff` with the same safety but worse forensics.
- **`enforce` + OIDC absent + service tokens present** is a **legal, non-degraded** state: a
  machine-only deployment. It is not a silent bypass — the operator declared it.

### 7.4 The code paths that must be deleted

Non-negotiable, and each gets a regression test:

1. `security.py`: `if _dev_mode or _security_gate is None: return CallerIdentity(...)` — **gone**.
2. `app.py::_init_security_gate`: `except Exception: … set_security_gate(None)` — **gone**.
   Replaced by: record the failure, mark the subsystem unhealthy, deny.
3. `middleware.py::_verify_jwt`: `except DecodeError: return token` — **gone**. A credential
   that does not parse is a 401, not an identity.
4. `security.py::_extract_caller_id`: the `X-Caller-ID` header and `caller_id` query-param
   branches — **gone**.

---

## 8. Decision 5 & 6 — JWKS Verification and Config Schema

### 8.1 JWKS cache

`forge_security.oidc.JwksCache` — **async** (`httpx.AsyncClient`). PyJWT's `PyJWKClient` is
**rejected**: it uses blocking `urllib` (it would stall the event loop on every refresh inside
an async request handler), and its `cache_keys=True` LRU has no expiry and is
[known to serve revoked keys indefinitely](https://github.com/jpadilla/pyjwt/issues/1051). We
use **PyJWT for the JWS/claims verification** (which is the part that must be someone else's
well-reviewed code) and own the ~80-line cache (which is the part that must be async and
correct for *our* failure model).

| Parameter | Default | Purpose |
|---|---|---|
| `jwks_cache_ttl_seconds` | 300 | Normal refresh interval. |
| `jwks_min_refresh_seconds` | 30 | **Floor between forced refreshes.** Without it, an unauthenticated attacker sending tokens with random `kid`s makes us hammer Dex — a trivial amplification DoS. This bound is why the `kid`-miss refresh is safe. |
| `jwks_stale_grace_seconds` | 86400 | Serve stale keys this long past a failed refresh before returning 503. |
| `clock_skew_seconds` | 60 | `leeway` for `exp`/`nbf`/`iat`. |

Behaviour: warm at startup (failure is non-fatal — see the matrix); refresh lazily on TTL
expiry; on `kid` miss force **one** refresh subject to `min_refresh_seconds`, then give up with
`unknown_key`. This handles Dex key rotation with no restart and no operator action.

### 8.2 Token verification algorithm (normative — implement exactly this order)

```
verify_oidc_token(token: str) -> Claims

 1. Parse the JOSE header WITHOUT verifying.                    → fail: 401 invalid_token
 2. header["alg"] MUST be in cfg.allowed_algorithms (["RS256"]).→ fail: 401 invalid_token
    ── Do this BEFORE touching a key. Never let the token choose its own algorithm:
       that is the `alg: none` / HS-vs-RS confusion attack. Passing algorithms=["RS256"]
       to jwt.decode() is what actually enforces it; step 2 is belt-and-braces and gives
       a clean, testable rejection.
 3. header["kid"] MUST be present.                              → fail: 401 invalid_token
 4. key = jwks_cache.get(kid)
      cold cache + fetch fails                                  → 503 identity_provider_unavailable
      kid miss  → force refresh (rate-limited) → still miss     → 401 unknown_key
      refresh fails but cached keys are within stale_grace      → proceed with cached keys
      refresh fails and stale_grace exceeded                    → 503 identity_provider_unavailable
 5. jwt.decode(
        token, key,
        algorithms=cfg.allowed_algorithms,          # ["RS256"] — allow-list, not header-driven
        issuer=cfg.issuer,                          # exact match "https://dex.hvslocal/dex"
        audience=cfg.audience,                      # our client_id  ← the current verify_aud=False bug
        leeway=cfg.clock_skew_seconds,
        options={"require": ["exp", "iat", "iss", "aud", "sub"],
                 "verify_signature": True, "verify_exp": True, "verify_nbf": True,
                 "verify_iat": True, "verify_aud": True, "verify_iss": True},
    )
      InvalidSignatureError  → 401 invalid_token
      ExpiredSignatureError  → 401 token_expired
      InvalidIssuerError     → 401 invalid_issuer
      InvalidAudienceError   → 401 invalid_audience
      MissingRequiredClaim   → 401 invalid_token
      ANY other exception    → 401 invalid_token      ← there is NO branch that returns an identity
 6. If claims["aud"] is a list, claims["azp"] (when present) MUST equal cfg.client_id.
 7. AUTH-CODE FLOW ONLY: claims["nonce"] MUST equal the nonce from the transaction cookie.
                                                               → fail: 401 invalid_nonce
    (Not applicable to bearer-token verification — there is no nonce to bind to.)
 8. Return claims.
```

Everything is a rejection. **There is no path through this function that returns an identity it
did not cryptographically verify.** That sentence is the entire difference from today's code.

### 8.3 The new config schema

Additive to `forge_config.schema.SecurityConfig`:

```yaml
security:
  auth:
    mode: enforce                 # enforce | dev_insecure. DEFAULT: enforce.

  oidc:
    enabled: true
    issuer: "https://dex.hvslocal/dex"
    client_id: "forge-ai"
    client_secret:                             # SecretRef -> OpenBao -> ESO -> env
      source: env
      name: FORGE_OIDC_CLIENT_SECRET
    audience: "forge-ai"                       # defaults to client_id when omitted
    scopes: ["openid", "email", "profile", "groups"]   # NOTE: no offline_access (§4.1)
    redirect_uri: "https://forgeai.hvslocal/auth/callback"
    post_login_default_path: "/"

    discovery: true                            # fetch .well-known at startup
    # Explicit overrides; used when discovery=false or to pin endpoints.
    authorization_endpoint: null
    token_endpoint: null
    jwks_uri: "https://dex.hvslocal/dex/keys"

    allowed_algorithms: ["RS256"]
    clock_skew_seconds: 60
    jwks_cache_ttl_seconds: 300
    jwks_min_refresh_seconds: 30
    jwks_stale_grace_seconds: 86400

    accept_bearer_tokens: true                 # accept Dex id_tokens as Authorization: Bearer

    session:
      cookie_name: "forge_session"
      encryption_key:                          # SecretRef -> 32-byte urlsafe-b64 (Fernet key)
        source: env
        name: FORGE_SESSION_KEY
      previous_encryption_key: null            # SecretRef; enables zero-downtime key rotation
      lifetime_seconds: 28800                  # 8 h absolute
      idle_timeout_seconds: 3600               # 1 h sliding
      same_site: "lax"                         # lax | strict — "none" is REJECTED by validator
      secure: true                             # forced true unless mode == dev_insecure
      csrf_cookie_name: "forge_csrf"

  service_tokens:
    enabled: true
    tokens:
      - id: "ci-deployer"
        description: "GitHub Actions deploy pipeline"
        secret_sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        roles: ["admin"]
        expires_at: null                       # RFC 3339, optional
      - id: "break-glass"
        description: "Recovery credential — OIDC-independent. Rotate after use."
        secret_sha256: "…"
        roles: ["admin"]

  authorization:
    default_role: null                         # null == deny-by-default. Do not change lightly.
    metrics_public: true                       # see §6.5
    roles:                                     # merged over the built-ins
      viewer: ["config:read", "metrics:read"]
      user:   ["config:read", "metrics:read", "agent:invoke", "tools:invoke"]
      admin:  ["*"]
    bindings:
      - role: admin
        groups: []                             # fill in from /v1/auth/me after first login (§6.4)
        emails: ["ageddes75@gmail.com"]        # bootstrap binding
        subs: []
      - role: user
        groups: ["hvs-platform:engineers"]

  rate_limit_rpm: 60
  allowed_origins: ["https://forgeai.hvslocal"]   # "*" now REJECTED when session auth is on

  # --- DEPRECATED (§11) ---
  api_keys: { enabled: false, keys: [] }       # accepted for one minor; warns; → synthetic admin token
  jwt_secret: null                             # REMOVED. Presence is a hard config error.
  agentweave: { ... }                          # inert; retained so old configs still parse
```

### 8.4 Config validators (these are `model_validator`s, and each gets a test)

1. `auth.mode == enforce` **and** `oidc.enabled == false` **and** `service_tokens` empty
   ⇒ **`ConfigError`** at load. ("You declared enforce and gave me nothing to enforce with.")
2. `oidc.enabled` **and** `session.secure == false` **and** `mode != dev_insecure` ⇒ `ConfigError`.
3. `session.same_site == "none"` ⇒ `ConfigError` (would defeat the primary CSRF control).
4. `"*" in allowed_origins` **and** `oidc.enabled` ⇒ **`ConfigError`**. A wildcard origin with
   `allow_credentials=True` reflects any origin — with cookies, that is a total compromise.
5. `security.jwt_secret` present ⇒ `ConfigError` with an explicit migration message.
6. `bindings[*].role` must exist in `roles`; `roles[*]` permissions must be in the known set
   (or `*`) ⇒ typo-proofing. A misspelled permission must never fail *open*.
7. `service_tokens.tokens[*].secret_sha256` must match `^[0-9a-f]{64}$`.
8. `oidc.issuer` must be `https://` (unless `mode == dev_insecure`).

### 8.5 Secrets: OpenBao → ESO → env

Two new keys at OpenBao path `kv/users/aj-geddes/app/forge-ai`:

| OpenBao property | ESO `secretKey` (env var) | Value |
|---|---|---|
| `oidc_client_secret` | `FORGE_OIDC_CLIENT_SECRET` | The Dex client secret. Must be byte-identical to the one registered with Dex. |
| `session_encryption_key` | `FORGE_SESSION_KEY` | `Fernet.generate_key()` — 32 random bytes, urlsafe-base64. **Must be identical across replicas** (hence OpenBao, not a per-pod generated value). |
| `session_encryption_key_previous` | `FORGE_SESSION_KEY_PREVIOUS` | Optional. Enables `MultiFernet` rotation with no forced logout. |
| `break_glass_token` | *(not mounted)* | The plaintext break-glass service token. Stored **for the human**, never read by the pod — the pod only ever sees its SHA-256, which lives in `forge.yaml`. |

`deploy/helm/forge/values-hvs-k8s.yaml` `externalSecret.data` gains entries for the first three
(remember: `remoteRef.key` omits the `kv/` prefix — `users/aj-geddes/app/forge-ai`).

**No secret ever enters git or the image.** The only auth material in git is a SHA-256 digest
(§5), which is by construction not a secret.

---

## 9. The Flows

### 9.1 Browser login (numbered, normative)

1. Unauthenticated browser requests `GET /config`. The SPA shell loads (static assets are
   public) and calls `GET /v1/auth/me`.
2. Gateway finds no session cookie → **401** `{"error": "missing_credentials"}`.
3. SPA sees 401 → `window.location.assign("/auth/login?next=/config")`.
4. `GET /auth/login`:
   a. Validate `next` is a **relative path** (`^/[A-Za-z0-9/_-]*$`, no `//`, no scheme).
      Anything else → use `post_login_default_path`. *(Open-redirect defence.)*
   b. Generate `state` (32 random bytes), `nonce` (32 random bytes), `code_verifier`
      (43–128 chars).
   c. Set a **transaction cookie** `forge_oidc_tx` — encrypted, `httpOnly`, `Secure`,
      `SameSite=Lax`, `Max-Age=600`, `Path=/auth` — containing `{state, nonce, code_verifier,
      next}`. *(Stateless ⇒ works across replicas with no shared store. `Lax` is required so
      the cookie survives the top-level GET redirect back from Dex.)*
   d. **302** to
      `https://dex.hvslocal/dex/auth?response_type=code&client_id=forge-ai&redirect_uri=…&scope=openid+email+profile+groups&state=…&nonce=…&code_challenge=S256(verifier)&code_challenge_method=S256`.
5. Dex authenticates the user against GitHub (or reuses its existing SSO session) and
   **302**s to `https://forgeai.hvslocal/auth/callback?code=…&state=…`.
6. `GET /auth/callback`:
   a. Decrypt `forge_oidc_tx`. Absent/undecryptable/expired → **400** `invalid_transaction`.
   b. `hmac.compare_digest(query.state, tx.state)` → mismatch = **400** `state_mismatch`.
   c. Exchange the code at Dex's `token_endpoint`: `grant_type=authorization_code`,
      `code`, `redirect_uri`, `code_verifier`, and **client authentication** with
      `client_id` + `client_secret` (HTTP Basic). Network/4xx → **502** `token_exchange_failed`.
   d. Verify the returned `id_token` with §8.2 **including step 7** (`nonce` == `tx.nonce`).
   e. Extract `sub`, `email`, `name`, `preferred_username`, `groups`.
   f. Resolve roles via `bindings`. **If zero roles → 403 `forbidden`, and do not set a session
      cookie.** (An authenticated stranger gets no session. Note this deliberately makes the
      "you have no access" experience a 403 page, not a login loop.)
   g. Set `forge_session` (encrypted, `httpOnly`, `Secure`, `SameSite=Lax`, `Max-Age=28800`,
      `Path=/`) and `forge_csrf` (random, **readable by JS**, same lifetime).
   h. **Delete** `forge_oidc_tx` (`Max-Age=0`) — single-use.
   i. **302** to `tx.next`.
7. SPA re-calls `GET /v1/auth/me` → 200 with claims + roles + permissions. The SPA renders nav
   from `permissions` (UI gating is **cosmetic**; the server is the authority).
8. Every subsequent API call carries the cookie automatically (same origin). Non-safe methods
   also send `X-CSRF-Token` from the `forge_csrf` cookie.

**Session expiry**: any API call returns 401 `session_expired` → SPA redirects to
`/auth/login?next=<current path>` → Dex's own SSO session is still valid → user is bounced
straight back with a new session. Perceived cost: a page flicker.

### 9.2 Machine client

```bash
curl -H "Authorization: Bearer forge_sk_ci-deployer_9xQ7v1p…" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"hi"}]}' \
     https://forgeai.hvslocal/v1/chat/completions
```

Resolver → prefix `forge_sk_` → SHA-256 → constant-time compare → `expires_at` check →
`Principal(kind="service", sub="svc:ci-deployer", roles=["admin"])` → permission check →
audit. No cookie, no CSRF, no browser.

MCP clients set the same header; `/mcp` requires `tools:invoke`. A2A peers likewise, requiring
`agent:peer`.

### 9.3 Dex client registration (**platform-side change — requires a human**)

⚠️ **Landmine, read before writing YAML.** Dex's Kubernetes storage looks clients up by a
**derived object name**: `idToName(id)` = base32 (lowercase alphabet
`abcdefghijklmnopqrstuvwxyz234567`, padding stripped) of a SHA-256-based hash of the client ID.
A hand-written `OAuth2Client` with a human-readable `metadata.name` **will not be found by Dex**
— you get `Invalid client_id` at the authorize endpoint and a long, confusing debugging session.
(This is [dexidp/dex#1422](https://github.com/dexidp/dex/issues/1422); the encoding is
implemented in [`storage/kubernetes/types.go`](https://github.com/dexidp/dex/blob/master/storage/kubernetes/types.go).)

**Recommended: register Forge as a Dex *static client*** — via the Dex config / the existing
`dex-static-clients` Secret — which sidesteps the name-hashing entirely and is what ArgoCD
already does on this platform:

```yaml
staticClients:
  - id: forge-ai
    name: Forge AI
    secretEnv: FORGE_AI_CLIENT_SECRET       # sourced from the dex-static-clients Secret
    redirectURIs:
      - https://forgeai.hvslocal/auth/callback
```

**Alternative: the CRD** (note top-level fields — an `OAuth2Client` has **no `spec:`**):

```yaml
apiVersion: dex.coreos.com/v1
kind: OAuth2Client
metadata:
  # MUST be dex's idToName("forge-ai") — NOT a name you choose.
  name: <base32-encoded-derived-name>
  namespace: dex
id: forge-ai
name: Forge AI
secret: <the client secret — plaintext in the CR; see caveat>
redirectURIs:
  - https://forgeai.hvslocal/auth/callback
public: false
```

**HUMAN DECISIONS REQUIRED (blocking, platform-side):**

- **D-1**: static client vs CRD. **Recommendation: static client.** Run
  `kubectl get oauth2clients -n dex -o yaml` and inspect how the existing client's
  `metadata.name` relates to its `id` — that tells you whether Dex created it (hashed name) or
  a human did (and whether it actually works). *I could not verify this: no shell access in
  this session.*
- **D-2**: the CRD stores the client secret **in plaintext in the CR**. If you go the CRD route,
  the secret must be delivered by ESO/a sealed mechanism, not committed. This is another
  point in favour of the static client.
- **D-3**: `redirect_uri` must be registered **exactly** as
  `https://forgeai.hvslocal/auth/callback`. Dex does exact matching. One trailing slash and
  login breaks.
- **D-4**: confirm the GitHub connector's group format (§6.4) — resolved empirically at R4.
- **D-5**: whether unauthenticated `/metrics` may remain exposed via the HTTPRoute (§6.5).

---

## 10. Implementation Plan (TDD, per package)

Order matters: `forge-config` → `forge-security` → `forge-gateway` → `forge-ui` → `deploy`.
Every task is **test-first**. Named test cases below are the acceptance criteria.

### 10.1 `forge-config`

**Tasks**
1. `AuthMode` enum (`enforce`, `dev_insecure`); `AuthConfig` (`mode`, default `enforce`).
2. `OIDCConfig`, `SessionConfig`, `ServiceTokenConfig`, `ServiceToken`,
   `AuthorizationConfig`, `RoleBinding`, `Permission` enum.
3. Wire into `SecurityConfig`. Mark `api_keys` deprecated; **remove** `jwt_secret` (leave a
   validator that errors on its presence).
4. Implement the eight validators of §8.4.
5. Registration of a `SecretRef` resolver path for the new secrets (env only — no change needed,
   but assert it).

**Tests** (`packages/forge-config/tests/test_security_schema.py`)
- `test_auth_mode_defaults_to_enforce_when_security_block_absent` ← **the headline test**
- `test_enforce_with_no_oidc_and_no_service_tokens_raises_config_error`
- `test_wildcard_origin_with_oidc_enabled_raises_config_error`
- `test_same_site_none_raises_config_error`
- `test_session_secure_false_in_enforce_mode_raises_config_error`
- `test_jwt_secret_present_raises_config_error_with_migration_message`
- `test_binding_referencing_unknown_role_raises_config_error`
- `test_role_with_unknown_permission_raises_config_error`
- `test_service_token_sha256_must_be_64_hex_chars`
- `test_audience_defaults_to_client_id`
- `test_api_keys_config_still_parses_and_emits_deprecation_warning`

### 10.2 `forge-security`

**New module `forge_security/oidc/`**

| File | Contents |
|---|---|
| `jwks.py` | `JwksCache` — async, TTL, `kid` lookup, rate-limited forced refresh, stale-if-error |
| `verifier.py` | `OIDCTokenVerifier.verify(token) -> Claims` — §8.2 verbatim |
| `discovery.py` | `.well-known` fetch; endpoint pinning; startup warm |
| `session.py` | `SessionCodec` — `MultiFernet` encrypt/decrypt, absolute + idle expiry, `sid` |
| `service_tokens.py` | `ServiceTokenVerifier` — prefix, SHA-256, `compare_digest`, expiry |
| `principal.py` | `Principal(kind, sub, email, name, groups, roles, permissions, token_id)` |
| `authorizer.py` | `Authorizer.roles_for(claims)`, `Authorizer.has(principal, permission)` |
| `errors.py` | `AuthError(status, code)` — the only way out of a failed check |

**Deletions**: `SecurityGate.authenticate` / `_verify_jwt` HS256 path and its trust-as-is
fallthrough. Keep `TrustPolicyEnforcer` (rate limit / origin) and `AuditLogger`, and re-point
them at `Principal`. **Drop `python-jose`** from `forge-security/pyproject.toml` — it is
unmaintained, has a CVE history, and PyJWT + `cryptography` covers everything. Add
`pyjwt[crypto]>=2.10` and `httpx>=0.28` explicitly.

**Tests** (`packages/forge-security/tests/`)

`test_jwks_cache.py`
- `test_fetches_and_caches_keys` / `test_serves_from_cache_within_ttl`
- `test_refetches_after_ttl_expiry`
- `test_kid_miss_triggers_forced_refresh_and_then_resolves` ← key-rotation case
- `test_kid_miss_refresh_is_rate_limited_by_min_refresh_seconds` ← the DoS-amplification guard
- `test_unknown_kid_after_refresh_raises_auth_error_401_unknown_key`
- `test_fetch_failure_with_warm_cache_serves_stale_keys`
- `test_fetch_failure_past_stale_grace_raises_503`
- `test_cold_cache_fetch_failure_raises_503_not_401` ← **infra failure must not be a 401**

`test_oidc_verifier.py` (fixtures: a locally generated RSA keypair + a synthetic JWKS)
- `test_valid_rs256_token_returns_claims`
- `test_hs256_token_signed_with_jwks_modulus_is_rejected` ← **alg-confusion**
- `test_alg_none_token_is_rejected` ← **the classic**
- `test_wrong_issuer_rejected_401_invalid_issuer`
- `test_wrong_audience_rejected_401_invalid_audience` ← **the `verify_aud=False` regression**
- `test_audience_of_another_dex_client_rejected` (e.g. an ArgoCD token)
- `test_expired_token_rejected_401_token_expired`
- `test_token_within_clock_skew_leeway_accepted`
- `test_missing_kid_rejected` / `test_missing_sub_rejected`
- `test_token_signed_by_unrelated_key_rejected`
- `test_garbage_string_rejected_401_not_treated_as_identity` ← **the current bypass**
- `test_nonce_mismatch_rejected_in_authcode_flow`

`test_session_codec.py`
- `test_roundtrip_encrypt_decrypt`
- `test_tampered_ciphertext_raises_invalid_session`
- `test_cookie_from_a_different_key_raises_invalid_session`
- `test_absolute_expiry_enforced` / `test_idle_timeout_enforced`
- `test_previous_key_decrypts_but_new_key_encrypts` ← rotation
- `test_session_blob_contains_no_tokens` ← asserts we never persisted an access/refresh token

`test_service_tokens.py`
- `test_valid_token_resolves_to_principal_with_declared_roles`
- `test_unknown_token_id_rejected` / `test_wrong_secret_rejected`
- `test_expired_token_rejected`
- `test_comparison_is_constant_time` (assert `hmac.compare_digest` is used)
- `test_token_without_forge_sk_prefix_is_not_treated_as_service_token`

`test_authorizer.py`
- `test_group_binding_grants_role` / `test_email_binding_is_case_insensitive`
- `test_multiple_matching_bindings_union_roles`
- `test_no_matching_binding_yields_zero_permissions` ← **deny by default**
- `test_admin_wildcard_grants_all_permissions`
- `test_role_lacking_permission_denies`

### 10.3 `forge-gateway`

**Tasks**
1. **Rewrite `security.py`**: `resolve_principal(request) -> Principal` (§5.1) and a
   `require(permission)` dependency factory. **Delete `_dev_mode`, the `X-Caller-ID` /
   `caller_id` extraction, and every fallthrough.**
2. **Rewrite `app.py::_init_security_gate` → `_init_auth()`**: no `except: → open`. On total
   failure, set `health.set_ready(False)` and install a "deny-all-503" resolver.
3. New `routes/auth.py`: `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`,
   `GET /v1/auth/me`. **Register before the `/{path:path}` SPA catch-all.**
4. CSRF middleware (§4.2) — cookie-auth + unsafe method only.
5. Re-point every router: `conversational`/`programmatic` → `require("agent:invoke")`;
   `admin`/`persona` → `require("config:read"|"config:write")`; `a2a` → `require("agent:peer")`;
   `mcp` (`enforce_asgi_security`) → `require("tools:invoke")`.
6. **Delete `auth.py::require_admin_key`** (keep `validate_peer_endpoint` — unrelated SSRF guard).
7. Fix CORS: explicit origins; refuse to start with `*` + credentials.
8. Add `"login"` to `spa_routes`; add `auth_mode` + `auth_healthy` to `/health/ready`.
9. Metrics: `forge_auth_decisions_total{result,reason,kind}`, `forge_jwks_refresh_total{result}`,
   `forge_jwks_stale`, `forge_session_issued_total`.
10. Audit every decision with `Principal.sub` + `token_id` — **never** the credential itself.

**Tests** (`packages/forge-gateway/tests/`)

`test_auth_enforcement.py` — **the regression suite for the live vulnerability**
- `test_chat_completions_without_credentials_returns_401` ← **the bug**
- `test_chat_completions_with_x_caller_id_header_returns_401` ← **the bypass**
- `test_chat_completions_with_caller_id_query_param_returns_401` ← **the bypass**
- `test_mcp_mount_without_credentials_returns_401`
- `test_a2a_task_without_credentials_returns_401`
- `test_admin_without_credentials_returns_401`
- `test_health_endpoints_are_public`
- `test_auth_init_failure_marks_not_ready_and_returns_503_not_200` ← **the fail-open bug**
- `test_missing_security_config_defaults_to_enforce_and_denies` ← **absence ≠ no auth**
- `test_dev_insecure_requires_both_config_and_env_var`
- `test_dev_insecure_sets_x_forge_insecure_mode_header`

`test_auth_routes.py`
- `test_login_redirects_to_dex_with_pkce_state_and_nonce`
- `test_login_sets_transaction_cookie_httponly_secure_samesite_lax`
- `test_login_rejects_absolute_url_in_next_param` ← **open redirect**
- `test_login_rejects_protocol_relative_next_param` (`//evil.com`)
- `test_callback_with_mismatched_state_returns_400`
- `test_callback_without_transaction_cookie_returns_400`
- `test_callback_with_replayed_transaction_cookie_returns_400` ← single-use
- `test_callback_with_bad_nonce_returns_401`
- `test_callback_sets_session_cookie_httponly_and_csrf_cookie_readable`
- `test_callback_for_user_with_no_role_binding_returns_403_and_sets_no_cookie`
- `test_session_cookie_is_not_readable_by_js` (assert `HttpOnly` in `Set-Cookie`)
- `test_logout_clears_session_cookie`
- `test_auth_me_returns_claims_roles_and_permissions`

`test_csrf.py`
- `test_cookie_auth_post_without_csrf_header_returns_403`
- `test_cookie_auth_post_with_mismatched_csrf_returns_403`
- `test_bearer_auth_post_without_csrf_header_succeeds` ← bearer is CSRF-exempt
- `test_cookie_auth_get_without_csrf_succeeds`

`test_authorization.py`
- `test_user_role_can_chat_but_not_write_config` (403)
- `test_admin_role_can_write_config`
- `test_viewer_can_read_config_but_not_chat`
- `test_service_token_scoped_to_agent_invoke_cannot_reach_admin`

### 10.4 `forge-ui`

**Tasks**
1. **Delete the API-key `LoginPage`** form. Replace with a "Sign in with Dex" button →
   `window.location.assign('/auth/login?next=' + encodeURIComponent(path))`.
2. Rewrite `stores/authStore` — **no token is ever held**. State = the `/v1/auth/me` response.
3. Global fetch interceptor: on `401` → redirect to `/auth/login`; on `403` → render a
   "no access" page (do **not** loop into login).
4. Attach `X-CSRF-Token` (read from the `forge_csrf` cookie) to all non-GET requests;
   `credentials: "same-origin"`.
5. Gate nav/actions on `permissions` from `/v1/auth/me` (cosmetic only).
6. Red banner when `/health/ready` reports `auth_mode: dev_insecure`.
7. "Signed out of Forge (your Dex SSO session persists)" copy on logout (§4.1).

**Tests** (vitest)
- `test_login_page_has_no_api_key_input`
- `test_401_response_triggers_redirect_to_auth_login`
- `test_403_response_does_not_redirect_to_login`
- `test_non_get_requests_include_csrf_header`
- `test_auth_store_never_persists_a_token`

### 10.5 `deploy`

**Tasks**
1. OpenBao: write `oidc_client_secret`, `session_encryption_key`, `break_glass_token` to
   `kv/users/aj-geddes/app/forge-ai`.
2. `values-hvs-k8s.yaml`: add the three `externalSecret.data` entries (remember: no `kv/` prefix).
3. `values-hvs-k8s.yaml` `forgeConfig.security`: the full §8.3 block. Remove
   `agentweave.enabled: false` reliance and `api_keys.enabled: false`.
4. HTTPRoute: exclude `/metrics` from public exposure (D-5).
5. **CI guard**: `helm template … | grep -q FORGE_DEV_INSECURE && exit 1` — the chart must be
   structurally incapable of rendering the insecure switch.
6. Platform: register the Dex client (§9.3) — **do this first**, it is the long pole.

---

## 11. Migration and Rollout (the app is live *right now*)

### Compatibility

| Existing setting | Fate |
|---|---|
| `security.jwt_secret` | **Removed.** Its presence is a **hard config error** with a migration message. It is symmetric HS256 with `verify_aud=False` — leaving it accepted would leave a second, weaker door. Nothing in the live config uses it. |
| `security.api_keys` | **Deprecated, still honoured for one minor.** Translated to a synthetic service token (`id: legacy-api-key`, `roles: ["admin"]`), logged as a `DeprecationWarning` at every startup. Live config has it `enabled: false`, so **nothing actually depends on it** — but the shim protects any local/undocumented use. Removed in the next minor. |
| `security.agentweave` | Inert. Retained in the schema so old configs parse; ignored at runtime; **no longer able to disable auth**. Delete in the next minor along with the unused `agentweave` dependency. |
| `security.allowed_origins: ["*"]` | Now a **config error** when OIDC is on. The live value is already explicit. |

### Rollout

**R0 — Platform (no Forge change, no risk).** Register the Dex client (§9.3, D-1/D-2/D-3).
Write the OpenBao keys. Verify from a pod:
`curl -s https://dex.hvslocal/dex/.well-known/openid-configuration`. **Blocking prerequisite.**

**R1 — Mint the break-glass token.** Generate `forge_sk_break-glass_<32 random bytes>`. Put the
**plaintext in OpenBao** (for you) and the **SHA-256 in `forge.yaml`** (for the pod). *Verify it
works before R3.* This is what makes R3 safe: if OIDC is misconfigured, you still have admin.

**R2 — Ship the code with `dev_insecure` locally only.** Merge the packages. CI is green. The
deployed values are **not yet changed** — the live pod still runs the old image. Zero production
impact.

**R3 — Flip the cluster, `enforce` from the first request.** Update `values-hvs-k8s.yaml`
(new `image.tag`, full `security` block, `mode: enforce`, `bindings` with your **email**
bootstrap per §6.4) → commit → push → ArgoCD syncs.

*There is no "monitor mode" and no gradual ramp, deliberately.* A monitor mode is a mode in
which the endpoint is still open — and the endpoint being open is the incident we are fixing.
The rollout is safe not because it is gradual but because it is **reversible in two ways**: the
break-glass token (R1) recovers admin access without OIDC, and reverting `image.tag` in git
recovers the whole thing via ArgoCD.

Watch: the new pod must reach Ready. **If auth cannot initialize, the new pod never becomes
Ready and the old pod keeps serving** (§7.3) — the failure mode is "the fix didn't land", not
"the site is down".

**R4 — Close the group unknown.** Log in via the browser. `GET /v1/auth/me`. Read the **actual**
`groups` strings. Write them into `bindings`. Commit. This is the step that turns the email
bootstrap into proper group-based authorization (§6.4).

**R5 — Verify the fix.** These are the acceptance checks; run them against the live host:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  https://forgeai.hvslocal/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"messages":[]}'          # expect 401
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://forgeai.hvslocal/v1/chat/completions -H 'X-Caller-ID: admin'   # expect 401
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://forgeai.hvslocal/v1/admin/config?caller_id=admin'         # expect 401
curl -sS -o /dev/null -w '%{http_code}\n' https://forgeai.hvslocal/health/live  # expect 200
curl -sS -o /dev/null -w '%{http_code}\n' https://forgeai.hvslocal/mcp          # expect 401
```

**R6 — Clean up.** Rotate the break-glass token if it was used. Remove the `api_keys` shim and
the `agentweave` dependency in the next minor.

### Rollback

Revert `image.tag` (and the `security` block) in `values-hvs-k8s.yaml`; ArgoCD syncs.
**Never roll back by setting `mode: dev_insecure`** — it would not work anyway (the chart
cannot render `FORGE_DEV_INSECURE`), and that is by design.

---

## 12. Consequences

**Positive**
- The live unauthenticated endpoint closes. Every agent-driving surface requires a verified
  credential.
- Authentication becomes **non-bypassable**: one resolver, no fallthrough, no header-supplied
  identity.
- Authorization becomes **real**: authenticated ≠ authorized; deny by default; per-principal
  roles.
- **No OAuth token ever touches the browser.**
- Audit logs finally name a principal (`sub` / `svc:<id>`) instead of "someone with the key".
- SSO consistency with ArgoCD; no new password to manage; offboarding a GitHub user offboards
  them from Forge.
- `python-jose` (unmaintained) leaves the dependency tree.

**Negative / accepted limitations**
- **No server-side session revocation** before expiry (§4.3). Mitigated by short lifetimes and
  key rotation as the "log everyone out" lever. — **TD-001**.
- **Local logout only** — Dex publishes no `end_session_endpoint`; the Dex SSO session survives
  Forge logout. Must be communicated in the UI. — **TD-002**.
- **`/metrics` remains unauthenticated on the pod port** to keep Prometheus scraping (§6.5).
  — **TD-003**.
- **A2A peers use bearer service tokens, not mTLS/SPIFFE.** Correct given no SPIRE, weaker than
  the ideal. — **TD-004**.
- Session expiry costs the user a redirect flicker every 8 h (the deliberate price of not
  holding refresh tokens).

**Risks and mitigations**

| Risk | Mitigation |
|---|---|
| **Self-lockout** during rollout (bad binding, wrong group string) | The **break-glass service token** (R1) is OIDC-independent and is minted and *tested* before enforcement. Admin config is hot-reloadable. |
| Dex `oauth2client` CRD name-hashing eats a day (§9.3) | Use a **static client** instead. Documented landmine + upstream issue link. |
| GitHub group strings differ from what we guessed | Bootstrap with an `emails` binding; close empirically at R4 via `/v1/auth/me`. |
| Dex outage ⇒ Forge unusable | Warm JWKS cache + 24 h stale grace keeps existing sessions and bearer tokens working; service tokens are wholly independent of Dex. |
| XSS in the SPA (LLM/tool output) | `httpOnly` cookie: the credential cannot be exfiltrated. (Session riding remains possible — a strict CSP is the follow-on, **TD-005**.) |
| `kid`-miss refresh storm as a DoS on Dex | `jwks_min_refresh_seconds` floor. Explicitly tested. |
| Session key leak ⇒ forged sessions | Key lives only in OpenBao→env, never in git/image. Rotation via `previous_encryption_key` needs no downtime. |

**Tech debt taken**: TD-001…TD-005 above, each with an owner and a paydown trigger. None of them
are on the critical path of "the endpoint is open".

---

## 13. Validation Criteria

Ship-blocking:

- [ ] Every entry in the §7.3 failure matrix has a passing test.
- [ ] `test_auth_mode_defaults_to_enforce_when_security_block_absent` passes.
- [ ] `test_chat_completions_with_x_caller_id_header_returns_401` passes.
- [ ] `test_wrong_audience_rejected` and `test_alg_none_token_is_rejected` pass.
- [ ] `test_auth_init_failure_marks_not_ready_and_returns_503_not_200` passes.
- [ ] `grep -rn "verify_aud.*False\|HS256\|X-Caller-ID\|_dev_mode" packages/` returns **nothing**
      outside tests.
- [ ] Coverage of `forge_security/oidc/` ≥ 90 %.
- [ ] R5's curl checks all return 401 against the live host.

Ongoing:

- `forge_auth_decisions_total{result="deny"}` — a sustained spike means a misconfigured binding
  or an attack.
- `forge_jwks_stale` — must be 0 in steady state.
- Alert on `auth_mode != enforce` in any non-dev deployment.
- **Reconsider this ADR when**: SPIRE/OPA actually land (revisit A2A mTLS and externalized
  policy); a shared cache lands (revisit session revocation, TD-001); or Forge becomes
  multi-tenant (roles become per-tenant, which this model does not cover).

---

## 14. References

- [OAuth 2.0 for Browser-Based Applications (BCP)](https://datatracker.ietf.org/doc/draft-ietf-oauth-browser-based-apps/) — the BFF recommendation
- [dexidp/dex #1422 — Kubernetes CRD storage hashes incorrectly](https://github.com/dexidp/dex/issues/1422)
- [dex `storage/kubernetes/types.go`](https://github.com/dexidp/dex/blob/master/storage/kubernetes/types.go) — `idToName` base32 encoding
- [dexidp/dex #2101 — OAuth2 client credentials grant](https://github.com/dexidp/dex/discussions/2101) and [#2382 — machine-to-machine](https://github.com/dexidp/dex/discussions/2382)
- [Dex — Machine Authentication / token exchange](https://dexidp.io/docs/guides/token-exchange/)
- [PyJWT API reference](https://pyjwt.readthedocs.io/en/stable/api.html) and [jpadilla/pyjwt #1051 — `cache_keys=True` serves revoked keys](https://github.com/jpadilla/pyjwt/issues/1051)
- RFC 7517 (JWK), RFC 7519 (JWT), RFC 9700 (OAuth 2.0 Security BCP), RFC 7636 (PKCE)
