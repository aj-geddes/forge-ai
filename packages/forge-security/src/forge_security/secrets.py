"""Secret resolvers for Forge.

``K8sSecretResolver`` reads secrets from files (simulating Kubernetes
volume-mounted secrets).  ``ForgeCompositeSecretResolver`` delegates to the
appropriate resolver based on the ``SecretSource`` of a ``SecretRef``.
"""

from __future__ import annotations

from pathlib import Path

from forge_config.exceptions import SecretResolutionError
from forge_config.schema import SecretRef, SecretSource
from forge_config.secret_resolver import EnvSecretResolver, SecretResolver


class K8sSecretResolver:
    """Resolve secrets by reading files from a Kubernetes-mounted volume.

    By default Kubernetes mounts secrets at
    ``/var/run/secrets/kubernetes.io/serviceaccount/`` but arbitrary mount
    paths are supported.

    The file is expected at ``<base_path>/<secret_name>/<key>``.

    Parameters
    ----------
    base_path:
        Root directory where secret volumes are mounted.
    """

    def __init__(self, base_path: str = "/var/run/secrets") -> None:
        self._base = Path(base_path)

    def resolve(self, ref: SecretRef) -> str:
        if ref.source != SecretSource.K8S_SECRET:
            msg = f"K8sSecretResolver only handles k8s_secret, got {ref.source}"
            raise SecretResolutionError(msg)

        if ref.key is None:
            msg = "key is required for k8s_secret references"
            raise SecretResolutionError(msg)

        secret_file = self._base / ref.name / ref.key
        try:
            return secret_file.read_text().strip()
        except FileNotFoundError:
            msg = f"Secret file not found: {secret_file}"
            raise SecretResolutionError(msg) from None
        except OSError as exc:
            msg = f"Failed to read secret file {secret_file}: {exc}"
            raise SecretResolutionError(msg) from exc


class ForgeCompositeSecretResolver:
    """Composite resolver that delegates to source-specific resolvers.

    By default an ``EnvSecretResolver`` and a ``K8sSecretResolver`` are
    registered.  Additional resolvers can be added via ``register``.
    """

    def __init__(
        self,
        resolvers: dict[SecretSource, SecretResolver] | None = None,
    ) -> None:
        if resolvers is not None:
            self._resolvers: dict[SecretSource, SecretResolver] = dict(resolvers)
        else:
            self._resolvers = {
                SecretSource.ENV: EnvSecretResolver(),
                SecretSource.K8S_SECRET: K8sSecretResolver(),
            }

    def register(self, source: SecretSource, resolver: SecretResolver) -> None:
        """Register a resolver for *source*."""
        self._resolvers[source] = resolver

    def resolve(self, ref: SecretRef) -> str:
        resolver = self._resolvers.get(ref.source)
        if resolver is None:
            msg = f"No resolver registered for source: {ref.source}"
            raise SecretResolutionError(msg)
        return resolver.resolve(ref)
