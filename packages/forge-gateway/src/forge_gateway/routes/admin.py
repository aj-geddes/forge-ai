"""Admin API routes for the Forge AI control plane.

Provides endpoints for config management, tool inspection, session
management, and peer status — all consumed by the forge-ui frontend.

Read endpoints require the ``config:read`` permission; mutating endpoints
require ``config:write`` (ADR-0001 SS6.1). The static admin API key is
retired -- see ``forge_gateway.security``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from forge_config.schema import ForgeConfig
from pydantic import ValidationError

from forge_gateway import security
from forge_gateway.auth import validate_peer_endpoint
from forge_gateway.models import (
    AdminConfigResponse,
    AdminConfigUpdateRequest,
    AdminConfigUpdateResponse,
    AdminPeerResponse,
    AdminPeerStatus,
    AdminSessionResponse,
    AdminToolInfo,
    AdminToolPreviewRequest,
    AdminToolPreviewResponse,
    ErrorResponse,
)

logger = logging.getLogger("forge.gateway.admin")

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
)

_read = Depends(security.require_permission("config:read"))
_write = Depends(security.require_permission("config:write"))

# Module-level state set from app lifespan
_config: ForgeConfig | None = None
_config_path: str = ""
_agent: Any = None

_UNSET: Any = object()


def set_state(
    config: ForgeConfig | None,
    config_path: str,
    agent: Any = _UNSET,
) -> None:
    """Wire admin state from the application lifespan.

    Pass ``agent`` to update it, or omit it to preserve the current value.
    This allows config hot-reloads to update config without losing the agent.
    """
    global _config, _config_path, _agent
    _config = config
    _config_path = config_path
    if agent is not _UNSET:
        _agent = agent


# --- Config endpoints ---


@router.get(
    "/config",
    response_model=AdminConfigResponse,
    responses={500: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def get_config() -> AdminConfigResponse:
    """Return the current resolved config with secrets redacted."""
    if _config is None:
        raise HTTPException(status_code=500, detail="No config loaded")

    # Serialize then redact secret values
    data = _config.model_dump(mode="json")
    _redact_secrets(data)

    return AdminConfigResponse(config=data, path=_config_path)


@router.put(
    "/config",
    response_model=AdminConfigUpdateResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    dependencies=[_write],
)
async def update_config(request: AdminConfigUpdateRequest) -> AdminConfigUpdateResponse:
    """Validate, apply in-memory, rebuild tools, and best-effort persist to disk."""
    global _config

    # Preserve original secret refs — the client receives redacted values,
    # so we must restore the real SecretRef entries before applying.
    incoming = request.config
    if _config is not None:
        _restore_secrets(incoming, _config.model_dump(mode="json"))

    # Validate the proposed config
    try:
        new_config = ForgeConfig.model_validate(incoming)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Apply config in-memory immediately
    _config = new_config

    # Re-wire the auth subsystem so updated bindings/service tokens/OIDC
    # settings take effect immediately (ADR-0001).
    try:
        from forge_gateway.app import _schedule_auth_reinit

        _schedule_auth_reinit(new_config)
    except Exception:
        logger.exception("Failed to schedule auth subsystem reinit after config update")

    # Hot-reload: rebuild tool surface if agent is available
    reloaded = False
    if _agent is not None:
        try:
            registry = getattr(_agent, "_registry", None)
            if registry is not None:
                reloaded = await registry.build_and_swap(new_config)
        except Exception:
            logger.exception("Failed to reload tool surface after config update")

    # Best-effort persist to disk (may fail on read-only filesystems)
    persisted = False
    config_path = Path(_config_path or os.environ.get("FORGE_CONFIG_PATH", "forge.yaml"))
    try:
        yaml_str = yaml.dump(
            new_config.model_dump(mode="json", exclude_none=True),
            default_flow_style=False,
            sort_keys=False,
        )
        config_path.write_text(yaml_str, encoding="utf-8")
        persisted = True
    except OSError:
        logger.warning("Could not persist config to %s (read-only filesystem)", config_path)

    parts = ["Config applied"]
    if reloaded:
        parts.append("tools reloaded")
    if persisted:
        parts.append("saved to disk")
    else:
        parts.append("in-memory only (filesystem read-only)")

    return AdminConfigUpdateResponse(
        success=True,
        reloaded=reloaded,
        message=" · ".join(parts),
    )


@router.get("/config/schema", dependencies=[_read])
async def get_config_schema() -> dict[str, Any]:
    """Return JSON Schema for the ForgeConfig model."""
    schema: dict[str, Any] = ForgeConfig.model_json_schema()
    return schema


# --- Tools endpoints ---


@router.get(
    "/tools",
    response_model=list[AdminToolInfo],
    dependencies=[_read],
)
async def list_tools() -> list[AdminToolInfo]:
    """List all registered tools with metadata."""
    if _agent is None:
        return []

    registry = getattr(_agent, "_registry", None)
    if registry is None:
        return []

    tools: list[AdminToolInfo] = []
    for tool in registry.tools:
        tools.append(
            AdminToolInfo(
                name=tool.name,
                description=getattr(tool, "description", "") or "",
                source=_classify_tool_source(tool.name),
            )
        )
    return tools


@router.post(
    "/tools/preview",
    response_model=AdminToolPreviewResponse,
    responses={400: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def preview_tools(request: AdminToolPreviewRequest) -> AdminToolPreviewResponse:
    """Dry-run: parse an OpenAPI spec and return the tool list without registering."""
    try:
        from forge_agent.builder.openapi import OpenAPIToolBuilder
        from forge_config.schema import OpenAPISource

        source = OpenAPISource.model_validate(request.source)
        builder = OpenAPIToolBuilder(source)
        tools = await builder.build()

        return AdminToolPreviewResponse(
            tools=[
                AdminToolInfo(
                    name=t.name,
                    description=getattr(t, "description", "") or "",
                    source="openapi",
                )
                for t in tools
            ],
            count=len(tools),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# --- Sessions endpoints ---


@router.get(
    "/sessions",
    response_model=list[AdminSessionResponse],
    dependencies=[_read],
)
async def list_sessions() -> list[AdminSessionResponse]:
    """List active agent sessions."""
    if _agent is None:
        return []

    context = getattr(_agent, "_context", None)
    if context is None:
        return []

    sessions: list[AdminSessionResponse] = []
    session_store = getattr(context, "_sessions", {})
    for sid, session in session_store.items():
        msg_count = len(getattr(session, "messages", []))
        sessions.append(
            AdminSessionResponse(
                session_id=sid,
                message_count=msg_count,
                agent=getattr(session, "agent", None),
            )
        )
    return sessions


@router.delete(
    "/sessions/{session_id}",
    responses={404: {"model": ErrorResponse}},
    dependencies=[_write],
)
async def delete_session(session_id: str) -> dict[str, str]:
    """Terminate a session."""
    if _agent is None:
        raise HTTPException(status_code=404, detail="No agent available")

    context = getattr(_agent, "_context", None)
    if context is None:
        raise HTTPException(status_code=404, detail="No session context available")

    session_store = getattr(context, "_sessions", {})
    if session_id not in session_store:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    del session_store[session_id]
    return {"status": "deleted", "session_id": session_id}


# --- Peers endpoints ---


@router.get(
    "/peers",
    response_model=list[AdminPeerResponse],
    dependencies=[_read],
)
async def list_peers() -> list[AdminPeerResponse]:
    """List peers with connection status."""
    if _config is None:
        return []

    return [
        AdminPeerResponse(
            name=peer.name,
            endpoint=peer.endpoint,
            trust_level=peer.trust_level.value,
            capabilities=peer.capabilities,
            status=AdminPeerStatus.UNKNOWN,
        )
        for peer in _config.agents.peers
    ]


@router.post(
    "/peers/{name}/ping",
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    dependencies=[_read],
)
async def ping_peer(name: str) -> dict[str, Any]:
    """Health-check a specific peer.

    Only allows pinging peers that are in the configured peer list,
    and validates that peer endpoints don't target private/internal IPs.
    """
    if _config is None:
        raise HTTPException(status_code=404, detail="No config loaded")

    peer = next((p for p in _config.agents.peers if p.name == name), None)
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Peer '{name}' not found")

    # SSRF protection: validate the peer endpoint
    if not validate_peer_endpoint(peer.endpoint):
        raise HTTPException(
            status_code=400,
            detail="Peer endpoint targets a private or internal network",
        )

    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                peer.endpoint.rstrip("/") + "/health/live",
                timeout=5.0,
            )
            resp.raise_for_status()
            return {"name": name, "status": "reachable", "http_status": resp.status_code}
    except httpx.HTTPError as e:
        return {"name": name, "status": "unreachable", "error": str(e)}


# --- Helpers ---


def _classify_tool_source(name: str) -> str:
    """Heuristic to classify tool origin."""
    if name.startswith("peer_"):
        return "peer"
    return "configured"


# Security-review finding #1 (CRITICAL): forge_config.loader substitutes
# ${ENV_VAR} placeholders with their literal value BEFORE Pydantic
# validation (forge_config/loader.py::_substitute_env_vars). A value that
# was *meant* to be a SecretRef -- e.g.
# ``llm.litellm.model_list[].litellm_params.api_key`` -- lands in the
# validated model as an ordinary plaintext string inside a permissive
# ``dict[str, Any]`` field (``LiteLLMConfig.model_list``). The structural
# SecretRef redaction below only recognises the resolved-ref *shape*
# (``{"source": ..., "name": ..., "key": ...}``) and walks straight past a
# plain string, so GET /v1/admin/config leaked the real key.
#
# This is the fail-safe complement: redact by KEY NAME, recursively,
# regardless of the value's type or shape. Keys are matched exactly
# (case-insensitive) rather than by substring, so a benign field like
# ``token_id`` (a service token's non-secret label) is not swept up by a
# looser match on "token".
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "authorization",
        "client_secret",
        "private_key",
    }
)


def _is_sensitive_key(key: str) -> bool:
    """Case-insensitive, exact match against ``_SENSITIVE_KEY_NAMES``."""
    return key.lower() in _SENSITIVE_KEY_NAMES


def _is_secret_ref_shape(data: dict[str, Any]) -> bool:
    """True if *data* is a resolved ``SecretRef`` (``{"source": "env" |
    "k8s_secret", "name": ..., "key": ...}``)."""
    return "source" in data and "name" in data and data.get("source") in ("env", "k8s_secret")


def _redact_secrets(data: Any) -> None:
    """Recursively redact secret-shaped and secret-named values in place.

    Two independent, both-required redaction strategies (finding #1):

    1. Structural -- a resolved ``SecretRef`` (``{"source": "env" |
       "k8s_secret", "name": ..., "key": ...}``) is redacted by shape,
       preserving the dict's structure (only ``name``/``key`` are blanked).
    2. By key name -- any *scalar* leaf whose key matches
       :data:`_SENSITIVE_KEY_NAMES` is blanked, regardless of where in the
       tree it appears. ``None`` values are left as ``None`` (an unset
       credential is not the same as a redacted one).

    Container values (dict/list) are always recursed into first -- even
    under a sensitive key name -- so a SecretRef nested under e.g.
    ``{"token": {"source": "env", ...}}`` is redacted structurally rather
    than being wholesale-blanked into an opaque string.

    Callers MUST pass a freshly serialized copy (e.g.
    ``model.model_dump(mode="json")``), never the live config object --
    this function mutates *data* in place and the caller is responsible
    for that copy boundary.
    """
    if isinstance(data, dict):
        if _is_secret_ref_shape(data):
            data["name"] = "***REDACTED***"
            if "key" in data:
                data["key"] = "***REDACTED***"
            return
        for key, value in data.items():
            if isinstance(value, dict | list):
                _redact_secrets(value)
            elif _is_sensitive_key(key) and value is not None:
                data[key] = "***REDACTED***"
    elif isinstance(data, list):
        for item in data:
            _redact_secrets(item)


def _restore_secrets(incoming: Any, original: Any) -> None:
    """Restore redacted SecretRef values from the original config.

    When the UI reads config, secrets are redacted to ``***REDACTED***``.
    When it PUTs config back (e.g. after adding a tool), those redacted
    values would overwrite the real secret refs.  This function walks
    both trees and copies the original values back wherever a redacted
    placeholder is found.
    """
    if isinstance(incoming, dict) and isinstance(original, dict):
        # SecretRef node: restore if redacted
        if (
            incoming.get("source") in ("env", "k8s_secret")
            and incoming.get("name") == "***REDACTED***"
        ):
            if "name" in original:
                incoming["name"] = original["name"]
            if "key" in original and incoming.get("key") == "***REDACTED***":
                incoming["key"] = original["key"]
            return
        for k in incoming:
            if k in original:
                _restore_secrets(incoming[k], original[k])
    elif isinstance(incoming, list) and isinstance(original, list):
        for i, item in enumerate(incoming):
            if i < len(original):
                _restore_secrets(item, original[i])
