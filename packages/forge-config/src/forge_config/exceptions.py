"""Forge Config exceptions."""


class ConfigError(Exception):
    """Base exception for all configuration errors."""


class ConfigLoadError(ConfigError):
    """Raised when a configuration file cannot be loaded."""


class ConfigValidationError(ConfigError):
    """Raised when configuration fails validation."""


class SecretResolutionError(ConfigError):
    """Raised when a secret reference cannot be resolved."""
