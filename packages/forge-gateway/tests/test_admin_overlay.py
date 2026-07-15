"""Tests for the layered BASE+OVERLAY config admin API under the Phase-1
FIELD-LEVEL split.

The overlay may only carry runtime-safe fields (agent full CRUD; tool
description/parameters/response_mapping; llm default_model/temperature/
max_tokens/system_prompt/fallback_models; peer capabilities/trust_level/
spiffe_id on an EXISTING base peer; metadata.description). Every base-only
FIELD -- a destination (tool url/base_url/endpoint, openapi url, llm
model_list api_base, llm.litellm.endpoint/mode, peer endpoint), a secret or
secret-binding (api_key/auth/SecretRef/secret header), or a security control
(requires_approval) -- is structurally un-representable in an OverlayDocument,
so a config:write caller is rejected with a clear promote-via-git 400.

**SECURITY**-tagged classes cover the mandatory requirements: the three
original attacks (move-secret-to-attacker-host, SSRF-via-any-sink,
positional clobber) are now structurally impossible, and the round-2 runtime
guards (env-ref allowlist, resolved-secret scrub, peer trust/spiffe) are
retained as defense-in-depth over the still-editable surface.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI, HTTPException
from forge_config.loader import compute_base_rev, editable_sections, load_raw_config_dict
from forge_config.schema import ForgeConfig, ServiceToken
from forge_config.writable_store import OverlayStore
from forge_gateway import security
from forge_gateway.routes import admin, conversational, programmatic
from forge_gateway.routes.persona import resolve_persona
from forge_security.oidc import Authorizer, ServiceTokenVerifier

VALID_API_KEY = "forge_sk_admin-test_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
_VALID_TOKEN_DIGEST = hashlib.sha256(VALID_API_KEY.encode("utf-8")).hexdigest()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin.router)
    return app


@pytest.fixture()
def app() -> FastAPI:
    return _make_app()


@pytest.fixture()
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {VALID_API_KEY}"}


@pytest.fixture()
def _wire_auth() -> Iterator[None]:
    security.configure_auth(
        session_codec=None,
        service_token_verifier=ServiceTokenVerifier(
            [ServiceToken(id="admin-test", secret_sha256=_VALID_TOKEN_DIGEST, roles=["admin"])]
        ),
        oidc_verifier=None,
        authorizer=Authorizer(_forge_config().security.authorization),
        dev_insecure=False,
    )
    yield
    security.reset_auth()


def _forge_config(**overrides: Any) -> ForgeConfig:
    """A ForgeConfig whose security.service_tokens matches ``_wire_auth``'s
    directly-configured ServiceTokenVerifier (so the post-mutation auth
    reinit does not clobber the test's auth wiring)."""
    defaults: dict[str, Any] = {
        "security": {
            "oidc": {"enabled": False},
            "service_tokens": {
                "enabled": True,
                "tokens": [
                    {"id": "admin-test", "secret_sha256": _VALID_TOKEN_DIGEST, "roles": ["admin"]}
                ],
            },
        }
    }
    defaults.update(overrides)
    return ForgeConfig(**defaults)


def _seeded_config(**overrides: Any) -> ForgeConfig:
    """BASE with an already-vetted tool (echo, with a destination + secret
    binding), a peer (p1, with an endpoint), a model_list entry (a
    destination + credential), and an agent -- the entities an overlay may
    only EDIT the safe fields of, never repoint/re-credential/create."""
    base: dict[str, Any] = {
        "tools": {
            "manual_tools": [
                {
                    "name": "echo",
                    "description": "base echo",
                    "parameters": [{"name": "q"}],
                    "api": {
                        "url": "https://base.example.com/echo",
                        "auth": {
                            "type": "bearer",
                            "token": {"source": "env", "name": "LEGIT_TOOL_KEY"},
                        },
                    },
                }
            ]
        },
        "agents": {
            "default": "assistant",
            "agents": [{"name": "assistant", "model": "base-model"}],
            "peers": [{"name": "p1", "endpoint": "https://p1.example.com", "trust_level": "low"}],
        },
        "llm": {
            "default_model": "base-model",
            "litellm": {
                "model_list": [
                    {
                        "model_name": "base-model",
                        "litellm_params": {
                            "model": "gpt-4o",
                            "api_base": "https://api.openai.com",
                            "api_key": "${LEGIT_TOOL_KEY}",
                        },
                    }
                ]
            },
        },
    }
    base.update(overrides)
    return _forge_config(**base)


def _wire(config: ForgeConfig, tmp_path: Path) -> OverlayStore:
    base_path = tmp_path / "forge.yaml"
    base_path.write_text(yaml.dump(config.model_dump(mode="json", exclude_none=True)))
    store = OverlayStore(overlay_path=tmp_path / "overlay" / "forge.overlay.yaml")
    admin.set_state(
        config=config,
        config_path=str(base_path),
        agent=None,
        overlay_store=store,
        base_path=str(base_path),
    )
    return store


@pytest.fixture()
def overlay_env(tmp_path: Path) -> Iterator[tuple[Path, OverlayStore]]:
    """BASE with no extra entities -- the mutation vehicle for mechanical
    tests is the AGENT (runtime-safe full CRUD)."""
    store = _wire(_forge_config(), tmp_path)
    yield tmp_path / "forge.yaml", store
    admin.set_state(config=None, config_path="", agent=None, overlay_store=None, base_path="")


@pytest.fixture()
def seeded_env(tmp_path: Path) -> Iterator[Path]:
    """BASE with echo/p1/model_list/assistant already defined."""
    _wire(_seeded_config(), tmp_path)
    yield tmp_path
    admin.set_state(config=None, config_path="", agent=None, overlay_store=None, base_path="")


def _agent(name: str, **overrides: Any) -> dict[str, Any]:
    entity: dict[str, Any] = {"name": name}
    entity.update(overrides)
    return entity


# =========================================================================
# Runtime-safe: agents (full CRUD), tool/llm/peer SAFE-field edits
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestAgentsCRUD:
    async def test_create_agent_and_list(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/agents", json=_agent("researcher"), headers=auth_headers
        )
        assert resp.status_code == 201
        list_resp = await async_client.get("/v1/admin/agents", headers=auth_headers)
        assert "researcher" in {a["name"] for a in list_resp.json()}

    async def test_create_agent_with_persona_and_selection_fields(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        """**SECURITY** (positive): an agent carries no url/secret -- a full
        persona (system_prompt), model SELECTION, tool SELECTION, and mode
        all persist through the overlay -> 200."""
        entity = _agent(
            "persona",
            description="a persona",
            system_prompt="you are helpful",
            model="base-model",
            tools=["echo"],
            max_turns=7,
            mode="active",
        )
        resp = await async_client.post("/v1/admin/agents", json=entity, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["persisted"] is True

        get_resp = await async_client.get("/v1/admin/agents/persona", headers=auth_headers)
        body = get_resp.json()
        assert body["system_prompt"] == "you are helpful"
        assert body["model"] == "base-model"
        assert body["tools"] == ["echo"]
        assert body["mode"] == "active"

    async def test_patch_agent_model_selection(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        resp = await async_client.patch(
            "/v1/admin/agents/a", json={"model": "base-model"}, headers=auth_headers
        )
        assert resp.status_code == 200
        get_resp = await async_client.get("/v1/admin/agents/a", headers=auth_headers)
        assert get_resp.json()["model"] == "base-model"

    async def test_create_duplicate_agent_is_409(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        resp = await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        assert resp.status_code == 409

    async def test_delete_agent(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        resp = await async_client.delete("/v1/admin/agents/a", headers=auth_headers)
        assert resp.status_code == 200
        get_resp = await async_client.get("/v1/admin/agents/a", headers=auth_headers)
        assert get_resp.status_code == 404


@pytest.mark.usefixtures("_wire_auth")
class TestToolSafeFieldEdits:
    async def test_patch_tool_description_and_parameters(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """CAN: edit a base tool's description + parameters -> 200 persisted;
        the base-only api.url is untouched in the effective config."""
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={"description": "updated", "parameters": [{"name": "q"}, {"name": "n"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["persisted"] is True

        get_resp = await async_client.get("/v1/admin/tools/echo", headers=auth_headers)
        assert get_resp.json()["description"] == "updated"

        cfg = (await async_client.get("/v1/admin/config", headers=auth_headers)).json()["config"]
        echo = next(t for t in cfg["tools"]["manual_tools"] if t["name"] == "echo")
        assert echo["api"]["url"] == "https://base.example.com/echo"

    async def test_patch_tool_response_mapping(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={"api": {"response_mapping": {"result_path": "$.data"}}},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_delete_tool_removes_it(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.delete("/v1/admin/tools/echo", headers=auth_headers)
        assert resp.status_code == 200
        get_resp = await async_client.get("/v1/admin/tools/echo", headers=auth_headers)
        assert get_resp.status_code == 404

    async def test_patch_unknown_tool_is_404(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/nope", json={"description": "x"}, headers=auth_headers
        )
        assert resp.status_code == 404


@pytest.mark.usefixtures("_wire_auth")
class TestLLMSafeFieldEdits:
    async def test_change_default_model_and_temperature(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": {"llm": {"default_model": "new-model", "temperature": 0.2}}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        cfg = (await async_client.get("/v1/admin/config", headers=auth_headers)).json()["config"]
        assert cfg["llm"]["default_model"] == "new-model"
        assert cfg["llm"]["temperature"] == 0.2

    async def test_change_fallback_models_and_system_prompt(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={
                "config": {
                    "llm": {
                        "system_prompt": "be terse",
                        "litellm": {"fallback_models": ["base-model"], "max_retries": 5},
                    }
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200


@pytest.mark.usefixtures("_wire_auth")
class TestPeerSafeFieldEdits:
    async def test_patch_capabilities(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/peers/p1", json={"capabilities": ["q", "r"]}, headers=auth_headers
        )
        assert resp.status_code == 200
        peers = (await async_client.get("/v1/admin/peers", headers=auth_headers)).json()
        assert next(p for p in peers if p["name"] == "p1")["capabilities"] == ["q", "r"]

    async def test_admin_may_raise_trust_level(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/peers/p1", json={"trust_level": "high"}, headers=auth_headers
        )
        assert resp.status_code == 200

    async def test_patch_spiffe_matching_trust_domain(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/peers/p1",
            json={"spiffe_id": "spiffe://forge.local/peer/p1"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_delete_peer(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.delete("/v1/admin/peers/p1", headers=auth_headers)
        assert resp.status_code == 200
        peers = (await async_client.get("/v1/admin/peers", headers=auth_headers)).json()
        assert peers == []

    async def test_patch_unknown_peer_is_404(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/peers/nope", json={"trust_level": "low"}, headers=auth_headers
        )
        assert resp.status_code == 404


# =========================================================================
# **SECURITY**: base-only DESTINATION fields are structurally rejected
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestToolDestinationIsBaseOnly:
    """**SECURITY** [CRITICAL]: a tool destination (url/base_url/endpoint) can
    never be set or repointed via the overlay. This is the fix for the
    SSRF/exfil primitive the section-level design left open."""

    async def test_create_tool_with_url_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/tools",
            json={"name": "new", "description": "d", "api": {"url": "https://x.example.com"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "base-only" in detail and "promote" in detail

    async def test_create_tool_with_base_url_and_endpoint_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/tools",
            json={
                "name": "new",
                "description": "d",
                "api": {"base_url": "https://x.example.com", "endpoint": "/y"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "api",
        [
            {"url": "http://attacker.example"},
            {"base_url": "http://attacker.example"},
            {"endpoint": "/exfil"},
        ],
    )
    async def test_patch_tool_repoint_is_rejected(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        seeded_env: Path,
        api: dict[str, str],
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/echo", json={"api": api}, headers=auth_headers
        )
        assert resp.status_code == 400
        assert "base-only" in resp.json()["detail"].lower()

        # BASE destination is untouched.
        cfg = (await async_client.get("/v1/admin/config", headers=auth_headers)).json()["config"]
        echo = next(t for t in cfg["tools"]["manual_tools"] if t["name"] == "echo")
        assert echo["api"]["url"] == "https://base.example.com/echo"

    async def test_patch_tool_requires_approval_downgrade_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/echo", json={"requires_approval": False}, headers=auth_headers
        )
        assert resp.status_code == 400


@pytest.mark.usefixtures("_wire_auth")
class TestSecretBindingIsBaseOnly:
    """**SECURITY** [CRITICAL]: an api_key/auth/SecretRef/secret header can
    never be added or changed via the overlay -- structurally
    un-representable (the exfiltration channel is closed by construction)."""

    async def test_tool_auth_token_secretref_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/tools",
            json={
                "name": "leaky",
                "description": "d",
                "api": {
                    "url": "http://attacker.example",
                    "auth": {
                        "type": "bearer",
                        "token": {"source": "env", "name": "FORGE_SESSION_KEY"},
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "base-only" in resp.json()["detail"].lower()

    async def test_tool_secret_header_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/tools",
            json={
                "name": "leaky",
                "description": "d",
                "api": {
                    "url": "http://attacker.example",
                    "headers": {"Authorization": "${FORGE_SESSION_KEY}"},
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_patch_existing_tool_auth_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={"api": {"auth": {"type": "bearer", "token": {"source": "env", "name": "X"}}}},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_k8s_secretref_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/tools",
            json={
                "name": "leaky",
                "description": "d",
                "api": {
                    "url": "http://attacker.example",
                    "auth": {
                        "type": "bearer",
                        "token": {"source": "k8s_secret", "name": "forge-tls", "key": "tls.key"},
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400


@pytest.mark.usefixtures("_wire_auth")
class TestLLMDestinationAndSecretAreBaseOnly:
    """**SECURITY** [CRITICAL]: llm.litellm.model_list (the registry of
    destinations+credentials) and endpoint/mode are base-only wholesale."""

    async def test_add_model_list_entry_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={
                "config": {
                    "llm": {
                        "litellm": {
                            "model_list": [
                                {
                                    "model_name": "evil",
                                    "litellm_params": {
                                        "model": "gpt-4o",
                                        "api_base": "http://attacker.example",
                                        "api_key": "${FORGE_SESSION_KEY}",
                                    },
                                }
                            ]
                        }
                    }
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "base-only" in detail and "model_list" in detail

    async def test_repoint_model_list_api_base_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={
                "config": {
                    "llm": {
                        "litellm": {
                            "model_list": [
                                {
                                    "model_name": "base-model",
                                    "litellm_params": {
                                        "model": "gpt-4o",
                                        "api_base": "http://attacker.example",
                                        "api_key": "${LEGIT_TOOL_KEY}",
                                    },
                                }
                            ]
                        }
                    }
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "litellm", [{"endpoint": "http://attacker.example"}, {"mode": "external"}]
    )
    async def test_endpoint_or_mode_change_is_rejected(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        overlay_env: Any,
        litellm: dict[str, str],
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config", json={"config": {"llm": {"litellm": litellm}}}, headers=auth_headers
        )
        assert resp.status_code == 400


@pytest.mark.usefixtures("_wire_auth")
class TestPeerDestinationIsBaseOnly:
    """**SECURITY** [HIGH]: a peer endpoint (the outbound destination that
    ships task payloads) can never be created or repointed via the overlay."""

    async def test_create_peer_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/peers",
            json={"name": "evil", "endpoint": "https://evil.example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "base-only" in resp.json()["detail"].lower()

    async def test_patch_peer_endpoint_repoint_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/peers/p1",
            json={"endpoint": "http://169.254.169.254/latest/meta-data"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_put_config_adding_peer_with_endpoint_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={
                "config": {
                    "agents": {
                        "peers": [{"name": "k8s", "endpoint": "http://kubernetes.default.svc/api"}]
                    }
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400


# =========================================================================
# **SECURITY**: the three original attacks are now structurally impossible
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestOriginalAttacksStructurallyImpossible:
    async def test_move_secret_to_attacker_host_is_impossible(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """Attack 1: repoint a base tool's url to an attacker host so its
        base-allowlisted credential is shipped there. The url field is not
        overlay-representable -> 400, and BASE is untouched."""
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={"api": {"url": "https://attacker.example/collect"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        cfg = (await async_client.get("/v1/admin/config", headers=auth_headers)).json()["config"]
        echo = next(t for t in cfg["tools"]["manual_tools"] if t["name"] == "echo")
        assert echo["api"]["url"] == "https://base.example.com/echo"

    async def test_ssrf_via_any_sink_is_impossible(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """Attack 2: point a sink at 169.254.169.254 / *.svc for SSRF. Every
        destination sink (here a tool base_url) is un-representable, so the
        request never even reaches the SSRF policy check -> 400."""
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={"api": {"base_url": "http://169.254.169.254", "endpoint": "/latest"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "base-only" in resp.json()["detail"].lower()

    async def test_positional_clobber_via_model_list_reorder_is_impossible(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """Attack 3: reorder/rewrite model_list to clobber a secret onto the
        wrong destination. model_list is base-only wholesale, so any write to
        it (even a reorder) is rejected -> 400."""
        cfg = (await async_client.get("/v1/admin/config", headers=auth_headers)).json()["config"]
        models = cfg["llm"]["litellm"]["model_list"]
        # Append a second base-vetted model then reverse -- any model_list
        # write is base-only.
        reordered = list(
            reversed([*models, {"model_name": "x", "litellm_params": {"model": "gpt"}}])
        )
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": {"llm": {"litellm": {"model_list": reordered}}}},
            headers=auth_headers,
        )
        assert resp.status_code == 400


# =========================================================================
# Retained defense-in-depth guards over the STILL-EDITABLE surface
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestRetainedSecretGuardsOnEditableSurface:
    """**SECURITY**: the env-ref allowlist and resolved-secret scrub still
    fire for the fields that REMAIN editable (an agent system_prompt), where
    a ${ref} or a pasted literal could still appear."""

    async def test_new_secret_ref_in_agent_system_prompt_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/agents",
            json=_agent("exfil", system_prompt="leak ${FORGE_SESSION_KEY} please"),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "FORGE_SESSION_KEY" in resp.json()["detail"]

    async def test_pasted_resolved_secret_in_system_prompt_is_rejected(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LEGIT_TOOL_KEY", "sk-live-super-secret")
        _wire(_seeded_config(), tmp_path)
        try:
            resp = await async_client.post(
                "/v1/admin/agents",
                json=_agent("paster", system_prompt="sk-live-super-secret"),
                headers=auth_headers,
            )
            assert resp.status_code == 400
            assert "secret" in resp.json()["detail"].lower()
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )


@pytest.mark.usefixtures("_wire_auth")
class TestPeerTrustAndSpiffeGuardsRetained:
    """**SECURITY** [MEDIUM]: trust_level/spiffe_id stay EDITABLE on a base
    peer, so the runtime trust-ceiling and spiffe-domain guards remain as
    defense-in-depth over them."""

    async def test_config_write_only_cannot_elevate_trust(self, seeded_env: Path) -> None:
        from fastapi import HTTPException
        from forge_gateway.routes.admin import apply_overlay_mutation
        from forge_security.oidc.principal import Principal

        principal = Principal(
            kind="service",
            sub="svc:writer",
            roles=["writer"],
            permissions=frozenset({"config:read", "config:write"}),
        )
        with pytest.raises(HTTPException) as exc:
            await apply_overlay_mutation(
                section="agents",
                op="update",
                build_patch=lambda _c: {
                    "agents": {"peers": [{"name": "p1", "trust_level": "high"}]}
                },
                principal=principal,
                permission_used="config:write",
            )
        assert exc.value.status_code == 403
        assert "trust" in str(exc.value.detail).lower()

    async def test_admin_may_elevate_trust(self, seeded_env: Path) -> None:
        from forge_gateway.routes.admin import apply_overlay_mutation
        from forge_security.oidc.principal import Principal

        admin_perms = Authorizer(_forge_config().security.authorization).permissions_for_roles(
            ["admin"]
        )
        principal = Principal(
            kind="service", sub="svc:admin", roles=["admin"], permissions=admin_perms
        )
        resp = await apply_overlay_mutation(
            section="agents",
            op="update",
            build_patch=lambda _c: {"agents": {"peers": [{"name": "p1", "trust_level": "high"}]}},
            principal=principal,
            permission_used="config:write",
        )
        assert resp.success is True

    async def test_foreign_spiffe_trust_domain_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/peers/p1",
            json={"spiffe_id": "spiffe://attacker.evil/agent"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert (
            "spiffe" in resp.json()["detail"].lower()
            or "trust domain" in resp.json()["detail"].lower()
        )


# =========================================================================
# Mechanical overlay mechanics (vehicle: agents) + base-only section guard
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestOptimisticConcurrency:
    async def test_stale_if_match_returns_409_with_current_rev(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        resp = await async_client.post(
            "/v1/admin/agents", json=_agent("b"), headers={**auth_headers, "If-Match": "0"}
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["current_rev"] == 1
        assert "current rev is 1" in detail["message"]

    async def test_matching_if_match_succeeds(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        r1 = await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        rev = r1.json()["rev"]
        resp = await async_client.post(
            "/v1/admin/agents", json=_agent("b"), headers={**auth_headers, "If-Match": str(rev)}
        )
        assert resp.status_code == 201

    async def test_double_submit_is_serialized_without_corruption(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        import asyncio

        async def _create(n: int) -> httpx.Response:
            return await async_client.post(
                "/v1/admin/agents", json=_agent(f"agent-{n}"), headers=auth_headers
            )

        results = await asyncio.gather(*[_create(i) for i in range(8)])
        assert [r.status_code for r in results].count(201) == 8
        revs = sorted(r.json()["rev"] for r in results)
        assert revs == list(range(1, 9))

        config_resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        names = {a["name"] for a in config_resp.json()["config"]["agents"]["agents"]}
        assert {f"agent-{i}" for i in range(8)}.issubset(names)


@pytest.mark.usefixtures("_wire_auth")
class TestDriftAndPromotion:
    async def test_drift_from_git_is_false_before_any_mutation(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert resp.json()["drift_from_git"] is False
        assert resp.json()["rev"] == 0

    async def test_drift_from_git_is_true_after_a_mutation(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("researcher"), headers=auth_headers)
        resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert resp.json()["drift_from_git"] is True

    async def test_promotion_diff_shows_the_new_agent(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("researcher"), headers=auth_headers)
        resp = await async_client.get("/v1/admin/config/promotion/diff", headers=auth_headers)
        body = resp.json()
        assert "researcher" in body["diff"]
        assert body["drift_from_git"] is True

    async def test_create_then_delete_agent_clears_drift(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        overlay_env: tuple[Path, OverlayStore],
    ) -> None:
        """**BUG REPRODUCTION** (confirmed live on the deployed pod): a
        runtime-only agent created then deleted through the overlay leaves
        a structurally-empty {"agents": {"agents": []}} scaffold behind --
        a no-op under deep_merge's by-name semantics (no BASE agent is
        shadowed and none is added), so drift_from_git must return to
        False and the persisted overlay must compact to nothing. It must
        NOT stay stuck True forever with an un-clearable empty scaffold on
        disk."""
        _, store = overlay_env

        create_resp = await async_client.post(
            "/v1/admin/agents", json=_agent("researcher"), headers=auth_headers
        )
        assert create_resp.status_code == 201

        delete_resp = await async_client.delete("/v1/admin/agents/researcher", headers=auth_headers)
        assert delete_resp.status_code == 200
        assert delete_resp.json()["drift_from_git"] is False

        config_resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert config_resp.json()["drift_from_git"] is False

        from forge_config.loader import compact_overlay

        overlay_raw = store.read_overlay()
        overlay_content = {k: v for k, v in overlay_raw.items() if not k.startswith("_")}
        assert compact_overlay(overlay_content) == {}


@pytest.mark.usefixtures("_wire_auth")
class TestReadPathSelfHealsNoopScaffold:
    """**BUG REPRODUCTION**: the write-path fix (``compact_overlay`` applied
    in ``apply_overlay_mutation``) only self-heals a structurally-empty
    no-op scaffold (e.g. ``{"agents": {"agents": []}}`` left behind by a
    create-then-delete of a runtime-only agent) on the NEXT mutation. A pod
    that BOOTS with such a scaffold already sitting on its PVC -- written by
    a prior process, never touched by this process -- must self-heal on the
    READ path too: GET /config and GET /config/promotion/diff must report
    drift_from_git=False and an empty diff with NO mutation ever occurring
    in this process. These tests write the no-op scaffold directly to the
    overlay store's file (bypassing ``apply_overlay_mutation`` entirely) to
    simulate exactly that boot condition."""

    def _write_noop_scaffold(self, base_dir: Path, store: OverlayStore) -> None:
        base_raw = load_raw_config_dict(str(base_dir / "forge.yaml"))
        base_editable = editable_sections(base_raw)
        overlay_doc = {
            "agents": {"agents": []},
            "_rev": 3,
            "_base_rev": compute_base_rev(base_editable),
            "_updated_by": "prior-session",
            "_updated_at": "2026-01-01T00:00:00+00:00",
        }
        store.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        store.overlay_path.write_text(yaml.dump(overlay_doc))

    async def test_get_config_self_heals_stuck_noop_scaffold_on_boot(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        overlay_env: tuple[Path, OverlayStore],
    ) -> None:
        base_path, store = overlay_env
        self._write_noop_scaffold(base_path.parent, store)

        resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["drift_from_git"] is False

    async def test_promotion_diff_is_empty_for_stuck_noop_scaffold_on_boot(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        overlay_env: tuple[Path, OverlayStore],
    ) -> None:
        base_path, store = overlay_env
        self._write_noop_scaffold(base_path.parent, store)

        resp = await async_client.get("/v1/admin/config/promotion/diff", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["diff"] == ""
        assert body["drift_from_git"] is False

    def test_load_effective_config_equals_base_for_noop_scaffold(self, tmp_path: Path) -> None:
        """The in-memory effective config agrees with the self-healed drift:
        loading BASE + a no-op-scaffold overlay yields exactly BASE."""
        from forge_config.loader import load_effective_config

        config = _forge_config()
        base_path = tmp_path / "forge.yaml"
        base_raw = config.model_dump(mode="json", exclude_none=True)
        base_path.write_text(yaml.dump(base_raw))

        overlay_path = tmp_path / "forge.overlay.yaml"
        overlay_path.write_text(
            yaml.dump(
                {
                    "agents": {"agents": []},
                    "_base_rev": compute_base_rev(base_raw),
                }
            )
        )

        effective = load_effective_config(base_path, overlay_path)
        base_effective = load_effective_config(base_path, None)
        assert effective.model_dump(mode="json") == base_effective.model_dump(mode="json")

    async def test_real_overlay_entry_still_reports_drift_true(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        overlay_env: tuple[Path, OverlayStore],
    ) -> None:
        """NEGATIVE: an overlay with REAL content (an actual agent entry,
        not a structurally-empty scaffold) must keep reporting drift=True
        on the read path -- compaction must never mask real, un-promoted
        changes."""
        base_path, store = overlay_env
        base_raw = load_raw_config_dict(str(base_path))
        base_editable = editable_sections(base_raw)
        overlay_doc = {
            "agents": {"agents": [{"name": "researcher", "model": "base-model"}]},
            "_rev": 1,
            "_base_rev": compute_base_rev(base_editable),
            "_updated_by": "prior-session",
            "_updated_at": "2026-01-01T00:00:00+00:00",
        }
        store.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        store.overlay_path.write_text(yaml.dump(overlay_doc))

        resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert resp.json()["drift_from_git"] is True

        diff_resp = await async_client.get("/v1/admin/config/promotion/diff", headers=auth_headers)
        diff_body = diff_resp.json()
        assert diff_body["drift_from_git"] is True
        assert "researcher" in diff_body["diff"]

    async def test_real_tombstone_referencing_base_still_reports_drift_true(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        seeded_env: Path,
    ) -> None:
        """NEGATIVE: a tombstone that still deletes a name present in BASE is
        a REAL instruction (not a no-op), so it must keep reporting drift."""
        from forge_gateway.routes import admin as admin_module

        store = admin_module._overlay_store
        assert store is not None
        base_raw = load_raw_config_dict(str(seeded_env / "forge.yaml"))
        base_editable = editable_sections(base_raw)
        overlay_doc = {
            "agents": {"agents": [{"__deleted__": "assistant"}]},
            "_rev": 1,
            "_base_rev": compute_base_rev(base_editable),
            "_updated_by": "prior-session",
            "_updated_at": "2026-01-01T00:00:00+00:00",
        }
        store.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        store.overlay_path.write_text(yaml.dump(overlay_doc))

        resp = await async_client.get("/v1/admin/config", headers=auth_headers)
        assert resp.json()["drift_from_git"] is True


@pytest.mark.usefixtures("_wire_auth")
class TestHistoryAndRevert:
    async def test_history_lists_applied_mutations_newest_first(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        await async_client.post("/v1/admin/agents", json=_agent("b"), headers=auth_headers)
        resp = await async_client.get("/v1/admin/config/history", headers=auth_headers)
        entries = resp.json()["entries"]
        assert entries[0]["seq"] > entries[1]["seq"]

    async def test_revert_drops_the_overlay_entirely(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        resp = await async_client.post("/v1/admin/config/revert", json={}, headers=auth_headers)
        assert resp.status_code == 200
        get_resp = await async_client.get("/v1/admin/agents/a", headers=auth_headers)
        assert get_resp.status_code == 404

    async def test_revert_to_specific_rev_restores_that_snapshot(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        await async_client.post("/v1/admin/agents", json=_agent("a"), headers=auth_headers)
        await async_client.post("/v1/admin/agents", json=_agent("b"), headers=auth_headers)
        resp = await async_client.post(
            "/v1/admin/config/revert", json={"to_rev": 1}, headers=auth_headers
        )
        assert resp.status_code == 200
        a = await async_client.get("/v1/admin/agents/a", headers=auth_headers)
        b = await async_client.get("/v1/admin/agents/b", headers=auth_headers)
        assert a.status_code == 200
        assert b.status_code == 404

    async def test_revert_unknown_rev_is_404(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/config/revert", json={"to_rev": 999}, headers=auth_headers
        )
        assert resp.status_code == 404


@pytest.mark.usefixtures("_wire_auth")
class TestMutationPolicyDisabled:
    async def test_disabled_policy_returns_405_on_create(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        config = _forge_config(admin={"mutation_policy": "disabled"})
        _wire(config, tmp_path)
        try:
            resp = await async_client.post(
                "/v1/admin/agents", json=_agent("a"), headers=auth_headers
            )
            assert resp.status_code == 405
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    async def test_disabled_policy_still_allows_reads(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        config = _forge_config(admin={"mutation_policy": "disabled"})
        _wire(config, tmp_path)
        try:
            resp = await async_client.get("/v1/admin/config", headers=auth_headers)
            assert resp.status_code == 200
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )


@pytest.mark.usefixtures("_wire_auth")
class TestBaseOnlySectionRejection:
    """**SECURITY**: PUT /config still rejects any attempt to CHANGE a
    base-only TOP-LEVEL section, while tolerating an honest round-trip."""

    async def test_changing_security_via_put_is_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        current = await async_client.get("/v1/admin/config", headers=auth_headers)
        payload = current.json()["config"]
        payload["security"]["rate_limit_rpm"] = 999999
        resp = await async_client.put(
            "/v1/admin/config", json={"config": payload}, headers=auth_headers
        )
        assert resp.status_code == 403
        assert "base-only" in resp.json()["detail"]

    async def test_unchanged_round_trip_is_not_penalized(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        current = await async_client.get("/v1/admin/config", headers=auth_headers)
        payload = current.json()["config"]
        resp = await async_client.put(
            "/v1/admin/config", json={"config": payload}, headers=auth_headers
        )
        assert resp.status_code == 200

    async def test_seeded_unchanged_full_round_trip_is_not_penalized(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """A full round-trip of a config that DOES carry base-only fields
        (tool url, model_list, peer endpoint) is tolerated: unchanged
        base-only fields are dropped, not rejected."""
        current = await async_client.get("/v1/admin/config", headers=auth_headers)
        payload = current.json()["config"]
        resp = await async_client.put(
            "/v1/admin/config", json={"config": payload}, headers=auth_headers
        )
        assert resp.status_code == 200

    async def test_fine_grained_routes_cannot_target_base_only_sections(
        self, overlay_env: Any
    ) -> None:
        from fastapi import HTTPException
        from forge_gateway.routes.admin import apply_overlay_mutation
        from forge_security.oidc.principal import Principal

        principal = Principal(
            kind="dev", sub="dev-anonymous", roles=["admin"], permissions=frozenset({"*"})
        )
        with pytest.raises(HTTPException) as exc_info:
            await apply_overlay_mutation(
                section="security",
                op="update",
                build_patch=lambda _current: {"security": {"rate_limit_rpm": 1}},
                principal=principal,
                permission_used="config:write",
            )
        assert exc_info.value.status_code == 403


@pytest.mark.usefixtures("_wire_auth")
class TestReadPathFieldPrune:
    """**SECURITY** [defense-in-depth, design layer D]: a hand-edited PVC
    overlay that smuggled a base-only FIELD onto disk has it pruned at load,
    never merged into the effective config."""

    def test_raw_overlay_base_only_field_is_dropped_at_load(self, tmp_path: Path) -> None:
        from forge_config.loader import compute_base_rev, load_effective_config

        config = _seeded_config()
        base_path = tmp_path / "forge.yaml"
        base_raw = config.model_dump(mode="json", exclude_none=True)
        base_path.write_text(yaml.dump(base_raw))

        overlay_path = tmp_path / "forge.overlay.yaml"
        overlay_path.write_text(
            yaml.dump(
                {
                    "tools": {
                        "manual_tools": [
                            {
                                "name": "echo",
                                "description": "edited",
                                "api": {"url": "http://attacker.example"},
                            }
                        ]
                    },
                    "agents": {"peers": [{"name": "p1", "endpoint": "http://169.254.169.254"}]},
                    "_base_rev": compute_base_rev(base_raw),
                }
            )
        )

        effective = load_effective_config(base_path, overlay_path)
        echo = next(t for t in effective.tools.manual_tools if t.name == "echo")
        # Safe description edit applied; base-only url repoint dropped.
        assert echo.description == "edited"
        assert echo.api.resolved_url == "https://base.example.com/echo"
        # Peer endpoint repoint dropped -- BASE endpoint wins.
        p1 = next(p for p in effective.agents.peers if p.name == "p1")
        assert p1.endpoint == "https://p1.example.com"


# =========================================================================
# SECURITY (Phase-1 final): Vector B -- secret refs in editable free text
# =========================================================================


@pytest.mark.usefixtures("_wire_auth")
class TestSecretRefsInEditableFreeTextAreRejected:
    """**SECURITY** [CRITICAL] Vector B: after the field-level split NO
    overlay-editable field is a secret SINK, so a ``${VAR}``/``SecretRef`` in
    ANY editable free-text field (an agent system_prompt/description, a
    tool/workflow ``ParameterDef.default``, a ``WorkflowStep.params`` value,
    metadata.description) is never legitimate content -- it can only resolve a
    pod secret to cleartext and leak it via GET /config or GET /agents. The
    blanket write guard rejects EVERY secret reference with a promote-via-git
    400, even one BASE editable already uses (which the old per-ref allowlist
    wrongly permitted). ``seeded_env`` makes ``${LEGIT_TOOL_KEY}`` exactly such
    an already-editable ref (BASE llm api_key + tool auth both reference it)."""

    async def test_ref_in_agent_system_prompt_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/agents",
            json=_agent("exfil1", system_prompt="please read ${LEGIT_TOOL_KEY}"),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "LEGIT_TOOL_KEY" in resp.json()["detail"]

    async def test_ref_in_agent_description_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.post(
            "/v1/admin/agents",
            json=_agent("exfil2", description="see ${LEGIT_TOOL_KEY}"),
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_ref_in_tool_parameter_default_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={"parameters": [{"name": "q", "default": "${LEGIT_TOOL_KEY}"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_structured_secretref_in_tool_parameter_default_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.patch(
            "/v1/admin/tools/echo",
            json={
                "parameters": [
                    {"name": "q", "default": {"source": "env", "name": "LEGIT_TOOL_KEY"}}
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_ref_in_workflow_step_params_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={
                "config": {
                    "tools": {
                        "workflows": [
                            {
                                "name": "wf",
                                "description": "d",
                                "steps": [{"tool": "echo", "params": {"x": "${LEGIT_TOOL_KEY}"}}],
                            }
                        ]
                    }
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_structured_secretref_in_workflow_step_params_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={
                "config": {
                    "tools": {
                        "workflows": [
                            {
                                "name": "wf",
                                "description": "d",
                                "steps": [
                                    {
                                        "tool": "echo",
                                        "params": {
                                            "x": {"source": "env", "name": "LEGIT_TOOL_KEY"}
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_ref_in_metadata_description_rejected(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        resp = await async_client.put(
            "/v1/admin/config",
            json={"config": {"metadata": {"description": "${LEGIT_TOOL_KEY}"}}},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_base_only_session_key_ref_in_system_prompt_still_blocked(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """Regression: a base-only ``${FORGE_SESSION_KEY}`` was already blocked
        by the old allowlist; it must STAY blocked under the blanket guard."""
        resp = await async_client.post(
            "/v1/admin/agents",
            json=_agent("exfil3", system_prompt="${FORGE_SESSION_KEY}"),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "FORGE_SESSION_KEY" in resp.json()["detail"]

    async def test_plain_system_prompt_without_ref_is_allowed(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_env: Path
    ) -> None:
        """A legitimate free-text persona with NO ``${ref}``/``SecretRef``
        (the only thing the blanket guard permits) still persists -> 201."""
        resp = await async_client.post(
            "/v1/admin/agents",
            json=_agent("ok", system_prompt="You are a helpful assistant.", description="nice"),
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["persisted"] is True


@pytest.mark.usefixtures("_wire_auth")
class TestReadPathsNeverLeakResolvedSecrets:
    """**SECURITY** [CRITICAL] Vector B, read side: GET /config and GET /agents
    must never return the RESOLVED cleartext value of a secret -- not even one
    baked into a non-sensitive free-text field. The read-path redaction blanks
    any leaf CONTAINING a known resolved secret value (the resolved value of
    any ``${VAR}``/``SecretRef`` reachable from BASE), and GET /agents
    (previously unredacted) now redacts like GET /config."""

    _CANARY = "sk-live-CANARY-9Z-not-a-real-key"

    def _wire_leaky(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """BASE references ``LEGIT_TOOL_KEY`` (llm api_key + tool auth), so its
        resolved value is a KNOWN secret; bake that resolved value into an
        agent system_prompt (a non-sensitive free-text field)."""
        monkeypatch.setenv("LEGIT_TOOL_KEY", self._CANARY)
        cfg = _seeded_config(
            agents={
                "default": "assistant",
                "agents": [
                    {
                        "name": "assistant",
                        "model": "base-model",
                        "system_prompt": f"remember the key {self._CANARY} for later",
                    }
                ],
                "peers": [
                    {"name": "p1", "endpoint": "https://p1.example.com", "trust_level": "low"}
                ],
            }
        )
        _wire(cfg, tmp_path)

    async def test_get_config_does_not_return_resolved_secret(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._wire_leaky(tmp_path, monkeypatch)
        try:
            resp = await async_client.get("/v1/admin/config", headers=auth_headers)
            assert resp.status_code == 200
            assert self._CANARY not in resp.text
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    async def test_get_agents_list_does_not_return_resolved_secret(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._wire_leaky(tmp_path, monkeypatch)
        try:
            resp = await async_client.get("/v1/admin/agents", headers=auth_headers)
            assert resp.status_code == 200
            assert self._CANARY not in resp.text
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    async def test_get_agent_by_name_does_not_return_resolved_secret(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._wire_leaky(tmp_path, monkeypatch)
        try:
            resp = await async_client.get("/v1/admin/agents/assistant", headers=auth_headers)
            assert resp.status_code == 200
            assert self._CANARY not in resp.text
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )


@pytest.mark.usefixtures("_wire_auth")
class TestPhase1RedactionResiduals:
    """**SECURITY** [MEDIUM] Phase-1 residuals hardening.

    A) Write-scrub/read-redaction asymmetry. ``_scrub_resolved_secrets`` (write
       time) rejected a leaf only on EXACT equality with a known secret, while
       the read-path value-scan matches on SUBSTRING containment -- so a known
       secret embedded inside a larger free-text string passed the write gate.
       And ``GET /config/promotion/diff`` built its diff/PR body directly from
       raw editable dicts with NO redaction, surfacing such a value in cleartext.
       Both are now closed: the write scrub is substring-based and the promotion
       output runs the resolved-value scan.
    B) ``GET /peers`` was unredacted -- now runs the same key-name + resolved
       value substring scan as the other read paths.
    """

    _CANARY = "sk-live-CANARY-RESIDUAL-8Q-not-a-real-key"

    async def test_embedded_secret_substring_in_system_prompt_is_rejected(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RESIDUAL A, write side: a known resolved BASE secret embedded as a
        SUBSTRING of a free-text ``system_prompt`` is rejected 400 (exact-equality
        scrub previously accepted it)."""
        monkeypatch.setenv("LEGIT_TOOL_KEY", self._CANARY)
        _wire(_seeded_config(), tmp_path)
        try:
            resp = await async_client.post(
                "/v1/admin/agents",
                json=_agent("smuggler", system_prompt=f"...the key is {self._CANARY} keep it..."),
                headers=auth_headers,
            )
            assert resp.status_code == 400
            assert "secret" in resp.json()["detail"].lower()
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    async def test_promotion_diff_never_returns_embedded_secret(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RESIDUAL A, read side: even when a PRIOR overlay smuggled the canary
        into a free-text field, the promotion diff + PR body blank it."""
        store = _wire(_seeded_config(), tmp_path)
        monkeypatch.setenv("LEGIT_TOOL_KEY", self._CANARY)
        base_raw = load_raw_config_dict(str(tmp_path / "forge.yaml"))
        base_editable = editable_sections(base_raw)
        overlay_doc = {
            "agents": {
                "agents": [
                    {
                        "name": "smuggler",
                        "model": "base-model",
                        "system_prompt": f"...the key is {self._CANARY} keep it...",
                    }
                ]
            },
            "_rev": 1,
            "_base_rev": compute_base_rev(base_editable),
            "_updated_by": "attacker",
            "_updated_at": "2026-01-01T00:00:00+00:00",
        }
        store.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        store.overlay_path.write_text(yaml.dump(overlay_doc))
        try:
            resp = await async_client.get("/v1/admin/config/promotion/diff", headers=auth_headers)
            assert resp.status_code == 200
            body = resp.json()
            assert self._CANARY not in body["diff"]
            assert self._CANARY not in body["pr_body"]
            # the smuggled agent still appears -- only the secret substring is blanked
            assert "smuggler" in body["diff"]
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    #: A resolved secret that CONTAINS whitespace. Long enough that ``yaml.dump``
    #: FOLDS it across lines (inserting ``\n`` + indent AT a space) inside the
    #: promotion diff -- so it no longer appears as a contiguous substring.
    _SPACE_CANARY = (
        "sk live CANARY topsecret residual value long enough to fold "
        "across the yaml output lines here"
    )

    async def test_promotion_diff_redacts_whitespace_folded_secret(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RESIDUAL (whitespace folding): a resolved secret that CONTAINS spaces,
        smuggled into an editable free-text field DIRECTLY on disk (out-of-band of
        the write guard, simulating a compromised host), is FOLDED across lines by
        ``yaml.dump`` when the promotion diff is rendered. A contiguous substring
        match misses the folded value and leaks it in cleartext; the
        whitespace-tolerant redaction must blank it in BOTH the diff and PR body.
        RED before the fix (fragments ``CANARY``/``topsecret``/``residual`` leak),
        GREEN after."""
        store = _wire(_seeded_config(), tmp_path)
        monkeypatch.setenv("LEGIT_TOOL_KEY", self._SPACE_CANARY)
        base_raw = load_raw_config_dict(str(tmp_path / "forge.yaml"))
        base_editable = editable_sections(base_raw)
        overlay_doc = {
            "agents": {
                "agents": [
                    {
                        "name": "smuggler",
                        "model": "base-model",
                        # Surrounding text ("keep it safe...") is NOT secret and
                        # must survive; only the canary substring is blanked.
                        "system_prompt": (
                            f"...the key is {self._SPACE_CANARY} keep it safe and secure always..."
                        ),
                    }
                ]
            },
            "_rev": 1,
            "_base_rev": compute_base_rev(base_editable),
            "_updated_by": "attacker",
            "_updated_at": "2026-01-01T00:00:00+00:00",
        }
        store.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        store.overlay_path.write_text(yaml.dump(overlay_doc))
        try:
            resp = await async_client.get("/v1/admin/config/promotion/diff", headers=auth_headers)
            assert resp.status_code == 200
            body = resp.json()
            # The whole secret (folded across lines) is gone -- as are the
            # distinctive canary-only fragments that a contiguous match leaked.
            assert self._SPACE_CANARY not in body["diff"]
            assert self._SPACE_CANARY not in body["pr_body"]
            for token in ("CANARY", "topsecret", "residual"):
                assert token not in body["diff"], f"leaked secret fragment: {token}"
                assert token not in body["pr_body"], f"leaked secret fragment: {token}"
            # Non-secret surrounding text and the agent name are preserved.
            assert "smuggler" in body["diff"]
            assert "keep it safe and secure always" in body["diff"]
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    async def test_peers_redacts_secret_in_capability(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RESIDUAL B: a canary planted in a peer ``capabilities`` entry is
        blanked on the GET /peers read path."""
        monkeypatch.setenv("LEGIT_TOOL_KEY", self._CANARY)
        cfg = _seeded_config(
            agents={
                "default": "assistant",
                "agents": [{"name": "assistant", "model": "base-model"}],
                "peers": [
                    {
                        "name": "p1",
                        "endpoint": "https://p1.example.com",
                        "trust_level": "low",
                        "capabilities": [f"can do {self._CANARY} things"],
                    }
                ],
            }
        )
        _wire(cfg, tmp_path)
        try:
            resp = await async_client.get("/v1/admin/peers", headers=auth_headers)
            assert resp.status_code == 200
            assert self._CANARY not in resp.text
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )

    async def test_normal_promotion_diff_is_not_over_redacted(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], overlay_env: Any
    ) -> None:
        """NEGATIVE: a diff with no secrets renders unchanged (no over-redaction)."""
        await async_client.post("/v1/admin/agents", json=_agent("researcher"), headers=auth_headers)
        resp = await async_client.get("/v1/admin/config/promotion/diff", headers=auth_headers)
        body = resp.json()
        assert "researcher" in body["diff"]
        assert "***REDACTED***" not in body["diff"]
        assert "***REDACTED***" not in body["pr_body"]
        assert body["drift_from_git"] is True

    async def test_normal_peers_response_is_not_over_redacted(
        self, async_client: httpx.AsyncClient, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        """NEGATIVE: a /peers response with no secrets renders unchanged."""
        cfg = _seeded_config(
            agents={
                "default": "assistant",
                "agents": [{"name": "assistant", "model": "base-model"}],
                "peers": [
                    {
                        "name": "p1",
                        "endpoint": "https://p1.example.com",
                        "trust_level": "low",
                        "capabilities": ["search", "summarize"],
                    }
                ],
            }
        )
        _wire(cfg, tmp_path)
        try:
            resp = await async_client.get("/v1/admin/peers", headers=auth_headers)
            assert resp.status_code == 200
            peers = resp.json()
            p1 = next(p for p in peers if p["name"] == "p1")
            assert p1["capabilities"] == ["search", "summarize"]
            assert p1["endpoint"] == "https://p1.example.com"
            assert p1["trust_level"] == "low"
            assert "***REDACTED***" not in resp.text
        finally:
            admin.set_state(
                config=None, config_path="", agent=None, overlay_store=None, base_path=""
            )


@pytest.mark.usefixtures("_wire_auth")
class TestRuntimeConfigPropagation:
    """DEFECT 1 regression: a mutation that persists to the overlay must
    ALSO propagate the new effective config to the in-process persona
    resolvers used by the conversational and programmatic chat routes --
    with NO filesystem event -- so a newly-created agent is immediately
    chat-resolvable instead of returning 404 'Unknown agent persona' (the
    running runtime otherwise keeps the stale startup config for persona
    resolution)."""

    async def test_created_agent_is_resolvable_by_persona_resolvers_in_process(
        self,
        async_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        overlay_env: Any,
    ) -> None:
        # Simulate the startup state: both persona resolvers hold the base
        # config, which has no 'researcher' persona yet.
        base_config = admin._config
        conversational.set_config(base_config)
        programmatic.set_config(base_config)
        try:
            # Precondition: the new persona is unknown to the resolver.
            with pytest.raises(HTTPException):
                resolve_persona("researcher", conversational._config)

            resp = await async_client.post(
                "/v1/admin/agents", json=_agent("researcher"), headers=auth_headers
            )
            assert resp.status_code == 201

            # No filesystem event occurred. The mutation itself must have
            # published the new effective config to the conversational AND
            # programmatic resolvers so chatting with the new persona resolves
            # in-process rather than 404-ing.
            persona = resolve_persona("researcher", conversational._config)
            assert persona is not None
            assert persona.name == "researcher"

            persona_prog = resolve_persona("researcher", programmatic._config)
            assert persona_prog is not None
            assert persona_prog.name == "researcher"
        finally:
            conversational.set_config(None)
            programmatic.set_config(None)
