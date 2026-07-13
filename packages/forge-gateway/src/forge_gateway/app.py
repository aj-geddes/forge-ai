"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from forge_config.schema import OIDCConfig, ServiceToken, SessionConfig
from forge_security.oidc import (
    Authorizer,
    DiscoveryDocument,
    JwksCache,
    OIDCTokenVerifier,
    OIDCVerifierConfig,
    ServiceTokenVerifier,
    SessionCodec,
    fetch_discovery_document,
    resolve_endpoints,
)
from forge_security.workload import WorkloadUnavailable, build_workload_plane

from forge_gateway import security
from forge_gateway.middleware.csrf import CSRFMiddleware
from forge_gateway.middleware.logging import RequestLoggingMiddleware
from forge_gateway.routes import (
    a2a,
    admin,
    conversational,
    health,
    mcp,
    metrics,
    programmatic,
)
from forge_gateway.routes import (
    auth as auth_routes,
)
from forge_gateway.routes import (
    tokens as tokens_routes,
)
from forge_gateway.workload import (
    WorkloadListenerHandle,
    WorkloadListenerStartupError,
    start_workload_listener,
)

if TYPE_CHECKING:
    from forge_security.oidc.user_tokens import UserTokenStore

logger = logging.getLogger("forge.gateway")

_DEV_INSECURE_LOG_INTERVAL_SECONDS = 60

_WORKLOAD_TEST_MODE_ENV_VAR = "FORGE_WORKLOAD_TEST_MODE"


def _make_reload_callback(
    config_path: str,
    agent: object | None = None,
) -> Callable[[Path], None]:
    """Create a config-reload callback bound to a specific config path and agent.

    The returned callable accepts a ``Path`` argument (provided by ConfigWatcher)
    and reloads the config, updating admin state, auth wiring, tool surface,
    MCP server, and the A2A agent card.

    The agent reference is captured in the closure so that async tool rebuilds
    can be scheduled when the config file changes on disk.
    """

    def _on_config_change(changed_path: Path) -> None:
        logger.info("Config file changed: %s, triggering reload", changed_path)
        try:
            from forge_config import load_config

            new_config = load_config(str(changed_path))
            logger.info("Reloaded config: %s", new_config.metadata.name)

            # Preserve the current agent reference across config reloads
            admin.set_state(config=new_config, config_path=config_path)
            programmatic.set_config(new_config)
            conversational.set_config(new_config)
            _schedule_auth_reinit(new_config)

            # Schedule async tool surface + MCP rebuild
            _schedule_tool_rebuild(new_config, agent)

            _refresh_agent_card(new_config, agent)
        except Exception:
            logger.exception("Failed to reload config from %s", changed_path)

    return _on_config_change


def _schedule_tool_rebuild(config: object, agent: object | None) -> None:
    """Schedule an async rebuild of the tool surface and MCP server.

    Called from the synchronous config-change callback.  Uses
    ``asyncio.ensure_future`` to run the coroutine on the current event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("No running event loop — skipping tool surface rebuild")
        return

    asyncio.ensure_future(_rebuild_tool_surface(config, agent))


def _schedule_auth_reinit(config: object) -> None:
    """Schedule an async re-initialization of the auth subsystem.

    Called from the synchronous config-change callback -- rebuilding the
    JWKS cache / OIDC verifier / session codec involves an (optional)
    discovery-document fetch, so it must run on the event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("No running event loop — skipping auth subsystem reinit")
        return

    asyncio.ensure_future(_init_auth(config))


async def _rebuild_tool_surface(config: object, agent: object | None) -> None:
    """Rebuild the agent tool surface and MCP server from a new config.

    Each step is independent and errors are logged without propagating,
    so a failure in one subsystem does not block the others.
    """
    from forge_config.schema import ForgeConfig

    if not isinstance(config, ForgeConfig):
        return

    # 1. Rebuild the agent's tool registry
    if agent is not None:
        try:
            registry = getattr(agent, "_registry", None)
            if registry is not None:
                swapped = await registry.build_and_swap(config)
                if swapped:
                    logger.info(
                        "Tool surface rebuilt: %d tools (version %s)",
                        registry.tool_count,
                        registry.version,
                    )
                else:
                    logger.info("Tool surface unchanged, skipping rebuild")
            else:
                logger.debug("Agent has no _registry attribute, skipping tool rebuild")
        except Exception:
            logger.exception("Failed to rebuild tool surface during config reload")

    # 2. Rebuild the MCP server with updated tools and activate it at /mcp,
    # so the live endpoint actually serves the new tool surface (not just an
    # inert module-level reference -- see routes.mcp.rebuild_and_activate).
    if agent is not None:
        try:
            registry = getattr(agent, "_registry", None)
            if registry is not None:
                await mcp.rebuild_and_activate(registry)
                logger.info("MCP server rebuilt with %d tools", registry.tool_count)
        except Exception:
            logger.exception("Failed to rebuild MCP server during config reload")


def _refresh_agent_card(config: object | None, agent: object | None = None) -> None:
    """Build an A2A agent card from *config* and set it for discovery.

    Called during startup and on config hot-reload so the ``/a2a/agent-card``
    endpoint always reflects the current configuration.
    """
    try:
        from forge_gateway.routes.a2a import build_agent_card, set_agent_card

        card = build_agent_card(config, agent)
        set_agent_card(card)
        logger.info(
            "A2A agent card set: name=%s, capabilities=%d",
            card.name,
            len(card.capabilities),
        )
    except Exception:
        logger.exception("Failed to build A2A agent card")


def _resolve_legacy_service_tokens(config: object) -> list[ServiceToken]:
    """ADR-0001 SS11: translate ``security.api_keys`` (deprecated, accepted
    for one minor release) into synthetic ``legacy-api-key`` service
    tokens with ``admin`` role. Live config has ``api_keys.enabled: false``,
    so nothing depends on this in production -- it exists only to protect
    any local/undocumented use during the migration window."""
    import hashlib

    from forge_config.schema import ForgeConfig

    if not isinstance(config, ForgeConfig):
        return []

    api_keys = config.security.api_keys
    if not api_keys.enabled or not api_keys.keys:
        return []

    from forge_agent.agent.core import build_default_secret_resolver

    resolver = build_default_secret_resolver()
    tokens: list[ServiceToken] = []
    for i, ref in enumerate(api_keys.keys):
        try:
            plaintext = resolver.resolve(ref)
        except Exception:
            logger.warning("Failed to resolve legacy api_keys secret: %s", ref.name)
            continue
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        tokens.append(ServiceToken(id=f"legacy-api-key-{i}", secret_sha256=digest, roles=["admin"]))
    return tokens


_FORGE_OIDC_CA_BUNDLE_ENV_VAR = "FORGE_OIDC_CA_BUNDLE"
_SSL_CERT_FILE_ENV_VAR = "SSL_CERT_FILE"


def _oidc_ca_verify() -> str | bool:
    """Resolve the CA trust bundle for the gateway's OIDC (Dex) httpx clients.

    Dex (``dex.hvslocal``) presents a certificate signed by the hvslocal
    private Root CA, which is not in the public CA bundle httpx/certifi
    trusts by default -- without this, OIDC discovery, JWKS fetches, and
    the Dex token exchange all fail with
    ``SSL: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``.

    Resolution order for httpx's ``verify=`` kwarg:

    1. ``FORGE_OIDC_CA_BUNDLE`` if set and the file exists -- deploy-time
       mount of the hvslocal Root CA (see docs/adr/0001-dex-oidc-authentication.md).
    2. ``SSL_CERT_FILE`` (the standard OpenSSL/platform convention) if set
       and the file exists.
    3. Otherwise ``True`` -- the httpx/certifi default public-CA behavior,
       which preserves existing tests and non-Dex deployments.

    A configured-but-missing path is a misconfiguration, not license to
    disable verification: it is logged at WARNING and resolution falls
    through to the next step, never to ``False``. ``verify=False`` must
    never be returned here -- that would disable TLS verification
    entirely rather than trusting a specific private CA.
    """
    bundle = os.environ.get(_FORGE_OIDC_CA_BUNDLE_ENV_VAR)
    if bundle:
        if os.path.isfile(bundle):
            logger.info("OIDC HTTP client trusting CA bundle: %s", bundle)
            return bundle
        logger.warning(
            "%s=%s does not exist; ignoring it and falling back to the next "
            "CA trust source (Dex TLS may fail if it uses a private CA)",
            _FORGE_OIDC_CA_BUNDLE_ENV_VAR,
            bundle,
        )

    ssl_cert_file = os.environ.get(_SSL_CERT_FILE_ENV_VAR)
    if ssl_cert_file:
        if os.path.isfile(ssl_cert_file):
            logger.info(
                "OIDC HTTP client trusting CA bundle (%s): %s",
                _SSL_CERT_FILE_ENV_VAR,
                ssl_cert_file,
            )
            return ssl_cert_file
        logger.warning(
            "%s=%s does not exist; ignoring it and falling back to default public CA verification",
            _SSL_CERT_FILE_ENV_VAR,
            ssl_cert_file,
        )

    return True


def _httpx_verify_kwarg(resolved: str | bool) -> ssl.SSLContext | bool:
    """Adapt :func:`_oidc_ca_verify`'s resolved value to httpx's ``verify=``
    kwarg without triggering httpx's ``verify=<str>`` ``DeprecationWarning``
    (httpx expects ``ssl.SSLContext | bool``, not a bare path string).

    A resolved bundle *path* becomes a default SSL context pinned to that
    CA file (``ssl.create_default_context(cafile=...)``), which is exactly
    what httpx itself does internally for a string ``verify=`` today --
    this only does it explicitly, ahead of the deprecation. ``True`` (no
    bundle configured -- trust the public CA store) passes through
    unchanged. This function is never handed ``False`` by
    :func:`_oidc_ca_verify` and never returns ``False`` itself -- TLS
    verification must never be silently disabled.
    """
    if isinstance(resolved, str):
        return ssl.create_default_context(cafile=resolved)
    return resolved


def _build_oidc_verifier(
    oidc_config: OIDCConfig, http_client: httpx.AsyncClient
) -> OIDCTokenVerifier:
    jwks_cache = JwksCache(
        http_client=http_client,
        jwks_uri=oidc_config.jwks_uri,
        cache_ttl_seconds=oidc_config.jwks_cache_ttl_seconds,
        min_refresh_seconds=oidc_config.jwks_min_refresh_seconds,
        stale_grace_seconds=oidc_config.jwks_stale_grace_seconds,
    )
    verifier_config = OIDCVerifierConfig(
        issuer=oidc_config.issuer,
        audience=oidc_config.audience or oidc_config.client_id,
        client_id=oidc_config.client_id,
        allowed_algorithms=list(oidc_config.allowed_algorithms),
        clock_skew_seconds=oidc_config.clock_skew_seconds,
    )
    return OIDCTokenVerifier(jwks_cache=jwks_cache, config=verifier_config)


def _build_session_codec(session_config: SessionConfig) -> tuple[SessionCodec, bytes]:
    from forge_agent.agent.core import build_default_secret_resolver

    if session_config.encryption_key is None:
        msg = "security.oidc.session.encryption_key is not configured"
        raise RuntimeError(msg)

    resolver = build_default_secret_resolver()
    key = resolver.resolve(session_config.encryption_key).encode("utf-8")
    previous_key = None
    if session_config.previous_encryption_key is not None:
        previous_key = resolver.resolve(session_config.previous_encryption_key).encode("utf-8")

    return (
        SessionCodec(
            key=key,
            previous_key=previous_key,
            lifetime_seconds=session_config.lifetime_seconds,
            idle_timeout_seconds=session_config.idle_timeout_seconds,
        ),
        key,
    )


async def _init_auth(config: object | None) -> None:
    """Build and wire the auth subsystem from the loaded config (ADR-0001).

    Unlike the retired ``_init_security_gate``, there is no
    ``except Exception: set_security_gate(None)`` fallback to an
    unauthenticated dev mode. A subsystem that fails to initialize is
    recorded as unhealthy; :func:`forge_gateway.security.get_principal`
    then denies every request with ``503 auth_unavailable`` and
    ``/health/ready`` reports NOT READY, so a broken pod never takes
    traffic.
    """
    from forge_config.schema import ForgeConfig

    if config is None or not isinstance(config, ForgeConfig):
        security.reset_auth()
        health.set_auth_healthy(False)
        return

    sec_config = config.security
    dev_insecure = security.is_dev_insecure_active(sec_config)

    oidc_verifier: OIDCTokenVerifier | None = None
    session_codec = None
    http_client: httpx.AsyncClient | None = None
    endpoints: DiscoveryDocument | None = None
    tx_key: bytes | None = None

    if sec_config.oidc.enabled:
        http_client = httpx.AsyncClient(verify=_httpx_verify_kwarg(_oidc_ca_verify()))
        discovery_doc = None
        if sec_config.oidc.discovery:
            try:
                discovery_doc = await fetch_discovery_document(http_client, sec_config.oidc.issuer)
            except Exception:
                logger.warning(
                    "OIDC discovery failed; relying on explicit endpoint config", exc_info=True
                )
        try:
            endpoints = resolve_endpoints(sec_config.oidc, discovery_doc)
        except Exception:
            logger.exception("Failed to resolve OIDC endpoints")
            endpoints = None

        try:
            oidc_verifier = _build_oidc_verifier(sec_config.oidc, http_client)
        except Exception:
            logger.exception("Failed to initialize OIDC verifier")

        try:
            session_codec, tx_key = _build_session_codec(sec_config.oidc.session)
        except Exception:
            logger.exception("Failed to initialize session codec")

    user_token_config = sec_config.service_tokens.user_tokens
    user_token_store: UserTokenStore | None = None
    if user_token_config.enabled:
        from forge_security.oidc.user_tokens import FileUserTokenStore

        user_token_store = FileUserTokenStore(user_token_config.store_path)
        try:
            await user_token_store.load()
        except Exception:
            # FileUserTokenStore.load() is documented to never raise (it
            # marks itself unavailable internally on a corrupt file
            # instead) -- this is defence-in-depth only, matching every
            # other subsystem init step in this function.
            logger.exception(
                "Failed to load user token store at %s -- user-issued API "
                "keys are unavailable; static tokens and OIDC are unaffected",
                user_token_config.store_path,
            )

    try:
        all_tokens = list(sec_config.service_tokens.tokens) + _resolve_legacy_service_tokens(config)
    except Exception:
        logger.exception("Failed to resolve configured service tokens")
        all_tokens = []
    service_token_verifier = ServiceTokenVerifier(all_tokens, store=user_token_store)

    try:
        authorizer = Authorizer(sec_config.authorization)
    except Exception:
        logger.exception("Failed to build authorizer from config")
        from forge_config.schema import AuthorizationConfig

        authorizer = Authorizer(AuthorizationConfig())

    security.configure_auth(
        session_codec=session_codec,
        service_token_verifier=service_token_verifier,
        oidc_verifier=oidc_verifier,
        authorizer=authorizer,
        cookie_name=sec_config.oidc.session.cookie_name,
        dev_insecure=dev_insecure,
        metrics_public=sec_config.authorization.metrics_public,
        http_client=http_client,
        oidc_config=sec_config.oidc,
        endpoints=endpoints,
        tx_key=tx_key,
        rate_limit_rpm=sec_config.rate_limit_rpm,
        user_token_store=user_token_store,
        user_token_config=user_token_config,
        authorization_config=sec_config.authorization,
    )
    health.set_auth_mode(sec_config.auth.mode.value)
    health.set_auth_healthy(security.is_auth_healthy())
    if not security.is_auth_healthy():
        logger.critical(
            "Auth subsystem has no working credential mechanism (mode=enforce, no OIDC, "
            "no service tokens) — marking the gateway NOT READY."
        )


def _workload_test_mode() -> bool:
    """ADR-0004: unit/integration tests set ``FORGE_WORKLOAD_TEST_MODE=1``
    to build the ``WorkloadPlane`` with agentweave's own test doubles
    (no real SPIRE/OPA needed), mirroring the ``FORGE_DEV_INSECURE``
    pattern used for the human plane's dev_insecure mode. Never set in
    production."""
    return os.environ.get(_WORKLOAD_TEST_MODE_ENV_VAR) == "1"


async def _init_workload_plane(
    config: object | None, agent: object | None
) -> WorkloadListenerHandle | None:
    """Build the ``WorkloadPlane`` and start the ``:8443`` a2a-mtls
    listener, but only when ``security.agentweave.enabled`` (ADR-0004
    SS4, SS8).

    Every failure path here -- agentweave disabled, no config loaded,
    SPIRE unreachable (``WorkloadUnavailable``), or the listener port
    itself failing to bind (``WorkloadListenerStartupError``) -- ends the
    same way: this returns ``None``, records a non-gating
    ``health.set_workload_health(...)`` snapshot, and the caller
    (:func:`lifespan`) proceeds with human-plane startup completely
    unaffected. There is no path here that raises out to the lifespan and
    blocks ``:8000`` from starting.
    """
    from forge_config.schema import ForgeConfig

    if config is None or not isinstance(config, ForgeConfig):
        health.set_workload_health(None)
        return None

    agentweave_config = config.security.agentweave
    if not agentweave_config.enabled:
        health.set_workload_health(None)
        return None

    test_mode = _workload_test_mode()
    try:
        plane = await build_workload_plane(
            agentweave_config,
            test_mode=test_mode,
            agent_name=config.metadata.name,
        )
    except WorkloadUnavailable as exc:
        logger.critical(
            "workload plane: SPIFFE identity infrastructure unavailable (%s) -- the "
            ":8443 a2a-mtls listener will NOT start. The human :8000 plane is "
            "unaffected and stays Ready.",
            exc.code,
        )
        health.set_workload_health({"status": "unavailable", "reason": exc.code})
        return None

    try:
        handle = await start_workload_listener(plane, agent, config)
    except WorkloadListenerStartupError:
        logger.critical(
            "workload plane: :8443 a2a-mtls listener failed to start -- the human "
            ":8000 plane is unaffected and stays Ready.",
            exc_info=True,
        )
        health.set_workload_health({"status": "unavailable", "reason": "listener_startup_failed"})
        return None

    health.set_workload_health(await plane.health())
    logger.info("workload plane: :8443 a2a-mtls listener ready")
    return handle


async def _dev_insecure_heartbeat() -> None:
    """ADR-0001 SS7.2: log CRITICAL every 60s while dev_insecure is active,
    so it is impossible to be in this mode and not know it."""
    try:
        while True:
            await asyncio.sleep(_DEV_INSECURE_LOG_INTERVAL_SECONDS)
            if not security.is_dev_insecure():
                return
            logger.critical(
                "FORGE IS STILL RUNNING IN dev_insecure MODE — every request is "
                "authenticated with admin privileges."
            )
    except asyncio.CancelledError:
        pass


async def _init_mcp_server(agent: object, config: object) -> None:
    """Build the FastMCP server from the agent's tool registry and activate it.

    The ``/mcp`` mount itself is created once, in :func:`create_app`, and
    stays mounted for the process lifetime (see
    ``mcp.get_mcp_mount_app``); this function only builds a FastMCP server
    from the current tool registry and makes it the live app that mount
    dispatches to.

    Activation starts the FastMCP ASGI app's own lifespan, which is
    required for its streamable-HTTP session manager to work at all --
    without it, every call to ``/mcp`` fails with "Task group is not
    initialized" because the parent ASGI server never ran FastMCP's
    lifespan on its behalf. Failures are logged but do not prevent the
    gateway from starting.
    """
    try:
        from forge_agent import ForgeAgent
        from forge_config.schema import ForgeConfig

        if not isinstance(agent, ForgeAgent) or not isinstance(config, ForgeConfig):
            return

        server_name = config.metadata.name or "Forge AI"
        mcp_server = mcp.build_mcp_server(agent.registry, name=server_name)
        await mcp.activate(mcp_server)
        logger.info("MCP server active at /mcp with %d tools", agent.registry.tool_count)
    except ImportError:
        logger.debug("MCP dependencies not available, skipping MCP server")
    except Exception:
        logger.exception("Failed to initialize MCP server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: build tools on startup, drain on shutdown."""
    logger.info("Forge Gateway starting up")

    # Signal startup complete
    health.set_started(True)

    watcher = None
    dev_insecure_task: asyncio.Task[None] | None = None
    workload_handle: WorkloadListenerHandle | None = None

    try:
        # Try to initialize the agent from config
        config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")
        config = None
        agent = None

        try:
            from forge_config import load_config

            config = load_config(config_path)
            logger.info("Loaded config: %s", config.metadata.name)
            health.set_version(config.metadata.version)

            # Try to build the agent
            try:
                from forge_agent import ForgeAgent

                agent = ForgeAgent(config)
                await agent.initialize()
                health.set_component_status("agent", "ready")

                # Wire agent and config into all route modules
                programmatic.set_agent(agent)
                programmatic.set_config(config)
                conversational.set_agent(agent)
                conversational.set_config(config)
                a2a.set_agent(agent)

                # Build MCP server from the agent's tool registry and
                # activate it at the persistent /mcp mount.
                await _init_mcp_server(agent, config)

                logger.info("Agent initialized successfully")
            except ImportError:
                logger.warning("forge-agent not available, running in gateway-only mode")
                health.set_component_status("agent", "unavailable")
            except Exception:
                logger.exception("Failed to initialize agent")
                health.set_component_status("agent", "failed")

            # Populate the A2A agent card from config + live tool registry
            _refresh_agent_card(config, agent)

        except Exception:
            logger.warning("No config loaded, running with defaults")

        # Wire admin state and the auth subsystem
        admin.set_state(config=config, config_path=config_path, agent=agent)
        await _init_auth(config)

        if security.is_dev_insecure():
            dev_insecure_task = asyncio.ensure_future(_dev_insecure_heartbeat())

        # ADR-0004: build the workload (SPIFFE + OPA + mTLS) plane and
        # start the :8443 a2a-mtls listener, strictly additive to
        # everything above. Wrapped defensively (on top of
        # _init_workload_plane's own internal fail-closed handling) so
        # that even an unanticipated exception here can never prevent
        # the human :8000 plane from reaching Ready.
        try:
            workload_handle = await _init_workload_plane(config, agent)
        except Exception:
            logger.exception(
                "workload plane: unexpected failure building the workload plane -- "
                "the :8443 listener will not start. The human :8000 plane is "
                "unaffected and stays Ready."
            )
            health.set_workload_health({"status": "unavailable", "reason": "unexpected_error"})
            workload_handle = None

        # Start config file watcher for hot-reload
        if config is not None and Path(config_path).exists():
            try:
                from forge_config import ConfigWatcher

                callback = _make_reload_callback(config_path, agent=agent)
                watcher = ConfigWatcher(config_path, on_change=callback)
                watcher.start()
            except ImportError:
                logger.warning("ConfigWatcher not available, hot-reload disabled")
            except Exception:
                logger.exception("Failed to start config watcher, hot-reload disabled")

        health.set_ready(True)
        logger.info("Forge Gateway ready")

        yield

    finally:
        logger.info("Forge Gateway shutting down")
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                logger.exception("Error stopping config watcher")
        if dev_insecure_task is not None:
            dev_insecure_task.cancel()
        if workload_handle is not None:
            try:
                await workload_handle.stop()
            except Exception:
                logger.exception("Error stopping workload plane :8443 listener")
        try:
            await mcp.shutdown_active_asgi_app()
        except Exception:
            logger.exception("Error stopping MCP server")
        try:
            await security.shutdown_auth()
        except Exception:
            logger.exception("Error shutting down auth subsystem")
        health.set_ready(False)
        health.set_started(False)
        health.reset_components()


def _resolve_cors_origins() -> list[str]:
    """Read ``allowed_origins`` from the config file for CORS setup.

    Falls back to ``["*"]`` with a logged warning when the config cannot be
    loaded or no origins are explicitly configured. A real, successfully
    loaded config can never carry ``"*"`` alongside OIDC (rejected by
    ``SecurityConfig``'s validator at load time) -- see :func:`create_app`
    for the additional defence-in-depth applied to whatever this returns.
    """
    config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")
    try:
        from forge_config import load_config

        config = load_config(config_path)
        origins: list[str] = config.security.allowed_origins
        if origins:
            return origins
    except Exception:
        logger.debug("Could not load config for CORS origins, using permissive defaults")

    logger.warning("CORS allowed_origins not configured — defaulting to ['*'] (dev mode)")
    return ["*"]


# The Docker image copies the built UI to this fixed location (see
# Dockerfile). Kept as a module attribute (rather than inlined) so tests can
# monkeypatch it without touching the real filesystem.
_DOCKER_STATIC_DIR = Path("/app/static")


def _packages_dir() -> Path:
    """The workspace ``packages/`` directory (parent of this package)."""
    # .../packages/forge-gateway/src/forge_gateway/app.py -> packages/
    return Path(__file__).parent.parent.parent.parent


def _resolve_static_dir() -> Path | None:
    """Resolve the directory containing the built frontend SPA, if any.

    Checked in order:

    1. ``/app/static`` -- the Docker image location (UI copied at build
       time; see ``Dockerfile``). Always takes priority so the Docker path
       is never degraded.
    2. ``packages/forge-ui/dist`` -- the local ``npm run build`` output,
       so the gateway serves the UI when run directly (outside Docker)
       during development.
    3. ``packages/static`` -- a legacy location, kept for backward
       compatibility with any manual copy.

    Returns:
        The first candidate directory that exists, or ``None`` if no
        built UI is found anywhere.
    """
    packages_dir = _packages_dir()
    candidates = [
        _DOCKER_STATIC_DIR,
        packages_dir / "forge-ui" / "dist",
        packages_dir / "static",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _safe_static_path(static_dir: Path, requested_path: str) -> Path | None:
    """Resolve *requested_path* under *static_dir*, refusing any escape.

    ``Path(static_dir) / requested_path`` alone is not safe to serve
    directly: ``..`` segments can walk out of ``static_dir``, and joining
    with an *absolute* path silently discards the base entirely in
    pathlib (``Path("/a/b") / "/etc/passwd" == Path("/etc/passwd")``),
    which is a classic path-traversal footgun for user-supplied path
    segments such as FastAPI's ``{path:path}`` converter.

    Args:
        static_dir: The directory files must be contained within.
        requested_path: The user-supplied path segment (untrusted).

    Returns:
        The resolved, existing file path, only when it is a regular file
        genuinely contained within *static_dir*; otherwise ``None``.
    """
    try:
        resolved_static_dir = static_dir.resolve()
        candidate = (static_dir / requested_path).resolve()
    except OSError:
        return None

    if not candidate.is_relative_to(resolved_static_dir):
        return None
    if not candidate.is_file():
        return None
    return candidate


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Forge AI Gateway",
        description="Config-driven AI agent system with dynamic MCP tool surfaces",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware — CORS must be added before startup
    origins = _resolve_cors_origins()
    # ADR-0001 SS4.2 hard prerequisite: CORSMiddleware must never be
    # configured with allow_origins=['*'] while allow_credentials=True --
    # that combination reflects any origin, which is a total compromise
    # once a session cookie exists. A real config can never reach this
    # function with "*" while OIDC is enabled (SecurityConfig rejects it
    # at load time); this is the defence-in-depth for the "no config file
    # at all" dev fallback above.
    allow_credentials = "*" not in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        CSRFMiddleware,
        session_cookie_name=_resolve_session_cookie_name(),
        csrf_cookie_name=_resolve_csrf_cookie_name(),
        allowed_origins=origins,
    )
    app.add_middleware(security.DevInsecureHeaderMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # API Routes
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(tokens_routes.router)
    app.include_router(programmatic.router)
    app.include_router(conversational.router)
    app.include_router(a2a.router)
    app.include_router(metrics.router)
    app.include_router(admin.router)

    # MCP tool surface — mounted once, for the process lifetime. It starts
    # out dispatching to nothing (503) until the lifespan activates a real
    # FastMCP server; see mcp.get_mcp_mount_app / _init_mcp_server /
    # mcp.rebuild_and_activate.
    app.mount("/mcp", mcp.get_mcp_mount_app(), name="mcp")

    # Serve frontend SPA if a built UI directory can be found
    static_dir = _resolve_static_dir()

    if static_dir is not None:
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

        # SPA catch-all: serve index.html for client-side routes
        spa_index = static_dir / "index.html"

        # Known SPA client-side routes (React Router paths)
        spa_routes = {"", "config", "tools", "chat", "peers", "security", "guide", "login"}

        @app.get("/{path:path}", response_model=None)
        async def spa_fallback(request: Request, path: str) -> FileResponse | JSONResponse:
            """Serve index.html for SPA client-side routes, static files, or 404."""
            # Serve actual static files if they exist and stay within static_dir
            static_file = _safe_static_path(static_dir, path)
            if static_file is not None:
                return FileResponse(str(static_file))
            # Serve index.html for known SPA routes (no-cache so deploys take effect)
            if path in spa_routes:
                return FileResponse(
                    str(spa_index),
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                )
            # Everything else is a 404
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        logger.info("Serving UI from %s", static_dir)
    else:
        logger.warning(
            "No built frontend UI found (checked %s and %s) — the UI will not be "
            "served. Run `npm run build` in packages/forge-ui for local "
            "development, or use the Docker image.",
            _DOCKER_STATIC_DIR,
            _packages_dir() / "forge-ui" / "dist",
        )

    return app


def _resolve_session_cookie_name() -> str:
    config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")
    try:
        from forge_config import load_config

        config = load_config(config_path)
        return config.security.oidc.session.cookie_name
    except Exception:
        return "forge_session"


def _resolve_csrf_cookie_name() -> str:
    config_path = os.environ.get("FORGE_CONFIG_PATH", "forge.yaml")
    try:
        from forge_config import load_config

        config = load_config(config_path)
        return config.security.oidc.session.csrf_cookie_name
    except Exception:
        return "forge_csrf"
