from __future__ import annotations

import pytest
from forge_config.schema import ServiceToken, ServiceTokenConfig, UserTokenConfig
from pydantic import ValidationError


def test_user_tokens_disabled_by_default():
    config = ServiceTokenConfig()
    assert config.user_tokens.enabled is False


def test_service_token_config_carries_user_tokens_block():
    config = ServiceTokenConfig()
    assert isinstance(config.user_tokens, UserTokenConfig)


def test_default_store_path_matches_adr():
    config = ServiceTokenConfig()
    assert config.user_tokens.store_path == "/app/data/user_tokens.json"


def test_default_ttl_is_30_days():
    config = ServiceTokenConfig()
    assert config.user_tokens.default_ttl_seconds == 2_592_000


def test_max_ttl_is_90_days():
    config = ServiceTokenConfig()
    assert config.user_tokens.max_ttl_seconds == 7_776_000


def test_default_ttl_must_not_exceed_max_ttl_raises_config_error():
    with pytest.raises(ValidationError) as exc_info:
        UserTokenConfig(default_ttl_seconds=8_000_000, max_ttl_seconds=7_776_000)
    assert "default_ttl_seconds" in str(exc_info.value)


def test_default_ttl_below_the_one_hour_floor_raises_config_error():
    with pytest.raises(ValidationError) as exc_info:
        UserTokenConfig(default_ttl_seconds=3599)
    assert "default_ttl_seconds" in str(exc_info.value)


def test_default_ttl_equal_to_max_ttl_is_legal():
    config = UserTokenConfig(default_ttl_seconds=7_776_000, max_ttl_seconds=7_776_000)
    assert config.default_ttl_seconds == config.max_ttl_seconds


def test_default_ttl_equal_to_floor_is_legal():
    config = UserTokenConfig(default_ttl_seconds=3600)
    assert config.default_ttl_seconds == 3600


def test_store_path_must_be_absolute():
    with pytest.raises(ValidationError) as exc_info:
        UserTokenConfig(store_path="relative/path.json")
    assert "absolute" in str(exc_info.value)


def test_absolute_store_path_accepted():
    config = UserTokenConfig(store_path="/app/data/user_tokens.json")
    assert config.store_path == "/app/data/user_tokens.json"


def test_static_tokens_still_parse_unchanged():
    token = ServiceToken(id="static-token", secret_sha256="a" * 64, roles=["read"])
    config = ServiceTokenConfig(tokens=[token])
    assert len(config.tokens) == 1
    assert config.tokens[0].id == "static-token"
    assert config.user_tokens.enabled is False


def test_user_tokens_not_required_when_loading_legacy_dict():
    legacy_dict = {"enabled": False, "tokens": []}
    config = ServiceTokenConfig.model_validate(legacy_dict)
    assert config.user_tokens.enabled is False


def test_static_token_id_with_reserved_prefix_raises_config_error():
    with pytest.raises(ValidationError) as exc_info:
        ServiceTokenConfig(
            tokens=[ServiceToken(id="u_reserved", secret_sha256="b" * 64, roles=["read"])]
        )
    assert "reserved" in str(exc_info.value)


def test_static_token_id_without_reserved_prefix_is_legal():
    config = ServiceTokenConfig(
        tokens=[ServiceToken(id="my-token", secret_sha256="c" * 64, roles=["read"])]
    )
    assert config.tokens[0].id == "my-token"


def test_default_max_tokens_per_owner_is_25():
    config = UserTokenConfig()
    assert config.max_tokens_per_owner == 25


def test_custom_max_tokens_per_owner_is_honored():
    config = UserTokenConfig(max_tokens_per_owner=5)
    assert config.max_tokens_per_owner == 5


def test_max_tokens_per_owner_below_one_raises_config_error():
    with pytest.raises(ValidationError) as exc_info:
        UserTokenConfig(max_tokens_per_owner=0)
    assert "max_tokens_per_owner" in str(exc_info.value)


def test_max_tokens_per_owner_of_one_is_legal():
    config = UserTokenConfig(max_tokens_per_owner=1)
    assert config.max_tokens_per_owner == 1


def test_max_tokens_per_owner_omitted_from_legacy_dict_uses_default():
    legacy_dict = {"enabled": True, "store_path": "/app/data/user_tokens.json"}
    config = UserTokenConfig.model_validate(legacy_dict)
    assert config.max_tokens_per_owner == 25
