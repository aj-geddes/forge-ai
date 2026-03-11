"""YAML configuration loading with environment variable overlay."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from forge_config.exceptions import ConfigLoadError, ConfigValidationError
from forge_config.schema import ForgeConfig

_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute ${VAR} and ${VAR:default} in string values."""
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default = match.group(2)
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            return match.group(0)  # Leave unresolved

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


def load_config(
    path: str | Path,
    *,
    env_overlay: bool = True,
) -> ForgeConfig:
    """Load and validate a Forge configuration file.

    Args:
        path: Path to the YAML configuration file.
        env_overlay: Whether to substitute environment variables in values.

    Returns:
        Validated ForgeConfig instance.

    Raises:
        ConfigLoadError: If the file cannot be read or parsed.
        ConfigValidationError: If the config fails Pydantic validation.
    """
    path = Path(path)

    if not path.exists():
        msg = f"Configuration file not found: {path}"
        raise ConfigLoadError(msg)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        msg = f"Cannot read configuration file: {path}"
        raise ConfigLoadError(msg) from e

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        msg = f"Invalid YAML in {path}: {e}"
        raise ConfigLoadError(msg) from e

    if data is None:
        data = {}

    if not isinstance(data, dict):
        msg = f"Configuration root must be a mapping, got {type(data).__name__}"
        raise ConfigLoadError(msg)

    if env_overlay:
        data = _substitute_env_vars(data)

    try:
        return ForgeConfig.model_validate(data)
    except ValidationError as e:
        msg = f"Configuration validation failed: {e}"
        raise ConfigValidationError(msg) from e
