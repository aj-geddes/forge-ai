"""Secret resolution protocols and implementations."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from forge_config.exceptions import SecretResolutionError
from forge_config.schema import SecretRef, SecretSource


@runtime_checkable
class SecretResolver(Protocol):
    """Protocol for resolving secret references to their values."""

    def resolve(self, ref: SecretRef) -> str:
        """Resolve a secret reference to its plaintext value.

        Args:
            ref: The secret reference to resolve.

        Returns:
            The resolved secret value.

        Raises:
            SecretResolutionError: If the secret cannot be resolved.
        """
        ...


class EnvSecretResolver:
    """Resolves secrets from environment variables."""

    def resolve(self, ref: SecretRef) -> str:
        if ref.source != SecretSource.ENV:
            msg = f"EnvSecretResolver only handles env secrets, got {ref.source}"
            raise SecretResolutionError(msg)

        value = os.environ.get(ref.name)
        if value is None:
            msg = f"Environment variable '{ref.name}' not set"
            raise SecretResolutionError(msg)

        return value


class CompositeSecretResolver:
    """Delegates to the appropriate resolver based on secret source."""

    def __init__(self, resolvers: dict[SecretSource, SecretResolver] | None = None) -> None:
        if resolvers is not None:
            self._resolvers: dict[SecretSource, SecretResolver] = resolvers
        else:
            self._resolvers = {SecretSource.ENV: EnvSecretResolver()}

    def register(self, source: SecretSource, resolver: SecretResolver) -> None:
        self._resolvers[source] = resolver

    def resolve(self, ref: SecretRef) -> str:
        resolver = self._resolvers.get(ref.source)
        if resolver is None:
            msg = f"No resolver registered for source: {ref.source}"
            raise SecretResolutionError(msg)
        return resolver.resolve(ref)
