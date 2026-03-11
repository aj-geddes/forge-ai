"""Configuration surface version tracking via content hashing."""

from __future__ import annotations

import hashlib

from forge_config.schema import ForgeConfig


def compute_surface_version(config: ForgeConfig) -> str:
    """Compute a SHA-256 hash representing the tool surface of a config.

    This is used to detect when the effective tool surface has changed,
    triggering an atomic swap of the tool registry.

    Args:
        config: The ForgeConfig to hash.

    Returns:
        Hex-encoded SHA-256 digest of the serialized config.
    """
    serialized = config.model_dump_json(exclude={"metadata"})
    return hashlib.sha256(serialized.encode()).hexdigest()


def has_surface_changed(old_version: str, new_config: ForgeConfig) -> bool:
    """Check whether the tool surface has changed between versions.

    Args:
        old_version: The previous surface version hash.
        new_config: The new configuration to compare against.

    Returns:
        True if the surface has changed.
    """
    return compute_surface_version(new_config) != old_version
