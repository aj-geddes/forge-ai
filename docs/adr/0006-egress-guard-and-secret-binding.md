# ADR-0006: SSRF-Safe Egress Guard + Secret→Destination Binding (connect-time IP pinning and credential-to-host binding for all agent-driven outbound HTTP)

**Date**: 2026-07-15
**Status**: Proposed
**Relates to**: ADR-0004 (AgentWeave SPIFFE + OPA workload plane — the peer mTLS pinning this ADR generalizes), ADR-0005 (passive/active lifecycle — active agents amplify the outbound-egress blast radius and reuse the same `ToolGate`/approval seam), ADR-0001 (Dex OIDC — the RBAC/`Permission` set the admin routes reuse)
**Governing thesis (owner)**: Forge AI lets an operator forge SEVERAL agents *easily* in a *compliant and secure* manner. An agent that can be steered to make outbound HTTP calls — with an operator's credential attached — is only safe if two properties hold that do not hold today: (1) it can never be tricked into connecting to an internal/metadata address (SSRF), and (2) a resolved secret is only ever attached when the *validated* destination host is the one that credential was bound to (no confused-deputy exfiltration). This ADR designs one shared egress layer that provides both, and specifies the first, adversarially-verifiable slice to build.

**Owner's definitions this ADR designs to (non-negotiable):**
- **SSRF-safe egress** = every real outbound connection resolves and validates the destination IP *at the moment it connects*, against a deny-by-default policy (internal ranges + multi-cloud metadata always denied; optional positive host-allowlist), with **no second DNS resolution** between validation and connect — closing the DNS-rebind/TOCTOU window.
- **Secret→destination binding** = a credential (Bearer/API-key/Basic header set) is *bound* to an allowed-host set at build time and is only attached to a request whose *final, fully-resolved* URL host is in that set; otherwise the call is **rejected** (default) or the credential is **dropped**.

---

## 1. Context

Four outbound-egress sinks exist today; a Phase-1 research pass mapped every one (credential source, destination editability, guard presence):

| Sink | Code | Credential | Destination | SSRF guard today |
|---|---|---|---|---|
| **Manual tools** | `builder/manual.py::_execute_api_call` (L278) via `httpx.AsyncClient()` fallback (L338) | `AuthConfig` SecretRefs → `_resolve_auth_headers`, baked at build time (L99) | `ManualToolAPI.resolved_url` (schema.py L201), then `{{param}}`-templated per call (L303) | **none** |
| **OpenAPI tools** | `builder/openapi.py::_execute_openapi_call` (L729) + `_fetch_remote_spec` (L125) | `AuthConfig` SecretRefs, baked at build time (L88) | spec `servers[0].url` (L176) *or* `source.url` — **content-influenced** | **none** |
| **A2A peers** | `agent/peers.py::PeerCaller.call_peer` (L397) | mTLS client cert (SPIFFE), not a header secret | `PeerAgent.endpoint`, BASE-only | **strongest** — `validate_peer_endpoint` at write time **+** pre-send SPIFFE TLS probe (ADR-0004) |
| **LLM / litellm** | `agent/llm.py::_build_model` (L203) → PydanticAI provider client | `litellm_params.api_key` (raw string, **not** a SecretRef) | `litellm_params.api_base`, BASE-only | **none** |

Two structural facts drive this ADR:

1. **A genuinely good SSRF classifier already exists, in the wrong layer, wired only as a pre-check.** `forge_gateway/auth.py` has `validate_peer_endpoint` / `_candidate_ips` / `_is_internal_ip` / `_PRIVATE_NETWORKS` / `_BLOCKED_HOSTNAMES` (auth.py L55–140). It correctly canonicalizes non-canonical IP encodings (decimal/hex/octal/short-dotted, IPv4-mapped IPv6 via `inet_aton` + `ipv4_mapped`) and denies RFC1918/loopback/link-local/IMDS/`*.svc`/`kubernetes.default`. But it is a **validate-time string/DNS check** that resolves the name **once**, then throws the result away; the real `httpx` call resolves the name **again** at connect time. Its own docstring (auth.py L137–139) *claims* "the call site re-validates the resolved address at connect time (resolve-then-pin)" — **that re-validation does not exist anywhere in the codebase.** An attacker controlling DNS for a name returns a public A record during the check and `169.254.169.254` at connect: classic DNS rebinding (the exact class behind 2025–2026 CVEs — MCP Atlassian GHSA-489g-7rxv-6c8q, and LiteLLM's own Pwn2Own SSRF+RCE chain). It also lives in `forge-gateway`, the *top* of the dependency chain (`forge-config → forge-security → forge-agent → forge-gateway`), so the tool builders in `forge-agent` **cannot reach it** and have no guard at all.

2. **The credential and the destination are fully decoupled — that decoupling is the exfiltration bug.** In every credentialed sink the resolved secret is attached *unconditionally* to whatever destination is currently configured. Repoint the destination (overlay edit of `url`/`base_url`/`api_base`, a `{{host}}` template the caller fills at runtime with no config write at all, or an attacker-served OpenAPI spec advertising a hostile `servers[0].url`) and the secret follows. The existing overlay `secret_refs()` guard (loader.py L86) only blocks *introducing a new* secret reference; it does nothing when the *same* token is kept and the *host* moves.

Also in scope, because it needs the exact same guard: `POST /v1/admin/tools/preview` (admin.py L1325) takes a caller-supplied `source: dict`, calls `OpenAPIToolBuilder(...).build()` → `_fetch_remote_spec` with **no guard**, requiring only `config:read` — a live, shipped SSRF-read primitive independent of any Phase-2 change.

**What must be designed:** (a) one shared, correctly-layered egress module; (b) a connect-time pinned-IP transport that eliminates the rebind window; (c) a deny-by-default host-allowlist *policy*; (d) a secret→destination *binding* so a credential only travels to its bound host; (e) per-sink integration; (f) making tool destinations safely runtime-editable.

---

## 2. Decision Drivers

- **Fail-closed / default-deny.** Absence of an allowlist entry, an unresolvable name, or a destination outside a credential's binding must **stop** the call, never proceed — mirroring ADR-0001's "absence of configuration can never mean absence of a control" and ADR-0005's two-key posture.
- **Guard at the connection, not the URL.** App-level URL validation has been bypassed repeatedly in the wild (OWASP 2026 SSRF cheat sheet; the LiteLLM CVE). The authoritative check must run at the socket layer against the *exact* IP being connected to, so there is no second resolution to rebind.
- **One implementation, shared across sinks.** The classifier must live *below* both the tool builders (`forge-agent`) and the admin routes (`forge-gateway`) so there is a single, drift-free enforcement point — not a copy in each layer.
- **Backward compatibility is absolute.** Every existing `forge.yaml` must parse and behave identically. The binding default must be "pin the credential to the host the operator already declared in BASE" so pre-existing configs are *safer* with zero rewrite, never broken.
- **Preserve TLS identity.** IP pinning must keep SNI + certificate verification against the *original hostname* and keep the `Host` header intact — pinning the socket, not the identity.
- **Redirect-safe by construction.** Every redirect hop is a new origin; each must be re-validated through the same guard, not trusted because the first hop passed.
- **Don't collapse the peer mTLS model.** A2A peer calls already do connect-time *cryptographic identity* pinning (SPIFFE), which subsumes host filtering for peers you cryptographically trust — keep it; only add the IP guard to the *non-mTLS* fallback and `ping_peer`.
- **Defense-in-depth beyond the app.** App-level guards are necessary but not sufficient; pair with a K8s NetworkPolicy default-deny egress (a later slice), because the app guard *will* be probed.

---

## 3. Architecture — the shared SSRF-safe egress layer

### 3.1 Where it lives (decision + justification)

**A new subpackage `forge_security.egress` in the `forge-security` package.** The SSRF classification primitives move *out* of `forge_gateway/auth.py` into this module; `auth.py` keeps a thin `validate_peer_endpoint` that re-exports from `forge_security.egress` (no behavior change, no drift, no duplicated blocklist).

Justification (dependency chain `forge-config → forge-security → forge-agent → forge-gateway`):
- The guard must be importable by **both** the tool builders (`forge-agent/builder/*`, `agent/peers.py`) **and** the gateway admin routes (`forge-gateway/routes/admin.py`). The single lowest common ancestor of both is **`forge-security`**. Putting it in `forge-agent` would leave `forge-security`'s own mTLS peer code unable to reuse it without an illegal upward dependency; leaving it in `forge-gateway` (today) is exactly why the builders have no guard.
- It is conceptually a **security control** — a sibling of `identity.py`, `workload/mtls.py`, `trust.py`, `signing.py` already in `forge-security` — not a tool-building concern. A network egress boundary belongs with the other security boundaries.
- `forge-security` already depends on `forge-config` (so it can consume `EgressPolicy`) and already declares `httpx>=0.28` — exactly the deps the guard needs, no new dependency edges.

### 3.2 Three planes

The module has three cooperating but independently-testable planes:

1. **Classification (`egress/classify.py`)** — pure, synchronous, no I/O beyond `getaddrinfo`. The hoisted-and-extended `is_internal_ip` / `candidate_ips` / `validate_endpoint`. This answers *"is this host/IP a forbidden address?"* Blocklist extended beyond AWS's `169.254.169.254` to the full multi-cloud metadata set: GCP `metadata.google.internal` (already), Alibaba `100.100.100.200`, Oracle `192.0.0.192`, AWS IPv6 IMDS `fd00:ec2::254`.

2. **Connect-time pinning (`egress/transport.py`)** — a custom `httpcore` network backend swapped into an `httpx.AsyncHTTPTransport`. It resolves, validates, and connects to a **literal pinned IP** inside one `connect_tcp` call, so the IP validated *is* the IP connected — **no rebind window**. This answers *"connect only to a validated address, and re-validate every redirect hop."* SNI/`Host`/cert-verification are preserved because httpcore derives `server_hostname` (TLS) and httpx derives `Host` from the origin, independently of the `connect_tcp` host we swap (verified against httpcore 1.0.9).

3. **Secret→destination binding (`egress/binding.py`)** — a `BoundCredential` value object pairing the resolved headers with the allowed-host set they may travel to, plus `enforce_binding(final_url, cred, policy)`. This answers *"may THIS credential attach to THIS validated destination?"* — a distinct question from plane 2's *"is this address safe to reach at all?"* Both are required: plane 2 stops SSRF to internal addresses; plane 3 stops a valid **public** host receiving a credential bound to a **different** public host (confused-deputy exfil).

These are complementary, not redundant. A request to a public attacker host passes plane 2 (it is not internal) but must fail plane 3 (the credential is not bound to it). A rebind to `169.254.169.254` fails plane 2 at the socket even if plane 3's host-string check was satisfied by a spoofed name.

### 3.3 Why connect-time pinning (not just a better pre-check)

The gold-standard pattern already in the repo is `peers.py::_probe_peer_tls_identity` — validate the *actual* thing immediately before use, not at config-write time. We apply the same principle at the IP layer. A custom `httpcore.AsyncNetworkBackend.connect_tcp` runs `candidate_ips(host)` (the blocking `getaddrinfo`/`inet_aton` off the event loop via `anyio.to_thread.run_sync`), rejects if any candidate is internal, then delegates to the inner backend with the **pinned literal IP string** as the connect host. anyio treats a literal IP as a no-op resolution, so the socket goes to the exact validated IP. Redirects re-enter the same pooled backend (each new origin opens a fresh connection) and are re-validated automatically — strictly better than today's implicit `follow_redirects=False`, and we keep `follow_redirects=False` anyway as belt-and-braces.

---

## 4. Core interface spec

All signatures are concrete and load-bearing.

### 4.1 `forge_config/schema.py` — additive, default-safe config

```python
class EgressAction(str, Enum):
    REJECT = "reject"   # DEFAULT — refuse the outbound call (fail-closed)
    DROP = "drop"       # send the request but strip the bound credential headers

class EgressPolicy(BaseModel):
    """Global egress control (SecurityConfig.egress). Deny-by-default at the
    IP layer is ALWAYS on; ``allowed_hosts`` adds an optional POSITIVE
    host-allowlist on top (when non-empty, a host not listed is denied)."""
    enabled: bool = True
    allowed_hosts: list[str] = Field(default_factory=list)  # exact host, host:port, or "*.suffix"
    require_https: bool = True
    default_action: EgressAction = EgressAction.REJECT

# extend the EXISTING AuthConfig (covers manual + openapi):
class AuthConfig(BaseModel):
    ...  # type/token/header_name/username/password unchanged
    allowed_hosts: list[str] = Field(default_factory=list)      # bind THIS credential to these hosts
    on_egress_violation: EgressAction = EgressAction.REJECT
    # SEMANTICS: empty allowed_hosts => DERIVE the bound host from the
    # config-DECLARED destination at build time (host of ManualToolAPI.resolved_url
    # / OpenAPI base_url as written in BASE). "Pin to where you were pointed" =>
    # every existing config is safe with no rewrite.

# extend the EXISTING SecurityConfig:
class SecurityConfig(BaseModel):
    ...
    egress: EgressPolicy = Field(default_factory=EgressPolicy)
```

(LLM `LiteLLMConfig.allowed_api_hosts: list[str]` is specified but deferred to slice 6, since its `api_key` is a raw string, not a SecretRef, and needs a parallel scrub — see §5.)

### 4.2 `forge_security/egress/classify.py` — classification (hoisted + extended)

```python
_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]   # +100.100.100.200/32, +192.0.0.192/32, +fd00:ec2::254/128
BLOCKED_HOSTNAMES: frozenset[str]                                            # + "metadata.google.internal" etc. (already present)
BLOCKED_HOSTNAME_SUFFIXES: tuple[str, ...]

def is_internal_ip(ip: _IpAddress) -> bool: ...          # was _is_internal_ip
def candidate_ips(host: str) -> list[_IpAddress]: ...    # was _candidate_ips (getaddrinfo + inet_aton + ipv4_mapped)
def is_blocked_hostname(host: str) -> bool: ...

def validate_endpoint(url: str, policy: EgressPolicy | None = None) -> bool:
    """Generalized validate_peer_endpoint. Deny internal/metadata (always);
    if policy.require_https, deny non-https; if policy.allowed_hosts non-empty,
    deny hosts not matched by it. Cheap first-line/UX check — NOT the
    authoritative enforcement point (the transport is)."""
```

`forge_gateway/auth.py` becomes:
```python
from forge_security.egress import validate_endpoint
def validate_peer_endpoint(endpoint: str) -> bool:      # unchanged public behavior
    return validate_endpoint(endpoint)
```

### 4.3 `forge_security/egress/transport.py` — connect-time pinning

```python
class SSRFConnectBlocked(httpcore.ConnectError):
    """Raised inside connect_tcp when the resolved address is internal or
    outside policy. Surfaces to callers as httpx.ConnectError."""

class GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        policy: EgressPolicy | None = None,
        inner: httpcore.AsyncNetworkBackend | None = None,   # defaults to AutoBackend()
    ) -> None: ...
    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Resolve+validate host off the event loop (anyio.to_thread.run_sync
        over candidate_ips); raise SSRFConnectBlocked if any candidate is
        internal or policy-denied; else connect the INNER backend to the
        pinned literal IP. No second resolution => no rebind."""
    async def connect_unix_socket(self, *a: Any, **k: Any) -> httpcore.AsyncNetworkStream:
        raise SSRFConnectBlocked("unix sockets are not permitted for egress")
    async def sleep(self, seconds: float) -> None: ...

class SSRFGuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, policy: EgressPolicy | None = None,
                 verify: ssl.SSLContext | bool = True, **kw: Any) -> None:
        super().__init__(verify=verify, **kw)
        self._pool._network_backend = GuardedBackend(policy)   # connections are lazy => applies to all

def make_guarded_client(
    *, policy: EgressPolicy | None = None,
    verify: ssl.SSLContext | bool = True,
    **kw: Any,
) -> httpx.AsyncClient:
    """The ONLY sanctioned way to build an outbound client. Defaults
    follow_redirects=False (defense-in-depth; the backend re-validates hops
    anyway) and a connect+read timeout. Pass verify=<ssl_context> for the
    mTLS peer path — SNI/cert-verify still bind to the original hostname."""
```

### 4.4 `forge_security/egress/binding.py` — secret→destination binding

```python
@dataclass(frozen=True)
class BoundCredential:
    headers: Mapping[str, str]          # resolved auth headers (secret material)
    allowed_hosts: frozenset[str]       # exact host / host:port / "*.suffix"; derived from BASE if none explicit
    action: EgressAction                # REJECT | DROP on violation
    def header_keys(self) -> frozenset[str]: ...   # the keys DROP would strip

    @classmethod
    def none(cls) -> "BoundCredential":            # AuthType.NONE => empty, unbound, no-op
        ...

class EgressViolationError(Exception):
    """Credential is bound; the final resolved destination host is not in the
    bound set and action == REJECT. Raised BEFORE the request is sent."""

def host_matches(host: str, patterns: frozenset[str]) -> bool:
    """Case-insensitive hostname match; optional :port; '*.suffix' wildcard."""

def enforce_binding(
    final_url: str, cred: BoundCredential, *, policy: EgressPolicy,
) -> dict[str, str]:
    """Called AFTER template/param/path substitution, BEFORE the request.
    1. Apply the GLOBAL policy allowlist/require_https to final_url's host.
    2. If cred has bound hosts and final host not matched:
         REJECT -> raise EgressViolationError; DROP -> return headers minus
         cred.header_keys().
    Returns the headers to actually attach."""
```

### 4.5 Credential resolution seam

A new function alongside the existing `_resolve_auth_headers` (which stays until OpenAPI is migrated in slice 4):

```python
# forge_agent/builder/ (shared, next to _resolve_auth_headers)
def resolve_bound_credential(
    auth: AuthConfig, resolver: SecretResolver | None, *, declared_host: str | None,
) -> BoundCredential:
    """Resolve auth headers once (fail-fast, as today) AND compute the bound
    host set: auth.allowed_hosts if non-empty, else {declared_host} — the host
    of the BASE-declared destination. Capturing from BASE is what stops a later
    runtime URL repoint from ALSO moving the binding."""
```

---

## 5. Enforcement points per sink

| Sink | Guard wiring | Binding check | Slice |
|---|---|---|---|
| **Manual tools** | `_execute_api_call` builds `make_guarded_client(policy=...)` instead of bare `AsyncClient`; **structural**: forbid `{{param}}` in URL scheme/authority (template only path/query) | `resolve_bound_credential(declared_host=host(resolved_url))` at build; `enforce_binding(final_url, ...)` after `_resolve_template_string`, before `client.request` | **3 (this pass)** |
| **OpenAPI tools** | `make_guarded_client` for both `_fetch_remote_spec` and `_execute_openapi_call`; **re-validate `servers[0].url`** against the guard and pin to `source.base_url` when present (never trust spec content verbatim) | `resolve_bound_credential(declared_host=host(base_url))`; `enforce_binding` in `_execute_openapi_call` after URL assembly | 4 |
| **`/v1/admin/tools/preview`** | uses the same guarded `_fetch_remote_spec`; additionally require `config:write` not `config:read` | — | 4 |
| **A2A peers** | wire `make_guarded_client` into the **non-mTLS fallback** branch of `_resolve_client` and into `ping_peer`; keep the mTLS SPIFFE pre-send probe unchanged | n/a (mTLS identity, not header secret) | 5 |
| **LLM / litellm** | validate `api_base` host via `validate_endpoint` at `_build_model` before constructing the provider; `LiteLLMConfig.allowed_api_hosts`; recommend SIDECAR/EXTERNAL as prod default + NetworkPolicy | n/a for headers, but a **parallel scrub**: `api_key` is a raw string, not a SecretRef, so `loader.py::secret_refs()`/overlay guard must gain a bare-`api_key`/`api_base` check if `model_list` is ever loosened | 6 |
| **Overlay write-time (belt-and-suspenders)** | reject any overlay mutation moving a credentialed sink's destination host outside the credential's bound/BASE host | 7 |

---

## 6. Decomposed implementation plan (ordered, TDD, tests per slice)

**Slice 1 — Core guard module (`forge_security.egress`).** `classify.py` (hoist + extend blocklist), `transport.py` (`GuardedBackend`, `SSRFGuardedTransport`, `make_guarded_client`), `binding.py` (`BoundCredential`, `host_matches`, `enforce_binding`, `EgressViolationError`).
Tests: `test_is_internal_ip_covers_multicloud_metadata` (169.254.169.254, 100.100.100.200, 192.0.0.192, fd00:ec2::254); `test_candidate_ips_canonicalizes_encodings` (decimal/hex/octal/short-dotted/IPv4-mapped); **`test_rebind_public_then_private_is_blocked`** (monkeypatch `candidate_ips`/`getaddrinfo` → PUBLIC on any validate call, PRIVATE at connect → assert `SSRFConnectBlocked`) — the exact scenario the current suite cannot express; `test_guarded_backend_pins_literal_ip` (assert inner backend receives an IP literal, not the name); `test_sni_and_host_preserved` (TLS `server_hostname`/`Host` still the original name); `test_redirect_hop_revalidated`; `test_unix_socket_rejected`; `test_host_matches_wildcard_and_port`; **`test_enforce_binding_reject_raises`** / `test_enforce_binding_drop_strips_headers`; `test_backend_invoked_smoke` (asserts `GuardedBackend.connect_tcp` actually runs — fails loudly if a future httpcore refactor renames `_pool`/`_network_backend`).

**Slice 2 — Config schema (`forge_config`).** `EgressAction`, `EgressPolicy`, `AuthConfig.allowed_hosts`/`on_egress_violation`, `SecurityConfig.egress`.
Tests: **`test_existing_config_unchanged`** (no `egress`/`allowed_hosts` fields → parses, defaults REJECT + enabled + require_https); `test_egress_policy_defaults`; `test_authconfig_allowed_hosts_optional`; `test_egress_action_enum_values`.

**Slice 3 — Manual builder integration (this pass, see §7).**

**Slice 4 — OpenAPI builder + preview endpoint.** Guarded client for spec fetch and operation calls; `servers[0].url` re-validation + pin-to-`source.base_url`; `resolve_bound_credential`/`enforce_binding` in `_execute_openapi_call`; `preview` → `config:write` + guarded fetch.
Tests: `test_spec_fetch_ssrf_blocked`; **`test_hostile_servers_url_revalidated`**; `test_operation_call_binding_enforced`; **`test_preview_requires_config_write`**; **`test_preview_spec_fetch_ssrf_blocked`**.

**Slice 5 — A2A peers non-mTLS + ping.** `make_guarded_client` in the `identity is None` fallback and `ping_peer`; mTLS path untouched.
Tests: `test_nonmtls_peer_call_ssrf_guarded`; `test_ping_peer_ssrf_guarded`; **`test_mtls_spiffe_pinning_unchanged`**.

**Slice 6 — LLM / litellm.** `api_base` host validation at `_build_model`; `allowed_api_hosts`; the bare-`api_key`/`api_base` scrub in `loader.py`/overlay; docs recommending SIDECAR/EXTERNAL as prod default.
Tests: `test_embedded_api_base_validated`; `test_allowed_api_hosts_enforced`; **`test_overlay_cannot_move_api_base_host`**.

**Slice 7 — Admin routes: runtime-editable destinations.** Make manual/openapi `url`/`base_url` overlay-editable *guarded* by a write-time binding check (destination host must stay within the credential's bound/BASE host, else `OverlayFieldError`/400); auth.py re-export.
Tests: **`test_overlay_repoint_to_unbound_host_rejected`**; `test_overlay_repoint_within_bound_host_allowed`; `test_validate_peer_endpoint_still_delegates`.

**Slice 8 — Migration + defense-in-depth.** `forge.yaml.example` egress stanza + docs (`docs/`); Helm NetworkPolicy default-deny egress in `deploy/helm/forge` with explicit allows; ensure no dev bypass flag leaks into `values.dev.yaml`.
Tests: `test_networkpolicy_renders_default_deny` (helm template); doc lint.

---

## 7. CORE_SLICE_SCOPE — build in THIS pass (slices 1 + 2 + 3, all TDD)

Small enough to build correctly and verify adversarially; touches exactly one credentialed sink end-to-end.

**Build:**
1. **`forge_security.egress` module (slice 1)** — the full guard: `classify.py`, `transport.py`, `binding.py`, and `forge_security/egress/__init__.py` exporting `make_guarded_client`, `SSRFGuardedTransport`, `GuardedBackend`, `SSRFConnectBlocked`, `validate_endpoint`, `is_internal_ip`, `candidate_ips`, `BoundCredential`, `EgressViolationError`, `host_matches`, `enforce_binding`. Move the classifier out of `forge_gateway/auth.py`; make `validate_peer_endpoint` a one-line re-export (do NOT change its public behavior; the existing `test_auth.py` peer tests must stay green).
2. **Config schema (slice 2)** — `EgressAction`, `EgressPolicy`, `AuthConfig.allowed_hosts`/`on_egress_violation`, `SecurityConfig.egress`.
3. **Manual builder integration (slice 3)**:
   - `ManualToolBuilder.__init__` gains `egress_policy: EgressPolicy | None = None` (threaded from the registry, which reads `config.security.egress`).
   - `build()` computes `bound = resolve_bound_credential(api_config.auth, self._secret_resolver, declared_host=urlsplit(api_config.resolved_url).hostname)` instead of the bare `auth_headers` dict.
   - `_execute_api_call` signature changes from `auth_headers: dict[str,str]` to `bound: BoundCredential, egress_policy: EgressPolicy | None`; after computing the final `url`, call `headers = dict(enforce_binding(url, bound, policy=egress_policy or EgressPolicy())); headers.update(config_headers...)`; build the client via `make_guarded_client(policy=egress_policy)` when none injected.
   - **Structural hardening**: reject `{{param}}` placeholders in the scheme/authority of `resolved_url` at build time (template may only touch path/query) — closes the caller-supplied-host vector at the source, independent of the runtime binding check.

**Explicitly deferred (NOT this pass):** OpenAPI, peers, LLM, admin runtime-editability, overlay write-time guard, Helm NetworkPolicy. Slice 3 leaves the OpenAPI/peer/LLM sinks exactly as today (still guarded by their existing controls or lack thereof) — no regression, and the shared module is proven on the manual sink first.

**Adversarial acceptance for this pass:**
- A manual tool whose auth is bound (explicitly or by BASE default) to `api.example.com` refuses (`EgressViolationError`) when its `resolved_url` host is templated/edited to `evil.example` — and drops the header instead if `on_egress_violation: drop`.
- A manual tool call to a host that DNS-rebinds to `169.254.169.254` between validate and connect is refused at the socket (`SSRFConnectBlocked`), asserted by the rebind regression test.
- A pre-existing `forge.yaml` manual tool (no `egress`/`allowed_hosts` fields) behaves identically, its credential now silently pinned to the BASE-declared host.

---

## 8. Security review (threat table)

| Threat | Manifestation here | Mitigation in this design |
|---|---|---|
| **DNS rebind / TOCTOU** | validate-time lookup returns public IP; connect-time lookup returns 169.254.169.254 | connect-time pinning in `GuardedBackend.connect_tcp`: one resolution, connect the literal validated IP — window eliminated, not narrowed |
| **Non-canonical IP encoding** | `0x7f000001`, `2130706433`, `127.1`, `::ffff:169.254.169.254` slip a naive parser | `candidate_ips` canonicalizes via `inet_aton` + `ipv4_mapped`, and now runs at CONNECT time too |
| **Multi-cloud metadata** | GCP/Alibaba/Oracle/AWS-IPv6 IMDS beyond AWS's 169.254.169.254 | blocklist extended to `100.100.100.200`, `192.0.0.192`, `fd00:ec2::254`, `metadata.google.internal` |
| **Credential exfiltration (confused deputy)** | overlay repoint, `{{host}}` template, or hostile `servers[0].url` sends the operator's token elsewhere | `enforce_binding` on the FINAL resolved URL against the BASE-derived bound host — REJECT (default) or DROP |
| **Caller-supplied host in template** | `{{host}}` in `resolved_url` filled by a tool arg, no config write | structural: template forbidden in scheme/authority (slice 3); binding check as backstop |
| **Redirect to internal** | validated public host 3xx-redirects to `169.254.169.254` | redirect-safe by construction (each hop re-enters `GuardedBackend`); `follow_redirects=False` default too |
| **Preview SSRF-read** | `config:read` caller points `source.url` at IMDS via `_fetch_remote_spec` | guarded fetch + `config:write` (slice 4) |
| **LLM api_base repoint** | raw `api_key` follows an edited `api_base`; bypasses `secret_refs()` (not a SecretRef) | `api_base` host validation + parallel bare-string scrub (slice 6) |
| **httpcore private-attr coupling** | future httpcore renames `_pool`/`_network_backend`; guard silently no-ops | pin httpx/httpcore; `test_backend_invoked_smoke` asserts the backend actually runs (fails loudly) |
| **App-guard bypass (precedent: LiteLLM Pwn2Own, MCP Atlassian)** | app-level SSRF guards have been bypassed in the wild | pair with K8s NetworkPolicy default-deny egress (slice 8) — app guard is necessary, not sufficient |

**Residual / open items:**
1. **`getaddrinfo` blocking in the async hot path** — every tool call now resolves through `anyio.to_thread.run_sync`; benchmark thread-pool pressure under high tool-call concurrency (a resolver cache with short TTL, itself re-validated, is a possible follow-up — but a cache reintroduces a rebind window if not re-validated on use, so default OFF).
2. **Timeouts under an allowlisted-but-slow host** — pinning does not prevent slow-loris resource exhaustion; keep the existing 30s tool timeout and a connect timeout in `make_guarded_client`.
3. **LLM `api_key` is not a SecretRef** — a structural gap; whether to migrate `litellm_params.api_key` to a SecretRef (so it flows through the same resolver/scrub machinery) is an owner decision for slice 6.
4. **Global vs. per-credential allowlist precedence** — confirm the intended composition: global `EgressPolicy.allowed_hosts` is an *additional* gate (intersection), never a widening of a narrower per-credential binding.

---

## 9. Backward compatibility & migration

- **Default REJECT + derive-bound-host-from-BASE** means every pre-existing `forge.yaml` parses unchanged and its credentials become *safer* (pinned to the host already declared) with no rewrite. A runtime repoint to an unbound host now fails closed; operators widen intentionally by listing `auth.allowed_hosts`.
- **`EgressPolicy.enabled: true` by default, `allowed_hosts: []`** means the IP-layer deny (internal/metadata) is always on, but no positive host-allowlist is imposed until an operator opts in — so legitimate public API calls keep working while SSRF-to-internal is closed immediately.
- **`validate_peer_endpoint` unchanged** — the peer write-time UX check keeps its exact signature/behavior via re-export; ADR-0004's peer flow is untouched.
- **`forge.yaml.example`** gains a documented `security.egress` stanza and a per-tool `auth.allowed_hosts` example (slice 8).

---

## 10. Consequences

**Positive**: one shared, correctly-layered egress control replaces zero-guard sinks; the DNS-rebind window is *eliminated* (not narrowed) via connect-time pinning; credentials can no longer be steered to an unbound host; the peer mTLS model is preserved and generalized rather than collapsed; backward compatible and fail-closed by default; every slice is independently TDD-verifiable, starting with a single sink.

**Negative / limitations**: reaching into httpcore private attributes (`_pool._network_backend`) is a version-coupling risk (mitigated by a pin + smoke test); `getaddrinfo` now runs per tool call off the event loop (thread-pool pressure to benchmark); app-level guarding is necessary but not sufficient (Helm NetworkPolicy is a required follow-up, slice 8); the LLM `api_key`-as-raw-string gap remains until slice 6.

**Tech debt / deferred**: OpenAPI/peers/LLM integration (slices 4–6); runtime-editable destinations with write-time binding guard (slice 7); NetworkPolicy default-deny egress + docs (slice 8); optional re-validated resolver cache; migrating `litellm_params.api_key` to a SecretRef.
