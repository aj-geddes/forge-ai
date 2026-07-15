"""Tests for the Dex OIDC security config schema (ADR-0001).

Covers: AuthMode / SecurityAuthConfig defaulting to ``enforce``, the OIDC /
session / service-token / authorization schema blocks, and the eight
model validators in ADR-0001 Section 8.4.
"""

from __future__ import annotations

import pytest
from forge_config.schema import (
    AuthMode,
    AuthorizationConfig,
    OIDCConfig,
    Permission,
    RoleBinding,
    SecretRef,
    SecretSource,
    SecurityAuthConfig,
    SecurityConfig,
    ServiceToken,
    ServiceTokenConfig,
    SessionConfig,
)
from pydantic import ValidationError


class TestAuthModeDefault:
    def test_auth_mode_defaults_to_enforce_when_security_block_absent(self) -> None:
        """A bare, unconfigured SecurityAuthConfig defaults to enforce."""
        auth = SecurityAuthConfig()
        assert auth.mode == AuthMode.ENFORCE

    def test_security_config_with_no_overrides_defaults_to_enforce(self) -> None:
        """A totally empty SecurityConfig() (as loaded from a forge.yaml with
        no ``security:`` block at all) is enforce, not "no auth"."""
        config = SecurityConfig()
        assert config.auth.mode == AuthMode.ENFORCE

    def test_absence_of_config_still_has_a_working_enforcement_mechanism(self) -> None:
        """The default OIDC block is enabled out of the box so that
        'enforce' is never a declaration with nothing to enforce."""
        config = SecurityConfig()
        assert config.oidc.enabled is True


class TestEnforceRequiresMechanism:
    def test_enforce_with_no_oidc_and_no_service_tokens_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="nothing to enforce"):
            SecurityConfig(
                auth=SecurityAuthConfig(mode=AuthMode.ENFORCE),
                oidc=OIDCConfig(enabled=False),
                service_tokens=ServiceTokenConfig(enabled=False, tokens=[]),
            )

    def test_enforce_with_service_tokens_only_is_legal(self) -> None:
        """enforce + oidc disabled + service tokens present is a legal,
        non-degraded machine-only deployment."""
        config = SecurityConfig(
            auth=SecurityAuthConfig(mode=AuthMode.ENFORCE),
            oidc=OIDCConfig(enabled=False),
            service_tokens=ServiceTokenConfig(
                enabled=True,
                tokens=[
                    ServiceToken(
                        id="ci-deployer",
                        secret_sha256="a" * 64,
                        roles=["admin"],
                    )
                ],
            ),
        )
        assert config.service_tokens.tokens[0].id == "ci-deployer"

    def test_dev_insecure_mode_does_not_require_a_mechanism(self) -> None:
        config = SecurityConfig(
            auth=SecurityAuthConfig(mode=AuthMode.DEV_INSECURE),
            oidc=OIDCConfig(enabled=False),
            service_tokens=ServiceTokenConfig(enabled=False, tokens=[]),
        )
        assert config.auth.mode == AuthMode.DEV_INSECURE


class TestWildcardOriginRejected:
    def test_wildcard_origin_with_oidc_enabled_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="allowed_origins"):
            SecurityConfig(
                oidc=OIDCConfig(enabled=True),
                allowed_origins=["*"],
            )

    def test_wildcard_origin_with_oidc_disabled_and_service_tokens_is_allowed(self) -> None:
        """Wildcard origin is only dangerous once a session cookie can exist
        (i.e. when OIDC/BFF is enabled). A machine-only deployment with no
        cookie-based session is not exposed to the credentialed-CORS hole."""
        config = SecurityConfig(
            oidc=OIDCConfig(enabled=False),
            service_tokens=ServiceTokenConfig(
                enabled=True,
                tokens=[ServiceToken(id="x", secret_sha256="b" * 64, roles=["admin"])],
            ),
            allowed_origins=["*"],
        )
        assert config.allowed_origins == ["*"]

    def test_explicit_origin_with_oidc_enabled_is_allowed(self) -> None:
        config = SecurityConfig(
            oidc=OIDCConfig(enabled=True),
            allowed_origins=["https://forgeai.hvslocal"],
        )
        assert config.allowed_origins == ["https://forgeai.hvslocal"]


class TestSameSiteNoneRejected:
    def test_same_site_none_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="same_site"):
            SessionConfig(same_site="none")

    def test_same_site_lax_and_strict_are_accepted(self) -> None:
        assert SessionConfig(same_site="lax").same_site == "lax"
        assert SessionConfig(same_site="strict").same_site == "strict"


class TestSessionSecureRequiredInEnforceMode:
    def test_session_secure_false_in_enforce_mode_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="secure"):
            SecurityConfig(
                auth=SecurityAuthConfig(mode=AuthMode.ENFORCE),
                oidc=OIDCConfig(enabled=True, session=SessionConfig(secure=False)),
            )

    def test_session_secure_false_in_dev_insecure_mode_is_allowed(self) -> None:
        config = SecurityConfig(
            auth=SecurityAuthConfig(mode=AuthMode.DEV_INSECURE),
            oidc=OIDCConfig(enabled=True, session=SessionConfig(secure=False)),
        )
        assert config.oidc.session.secure is False


class TestJwtSecretRemoved:
    def test_jwt_secret_present_raises_config_error_with_migration_message(self) -> None:
        with pytest.raises(ValidationError, match="jwt_secret"):
            SecurityConfig(jwt_secret={"source": "env", "name": "OLD_SECRET"})

    def test_jwt_secret_present_as_kwarg_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="jwt_secret"):
            SecurityConfig(jwt_secret=SecretRef(source=SecretSource.ENV, name="OLD_SECRET"))


class TestBindingRoleValidation:
    def test_binding_referencing_unknown_role_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="unknown role"):
            SecurityConfig(
                authorization=AuthorizationConfig(
                    bindings=[RoleBinding(role="superuser", emails=["a@b.com"])],
                )
            )

    def test_role_with_unknown_permission_raises_config_error(self) -> None:
        with pytest.raises(ValidationError, match="unknown permission"):
            SecurityConfig(
                authorization=AuthorizationConfig(
                    roles={"custom": ["delete:everything"]},
                )
            )

    def test_wildcard_permission_is_accepted(self) -> None:
        config = SecurityConfig(
            authorization=AuthorizationConfig(roles={"admin": ["*"]}),
        )
        assert config.authorization.roles["admin"] == ["*"]

    def test_default_roles_and_bindings_are_valid(self) -> None:
        """The built-in default roles/bindings must themselves pass the
        validators (self-consistency)."""
        config = SecurityConfig()
        assert "viewer" in config.authorization.roles
        assert "user" in config.authorization.roles
        assert "admin" in config.authorization.roles


class TestServiceTokenHashValidation:
    def test_service_token_sha256_must_be_64_hex_chars(self) -> None:
        with pytest.raises(ValidationError, match="secret_sha256"):
            ServiceToken(id="bad", secret_sha256="not-hex", roles=["admin"])

    def test_service_token_sha256_rejects_too_short(self) -> None:
        with pytest.raises(ValidationError, match="secret_sha256"):
            ServiceToken(id="bad", secret_sha256="a" * 63, roles=["admin"])

    def test_service_token_sha256_accepts_valid_digest(self) -> None:
        token = ServiceToken(id="ok", secret_sha256="f" * 64, roles=["admin"])
        assert token.secret_sha256 == "f" * 64


class TestAudienceDefaulting:
    def test_audience_defaults_to_client_id(self) -> None:
        oidc = OIDCConfig(client_id="forge-ai", audience=None)
        assert oidc.audience == "forge-ai"

    def test_audience_explicit_value_is_preserved(self) -> None:
        oidc = OIDCConfig(client_id="forge-ai", audience="explicit-audience")
        assert oidc.audience == "explicit-audience"


class TestApiKeysDeprecationShim:
    def test_api_keys_config_still_parses_and_emits_deprecation_warning(self) -> None:
        with pytest.warns(DeprecationWarning, match="api_keys"):
            config = SecurityConfig(
                api_keys={
                    "enabled": True,
                    "keys": [{"source": "env", "name": "FORGE_API_KEY"}],
                }
            )
        assert config.api_keys.enabled is True

    def test_api_keys_disabled_emits_no_warning(self, recwarn: pytest.WarningsRecorder) -> None:
        SecurityConfig(api_keys={"enabled": False, "keys": []})
        deprecation_warnings = [
            w for w in recwarn.list if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecation_warnings == []


class TestIssuerScheme:
    def test_issuer_must_be_https_in_enforce_mode(self) -> None:
        with pytest.raises(ValidationError, match="https"):
            SecurityConfig(
                auth=SecurityAuthConfig(mode=AuthMode.ENFORCE),
                oidc=OIDCConfig(enabled=True, issuer="http://dex.hvslocal/dex"),
            )

    def test_issuer_http_allowed_in_dev_insecure_mode(self) -> None:
        config = SecurityConfig(
            auth=SecurityAuthConfig(mode=AuthMode.DEV_INSECURE),
            oidc=OIDCConfig(
                enabled=True,
                issuer="http://localhost:5556/dex",
                session=SessionConfig(secure=False),
            ),
        )
        assert config.oidc.issuer == "http://localhost:5556/dex"


class TestOIDCClientSecretOptional:
    """The registered Dex client (client_id=forge-ai) is a PUBLIC client
    secured by PKCE -- there is no client_secret to configure. See the
    developer report for why this deviates from the ADR's confidential-
    client example."""

    def test_client_secret_defaults_to_none(self) -> None:
        assert OIDCConfig().client_secret is None

    def test_client_secret_can_still_be_configured_for_a_confidential_client(self) -> None:
        oidc = OIDCConfig(
            client_secret=SecretRef(source=SecretSource.ENV, name="FORGE_OIDC_CLIENT_SECRET")
        )
        assert oidc.client_secret is not None
        assert oidc.client_secret.name == "FORGE_OIDC_CLIENT_SECRET"


class TestPermissionEnum:
    def test_permission_values_match_adr_closed_set(self) -> None:
        values = {p.value for p in Permission}
        assert values == {
            "agent:invoke",
            "tools:invoke",
            "agent:peer",
            "agent:approve",
            "config:read",
            "config:write",
            "metrics:read",
            "infrastructure:write",
        }
