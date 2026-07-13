"""Tests for wiring the ADR-0002 user-token store into the auth subsystem.

Covers two layers:

* ``forge_gateway.app._init_auth`` -- the store is only constructed when
  ``security.service_tokens.user_tokens.enabled`` is true; it is loaded
  once and handed to ``ServiceTokenVerifier`` as a *live* reference (so a
  mint/revoke through ``routes/tokens.py`` needs no reload); a corrupt
  store degrades only the dynamic-token feature, never static tokens or
  OIDC.
* ``forge_gateway.security.configure_auth`` -- the "healthy" readiness
  gate is widened so a machine-only deployment with only a live
  user-token store (no static tokens, no OIDC, no dev_insecure) still
  reports ready. Exercised directly against ``configure_auth`` (the same
  pattern ``test_auth_enforcement.py`` uses) because
  ``forge_config.schema.SecurityConfig`` itself refuses to construct an
  "nothing to enforce with" config -- that pre-existing, unmodified
  validator has no notion of ``user_tokens.enabled`` as a mechanism, so
  the scenario cannot be expressed as a loadable ``ForgeConfig`` and must
  be exercised at the ``configure_auth`` layer instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from forge_config.schema import (
    AuthorizationConfig,
    ForgeConfig,
    OIDCConfig,
    SecurityConfig,
    ServiceToken,
    ServiceTokenConfig,
    UserTokenConfig,
)
from forge_gateway import security
from forge_gateway.app import _init_auth
from forge_security.oidc import Authorizer, ServiceTokenVerifier
from forge_security.oidc.user_tokens import FileUserTokenStore


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    security.reset_auth()
    yield
    security.reset_auth()


def _disabled_oidc_config() -> OIDCConfig:
    # Keep _init_auth from attempting any network I/O in these tests.
    return OIDCConfig(enabled=False)


def _one_static_admin_token() -> ServiceToken:
    # SecurityConfig requires *some* enforcement mechanism to construct at
    # all (a pre-existing, unrelated validator) -- these tests care about
    # user-token wiring, not that guard, so a harmless static token
    # satisfies it without participating in any assertion below.
    return ServiceToken(id="ci", secret_sha256="a" * 64, roles=["admin"])


class TestStoreConstructionGatedOnEnabled:
    async def test_store_is_none_when_user_tokens_disabled(self, tmp_path: Path) -> None:
        config = ForgeConfig(
            security=SecurityConfig(
                oidc=_disabled_oidc_config(),
                service_tokens=ServiceTokenConfig(
                    tokens=[_one_static_admin_token()],
                    user_tokens=UserTokenConfig(enabled=False, store_path=str(tmp_path / "t.json")),
                ),
            )
        )
        await _init_auth(config)
        assert security.get_user_token_store() is None

    async def test_store_is_constructed_when_user_tokens_enabled(self, tmp_path: Path) -> None:
        store_path = tmp_path / "user_tokens.json"
        config = ForgeConfig(
            security=SecurityConfig(
                oidc=_disabled_oidc_config(),
                service_tokens=ServiceTokenConfig(
                    tokens=[_one_static_admin_token()],
                    user_tokens=UserTokenConfig(enabled=True, store_path=str(store_path)),
                ),
            )
        )
        await _init_auth(config)
        store = security.get_user_token_store()
        assert isinstance(store, FileUserTokenStore)
        assert store.available

    async def test_user_token_config_is_exposed_regardless_of_enabled(self, tmp_path: Path) -> None:
        config = ForgeConfig(
            security=SecurityConfig(
                oidc=_disabled_oidc_config(),
                service_tokens=ServiceTokenConfig(
                    tokens=[_one_static_admin_token()],
                    user_tokens=UserTokenConfig(
                        enabled=False,
                        store_path=str(tmp_path / "t.json"),
                        default_ttl_seconds=7200,
                        max_ttl_seconds=14400,
                    ),
                ),
            )
        )
        await _init_auth(config)
        cfg = security.get_user_token_config()
        assert cfg is not None
        assert cfg.enabled is False
        assert cfg.default_ttl_seconds == 7200

    async def test_authorization_config_is_exposed_for_role_validation(
        self, tmp_path: Path
    ) -> None:
        config = ForgeConfig(
            security=SecurityConfig(
                oidc=_disabled_oidc_config(),
                service_tokens=ServiceTokenConfig(tokens=[_one_static_admin_token()]),
            )
        )
        await _init_auth(config)
        auth_cfg = security.get_authorization_config()
        assert auth_cfg is not None
        assert "admin" in auth_cfg.roles


class TestStoreIsLiveReference:
    async def test_verifier_and_security_state_share_the_same_store_instance(
        self, tmp_path: Path
    ) -> None:
        """A mint/revoke through the store object handed to
        ServiceTokenVerifier must be visible immediately -- this only
        holds if it is the exact same object, not a copy."""
        store_path = tmp_path / "user_tokens.json"
        config = ForgeConfig(
            security=SecurityConfig(
                oidc=_disabled_oidc_config(),
                service_tokens=ServiceTokenConfig(
                    tokens=[_one_static_admin_token()],
                    user_tokens=UserTokenConfig(enabled=True, store_path=str(store_path)),
                ),
            )
        )
        await _init_auth(config)

        state_store = security.get_user_token_store()
        verifier = security._state.service_token_verifier  # noqa: SLF001 -- white-box wiring check
        assert verifier is not None
        assert verifier._store is state_store  # noqa: SLF001


class TestHealthyGateWidenedByDynamicStore:
    def test_healthy_true_with_only_a_dynamic_store_no_static_tokens_no_oidc(
        self, tmp_path: Path
    ) -> None:
        store = FileUserTokenStore(tmp_path / "t.json")
        security.configure_auth(
            session_codec=None,
            service_token_verifier=ServiceTokenVerifier([], store=store),
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig()),
            dev_insecure=False,
            user_token_store=store,
        )
        assert security.is_auth_healthy() is True

    def test_unhealthy_when_no_mechanism_at_all(self) -> None:
        security.configure_auth(
            session_codec=None,
            service_token_verifier=ServiceTokenVerifier([]),
            oidc_verifier=None,
            authorizer=Authorizer(AuthorizationConfig()),
            dev_insecure=False,
            user_token_store=None,
        )
        assert security.is_auth_healthy() is False


class TestCorruptStoreFailsClosedWithoutBreakingStaticTokens:
    async def test_corrupt_store_file_leaves_static_tokens_and_healthy_state_intact(
        self, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "user_tokens.json"
        store_path.write_text("{not valid json")

        config = ForgeConfig(
            security=SecurityConfig(
                oidc=_disabled_oidc_config(),
                service_tokens=ServiceTokenConfig(
                    tokens=[_one_static_admin_token()],
                    user_tokens=UserTokenConfig(enabled=True, store_path=str(store_path)),
                ),
            )
        )
        await _init_auth(config)

        store = security.get_user_token_store()
        assert store is not None
        assert store.available is False
        # Static-token health and the config's own tokens are untouched --
        # only the dynamic-store feature degrades.
        assert security.is_auth_healthy() is True
        # The corrupt file itself must be left untouched for forensics.
        assert store_path.read_text() == "{not valid json"
