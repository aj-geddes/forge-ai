---
layout: page
title: Security
description: Two-plane authentication (Dex OIDC + service tokens for humans/machines, AgentWeave SPIFFE + OPA for workloads), SSRF protection, secret management, CORS/CSRF, and rate limiting for Forge AI.
parent: Technical
nav_order: 5
---

# Security

Forge AI implements defense-in-depth security across **two physically and logically separate planes** (ADR-0001, ADR-0004):

- **Human plane** -- north-south traffic on the main gateway port (`:8000`). Browsers and machine clients authenticate via Dex OIDC, BFF session cookies, or service tokens. There is no bypass toggle: an entirely absent `security:` block still enforces authentication against a working mechanism.
- **Workload plane** -- east-west, agent-to-agent (A2A) traffic on a dedicated mTLS listener (`:8443`), secured by AgentWeave SPIFFE identity + OPA policy authorization (ADR-0004). This plane is off by default (`security.agentweave.enabled: false`) and, when enabled, has zero effect on the human plane -- the two share no resolver, listener, or authorization mechanism.

An earlier design (`forge_security.SecurityGate`, `TrustPolicyEnforcer`, JWT-with-a-shared-secret) was replaced by ADR-0001 and is no longer wired into forge-gateway; those classes still exist in `forge_security.middleware` / `forge_security.trust` but nothing in forge-gateway or forge-agent imports them. The sections below describe the code that is actually on the request path today.

## Human Plane: OIDC, Sessions, and Service Tokens

### The credential resolver

Every protected route on `:8000` -- admin, agent invocation, chat, A2A, and `/mcp` -- ultimately depends on `forge_gateway.security.get_principal`, which extracts the session cookie and `Authorization` header from the request and delegates to the single bypass-free resolver:

```
forge_security.oidc.resolver.resolve_principal(
    session_cookie, authorization_header,
    session_codec, service_token_verifier, oidc_verifier, authorizer,
) -> Principal
```

Resolution order, with no fallthrough branch:

1. **Session cookie present** -> decode it (BFF session path) -> `Principal(kind="user")`.
2. **Else, `Authorization: Bearer <token>` present**:
   a. Token starts with `forge_sk_` -> verify as a service token -> `Principal(kind="service")`.
   b. Token has 3 dot-separated segments (a JWS) -> verify as an OIDC bearer JWT (RS256 against Dex's JWKS) -> `Principal(kind="user")`.
   c. Otherwise -> `401 invalid_credential_format`.
3. **Else** -> `401 missing_credentials` (with `WWW-Authenticate: Bearer`).

`X-Caller-ID` headers and `caller_id` query parameters are not authentication inputs anywhere in this path -- `resolve_principal`'s signature has no parameter that could carry them.

Once a principal is resolved, `get_principal` enforces the identity-keyed rate limit (see [Rate Limiting](#rate-limiting)) before returning it.

**Source:** `packages/forge-security/src/forge_security/oidc/resolver.py`, `packages/forge-gateway/src/forge_gateway/security.py`

### Browser login: Dex OIDC (BFF pattern)

Browsers never see an OAuth access or ID token. `forge_gateway.routes.auth` implements a backend-for-frontend authorization-code + PKCE flow against Dex:

| Route | Purpose |
|-------|---------|
| `GET /auth/login` | Starts the flow: generates PKCE `code_verifier`/`code_challenge`, `state`, and `nonce`; stores them in a short-lived, Fernet-encrypted, httpOnly `forge_oidc_tx` cookie; redirects to Dex's authorization endpoint. |
| `GET /auth/callback` | Exchanges the authorization code at Dex's token endpoint (PKCE `code_verifier` only -- the registered `forge-ai` Dex client is public, no `client_secret`), verifies the returned `id_token` (RS256 + JWKS, `nonce` checked), resolves roles from claims, and sets the session + CSRF cookies. |
| `POST /auth/logout` | Clears the local Forge session cookies. Dex's own SSO session is untouched (Dex exposes no `end_session_endpoint`). |
| `GET /v1/auth/me` | Returns the resolved caller's `kind`, `sub`, `email`, `groups`, `roles`, and `permissions`. Goes through `get_principal` like any other protected route. |

The session cookie (`forge_session` by default) is a stateless, authenticated-encrypted (Fernet/`MultiFernet`) blob containing only identity claims -- never an access or refresh token. `/auth/login` and `/auth/callback` carry an IP-keyed rate limit (the abuse vector: hammering `/auth/callback` with garbage authorization codes; no principal exists yet at that point to key an identity-based limit on).

**Source:** `packages/forge-gateway/src/forge_gateway/routes/auth.py`, `packages/forge-security/src/forge_security/oidc/verifier.py`, `packages/forge-security/src/forge_security/oidc/session.py`

### Service tokens (machine clients)

Machine clients (MCP, A2A peers, CI) authenticate with an opaque bearer token of the form `forge_sk_<token_id>_<random>`. Only the SHA-256 hex digest of a token is ever stored; the presented token's digest is compared against every configured digest with `hmac.compare_digest` (constant-time):

- **Static tokens** -- `security.service_tokens.tokens` in `forge.yaml`, each with an `id`, a `secret_sha256` digest, `roles`, and an optional `expires_at`.
- **User-issued (self-service) tokens** (ADR-0002) -- a logged-in Dex user (`principal.kind == "user"`) can mint their own token via `POST /v1/auth/tokens`. Enforced at mint time:
  - only session/OIDC principals may mint (no chaining -- a service token cannot mint another token);
  - requested roles are expanded to permissions and must be a subset of the minter's own current permissions (anti-escalation);
  - TTL must fall within `security.service_tokens.user_tokens.default_ttl_seconds`/`max_ttl_seconds` (1-hour floor);
  - each owner is capped at `max_tokens_per_owner` (default 25) active tokens.
  - `GET /v1/auth/tokens` lists the caller's own tokens (or all tokens with `?all=true`, admin-only); `DELETE /v1/auth/tokens/{token_id}` revokes one, effective on the very next request. A non-owner/non-admin revoke, or an unknown id, returns `404` (never `403`), so ids cannot be probed by enumeration.
  - This feature is opt-in: `security.service_tokens.user_tokens.enabled: false` (the default) makes all three routes return `404` as if they did not exist.

**Source:** `packages/forge-security/src/forge_security/oidc/service_tokens.py`, `packages/forge-security/src/forge_security/oidc/user_tokens.py`, `packages/forge-gateway/src/forge_gateway/routes/tokens.py`

### Authorization (roles and permissions)

`forge_security.oidc.Authorizer` maps claims to roles to permissions, deny-by-default:

- **Roles** -- a principal receives the union of roles from every `security.authorization.bindings` entry it matches (by `groups`, `emails`, or `subs`; `email` bindings only apply when the IdP asserts `email_verified`). A principal matching no binding gets zero permissions unless `authorization.default_role` is explicitly set.
- **Permissions** -- a closed set: `agent:invoke`, `tools:invoke`, `agent:peer`, `config:read`, `config:write`, `metrics:read`, plus the `admin` role's wildcard `*` (every permission).
- **Enforcement** -- `forge_gateway.security.require_permission(permission)` is the FastAPI dependency every protected route uses; it resolves the principal via `get_principal` (401/503 on auth failure) and then checks authorization (`403 forbidden` if the principal's permissions don't include it). `enforce_mcp_security` applies the same check (`tools:invoke`) to the raw ASGI `/mcp` mount, outside FastAPI's `Depends` machinery.

**Admin routes** (`/v1/admin/*`) are protected exactly this way -- `config:read` for read endpoints, `config:write` for mutating ones -- through the same principal resolver as every other route. There is no separate admin API key check.

| Route group | Required permission |
|-------------|---------------------|
| `GET`/`PUT /v1/admin/config`, `/v1/admin/config/schema`, `/v1/admin/tools`, `/v1/admin/tools/preview`, `/v1/admin/sessions`, `/v1/admin/peers`, `POST /v1/admin/peers/{name}/ping` | `config:read` (read) / `config:write` (write) |
| `POST /v1/agent/invoke`, `POST /v1/chat/completions` | `agent:invoke` |
| `POST /a2a/tasks` | `agent:peer` |
| `/mcp` (tool invocation) | `tools:invoke` |
| `GET /metrics` (only when `security.authorization.metrics_public: false`) | `metrics:read` |

**Source:** `packages/forge-security/src/forge_security/oidc/authorizer.py`, `packages/forge-gateway/src/forge_gateway/security.py`, `packages/forge-gateway/src/forge_gateway/routes/admin.py`

### Development mode (`dev_insecure`)

`security.auth.mode` is either `enforce` (the schema default -- an absent `security:` block still enforces auth) or `dev_insecure`. `dev_insecure` only actually engages when **both** of the following are true (checked by `forge_gateway.security.is_dev_insecure_active`):

1. `security.auth.mode: dev_insecure` in the config, **and**
2. the `FORGE_DEV_INSECURE=1` environment variable is set on the process.

Neither alone is sufficient -- a config flip without the environment variable, or vice versa, does nothing. While active:

- every request resolves to a synthetic principal (`sub: "dev-anonymous"`, role `admin`) with no credential check at all;
- `forge_gateway.security.DevInsecureHeaderMiddleware` sets `X-Forge-Insecure-Mode: true` on every response, so it is impossible to be in this mode without a visible signal;
- a `CRITICAL`-level log line is emitted at startup.

There is no config key that disables authentication on `:8000` outright (e.g. no `agentweave.enabled: false` bypass) -- `security.agentweave` only controls the separate workload plane (below) and has no field connecting it to `auth`/`oidc`.

**Source:** `packages/forge-gateway/src/forge_gateway/security.py`

## Workload Plane: AgentWeave SPIFFE + OPA (ADR-0004)

`security.agentweave` (default `enabled: false`) configures a second, independent security plane for **east-west agent-to-agent traffic**, physically separate from the human OIDC plane above -- no shared resolver, listener, or authorization mechanism. When `enabled: true`, forge-gateway builds a `WorkloadPlane` and starts a dedicated mTLS listener (`workload_listener_port`, default `8443`, the "a2a-mtls" listener) at application startup; when `false` (the default, and the safe value for pre-ADR-0004 configs), no workload listener starts and this block has zero runtime effect.

The workload plane's building blocks:

- **Identity** -- `extract_spiffe_id_from_cert` parses the `spiffe://` URI SAN from a peer's mTLS client certificate; `server_ssl_context`/`register_rotation_callback` wrap the SPIRE agent socket connection (`spiffe_endpoint`) and SVID rotation.
- **Authorization** -- `authorize_workload` drives a fail-closed OPA check (`opa_endpoint`, default `authz_provider: opa`): `decision = opa.check(caller_id=peer_spiffe_id, resource=my_spiffe_id, action="a2a:task", context={...})`. An unreachable OPA server, or any exception from the authorization provider, is treated as a deny (`WorkloadForbidden`, 403) -- never as an allow.
- **Audit** -- `build_audit_trail` records every workload-plane decision; sink is `stdout` (default, safe with any replica count) or `file` (`audit_backend`/`audit_path`).
- **Trust policy** -- `trust_policy`: `strict` (full enforcement) or `permissive` (relaxed, for development/testing).

Peers configured under `agents.peers` require a pinned `spiffe_id` when the workload plane is enabled (`ForgeConfig`'s `validate_agentweave_peers_are_pinned` validator).

**Source:** `packages/forge-security/src/forge_security/workload/` (`providers.py`, `mtls.py`, `authz.py`, `audit.py`, `resolver.py`), `packages/forge-gateway/src/forge_gateway/app.py` (`_init_workload_plane`), `packages/forge-gateway/src/forge_gateway/workload.py`

## SSRF Protection

The `validate_peer_endpoint()` function prevents Server-Side Request Forgery attacks when the gateway pings peer agents or interacts with configured endpoints:

```python
_PRIVATE_NETWORKS = [
    "10.0.0.0/8",       # RFC 1918
    "172.16.0.0/12",     # RFC 1918
    "192.168.0.0/16",    # RFC 1918
    "127.0.0.0/8",       # Loopback
    "169.254.0.0/16",    # Link-local
    "::1/128",           # IPv6 loopback
    "fc00::/7",          # IPv6 unique local
    "fe80::/10",         # IPv6 link-local
]
```

**Blocked targets:**

- All private IPv4 and IPv6 ranges listed above
- `localhost` hostname
- Hostnames ending in `.local`, `.internal`, or `.localhost`

For non-IP hostnames that do not match blocked patterns, the endpoint is allowed (DNS resolution happens later at connection time).

**Source:** `packages/forge-gateway/src/forge_gateway/auth.py` (`validate_peer_endpoint`)

## Secret Management

### Secret Resolution

Secrets are never stored in plaintext. All sensitive values use `SecretRef` objects that are resolved at runtime by the `CompositeSecretResolver`:

| Source | Resolver | Resolution Method |
|--------|----------|-------------------|
| `env` | `EnvSecretResolver` | `os.environ.get(ref.name)` |
| `k8s_secret` | `K8sSecretResolver` | Reads from Kubernetes secret volume mounts |

The `CompositeSecretResolver` supports registering additional resolvers for new secret sources.

**Source:** `packages/forge-config/src/forge_config/secret_resolver.py`

### `jwt_secret` -- removed

`security.jwt_secret` (a symmetric HS256 shared secret, verified with `verify_aud=False`) is **removed**, not merely deprecated (ADR-0001). Loading a config that still sets it is a hard validation error at parse time: it could not validate Dex's RS256 tokens and would have accepted a token from any Dex client. Migrate to `security.oidc` (Dex RS256 + JWKS) for humans or `security.service_tokens` for machine clients.

### `security.api_keys` -- deprecated

`security.api_keys` (the old admin API-key block) is deprecated (ADR-0001) and accepted for one minor release only: a configured key is translated internally into a synthetic `legacy-api-key` admin service token (constructing `APIKeyConfig` with a key configured emits a `DeprecationWarning`). Admin routes are no longer gated on it directly -- see [Authorization](#authorization-roles-and-permissions) above. Migrate to `security.service_tokens`.

### Secret Redaction

The admin API (`GET /v1/admin/config`) recursively redacts all `SecretRef` values before returning configuration data. The redaction logic identifies `SecretRef` objects by checking for the presence of `source` and `name` fields where `source` is `"env"` or `"k8s_secret"`:

```python
def _redact_secrets(data: Any) -> None:
    if isinstance(data, dict):
        if "source" in data and "name" in data and data.get("source") in ("env", "k8s_secret"):
            data["name"] = "***REDACTED***"
            if "key" in data:
                data["key"] = "***REDACTED***"
            return
        for v in data.values():
            _redact_secrets(v)
    elif isinstance(data, list):
        for item in data:
            _redact_secrets(item)
```

**Source:** `packages/forge-gateway/src/forge_gateway/routes/admin.py` (`_redact_secrets`)

### Kubernetes Secret Injection

In the Helm chart, secrets defined in `values.secrets` are injected as a Kubernetes Secret and loaded via `envFrom.secretRef` into the agent container. This allows `SecretRef` objects with `source: env` to resolve from Kubernetes-managed secrets:

```yaml
# values.yaml
secrets:
  OPENAI_API_KEY: "sk-..."
  ANTHROPIC_API_KEY: "sk-ant-..."
```

**Source:** `deploy/helm/forge/templates/secret.yaml`, `deployment.yaml`

## CORS and CSRF

CORS middleware is configured in the FastAPI application factory using the `security.allowed_origins` setting from `forge.yaml` (default: `["https://forgeai.hvslocal"]`):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,       # From config.security.allowed_origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`SecurityConfig` rejects a wildcard origin (`"*"`) combined with `security.oidc.enabled: true` at config-load time: a wildcard origin with credentialed CORS (required for the session cookie) reflects any origin, which is a total compromise once a session cookie exists. If the config cannot be loaded at all, CORS falls back to `["*"]` with a logged warning (a config that *did* load successfully can never carry that combination, by the validator above).

```yaml
security:
  allowed_origins:
    - "https://forge.example.com"
    - "https://admin.example.com"
```

Cookie-authenticated, state-changing requests (`POST`/`PUT`/`PATCH`/`DELETE` carrying the session cookie) additionally pass through `CSRFMiddleware`, which enforces a double-submit token: the `X-CSRF-Token` request header must match the non-httpOnly `forge_csrf` cookie (compared with `hmac.compare_digest`), and the `Origin`/`Referer` header must name an allowed origin. Requests authenticated with an `Authorization` header (service tokens, OIDC bearer tokens) carry no ambient credential and are exempt.

**Source:** `packages/forge-gateway/src/forge_gateway/app.py` (`_resolve_cors_origins`), `packages/forge-gateway/src/forge_gateway/middleware/csrf.py`

## Rate Limiting

### Per-Identity Rate Limiting

The `SlidingWindowRateLimiter` enforces a per-identity request limit using an in-memory sliding window:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_requests` | 60 | Maximum requests per window (configured via `security.rate_limit_rpm`) |
| `window_seconds` | 60.0 | Window duration in seconds |

**Implementation details:**

- Each identity string has its own timestamp bucket
- Uses `time.monotonic` for clock stability (immune to wall-clock adjustments)
- Lock-free for single-threaded asyncio loops
- Expired timestamps are pruned on each check
- Returns `RateLimitResult` with `remaining` count and `reset_after` duration

**HTTP response when rate-limited:** `429 Too Many Requests` with a `Retry-After` header.

**Source:** `packages/forge-security/src/forge_security/rate_limit.py`

### Rate Limit Integration

`forge_gateway.rate_limit` wires two independent `SlidingWindowRateLimiter` instances, both sized from `security.rate_limit_rpm`:

- **Principal limiter** -- keyed on the resolved `Principal`'s `token_id` (service tokens) or `sub` (everything else). Enforced inside `forge_gateway.security.get_principal`, so every route that reaches `require_permission`/`enforce_mcp_security` (plus `/v1/auth/me`, and `/metrics` when `metrics_public: false`) is covered in one place.
- **Auth-flow limiter** -- keyed on the client's connecting IP (not `X-Forwarded-For`, to prevent trivial evasion). Applied only to `/auth/login` and `/auth/callback`, the pre-authentication OIDC redirect flow where no principal exists yet.

Rate limiting is **disabled by default** (`rate_limit_rpm` of `None`/`<= 0`); real deployments enable it from the application lifespan using the configured `security.rate_limit_rpm`. Both limiters are per-process, in-memory state -- not shared across replicas -- and **fail open** on an internal limiter error (a bug in the throttle must never take the service down; this is deliberately asymmetric with authentication, which is fail-closed).

**Source:** `packages/forge-gateway/src/forge_gateway/rate_limit.py`

## Security Architecture Summary

| Layer | Mechanism | Scope | Default |
|-------|-----------|-------|---------|
| Human authentication | Dex OIDC (RS256 + JWKS) via BFF session cookie, or `Authorization: Bearer` (service token / OIDC JWT) | `:8000`, all protected routes | `security.auth.mode: enforce` |
| Human authorization | `Authorizer` (claims -> roles -> permissions, deny by default) | Every `require_permission(...)`-guarded route, including `/v1/admin/*` | `authorization.default_role: null` |
| Machine authentication | Service tokens (`forge_sk_...`, SHA-256 digest, constant-time compare); static or user-minted | `:8000`, any `Authorization: Bearer` caller | `security.service_tokens.enabled: false` |
| Workload plane | AgentWeave SPIFFE mTLS + OPA authorization | `:8443`, agent-to-agent (A2A) traffic only | `security.agentweave.enabled: false` |
| CORS | `CORSMiddleware` + wildcard-with-OIDC hard rejection | Browser cross-origin requests | `allowed_origins: ["https://forgeai.hvslocal"]` |
| CSRF | Double-submit `forge_csrf` cookie + `X-CSRF-Token` header + Origin check | Cookie-authenticated state-changing requests | Always on when the session cookie is set |
| SSRF | `validate_peer_endpoint()` private-network/hostname blocklist | `POST /v1/admin/peers/{name}/ping` | Always on |
| Rate limiting | Two `SlidingWindowRateLimiter` instances (identity-keyed, IP-keyed) | All protected routes / `/auth/login`, `/auth/callback` | Disabled (`rate_limit_rpm: 60` once enabled) |
| Secrets | `SecretRef` + `CompositeSecretResolver`; redacted on the admin API | `forge.yaml` secret-bearing fields | N/A |
