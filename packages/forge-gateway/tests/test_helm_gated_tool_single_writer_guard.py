"""ADR-0005 SS6.2/SS11 (security review finding #5): a gated tool
(``ManualTool.requires_approval``, ``OpenAPISource.requires_approval``, or
``OpenAPISource.approval_operations``) configured together with more than
one agent replica must fail the Helm render loudly, rather than silently
letting a future ``replicaCount``/autoscaling bump reintroduce a
cross-replica double-approval risk (the in-memory ``ApprovalStore`` does
not synchronize across replicas -- see the
``forge_agent.active.gate`` module docstring).

These tests shell out to the real ``helm template`` binary against
``deploy/helm/forge`` -- skipped (not failed) when ``helm`` isn't on PATH,
since this is an infrastructure-tooling check, not a Python unit test, but
it still needs to run under ``uv run pytest -q`` per this repo's
``testpaths`` (``packages/*/tests``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI not installed")

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm" / "forge"


def _helm_template(*extra_args: str) -> subprocess.CompletedProcess[str]:
    # Fixed argv (no shell, no untrusted input) invoking the real `helm`
    # binary against this repo's own chart -- resolved via PATH is
    # intentional (this test module is already skipped when helm isn't
    # installed).
    return subprocess.run(  # noqa: S603
        ["helm", "template", "guard-test", str(_CHART_DIR), *extra_args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _with_forge_config(config: dict[str, object]) -> str:
    return f"forgeConfig={json.dumps(config)}"


_GATED_MANUAL_TOOL_CONFIG = {
    "tools": {
        "manual_tools": [
            {
                "name": "publish",
                "description": "d",
                "api": {"url": "https://x", "method": "POST"},
                "requires_approval": True,
            }
        ]
    }
}

_GATED_OPENAPI_APPROVAL_OPS_CONFIG = {
    "tools": {
        "openapi_sources": [
            {"name": "postiz", "url": "https://x", "approval_operations": ["publish"]}
        ]
    }
}

_GATED_OPENAPI_SOURCE_CONFIG = {
    "tools": {
        "openapi_sources": [{"name": "postiz", "url": "https://x", "requires_approval": True}]
    }
}

_UNGATED_CONFIG = {
    "tools": {
        "manual_tools": [
            {"name": "plain", "description": "d", "api": {"url": "https://x", "method": "GET"}}
        ]
    }
}


class TestGatedToolRequiresSingleWriter:
    def test_gated_manual_tool_with_multiple_replicas_fails_render(self) -> None:
        result = _helm_template(
            "--set",
            "agent.replicaCount=3",
            "--set-json",
            _with_forge_config(_GATED_MANUAL_TOOL_CONFIG),
        )
        assert result.returncode != 0
        assert "single writer" in result.stderr
        assert "ApprovalStore" in result.stderr

    def test_gated_openapi_source_with_multiple_replicas_fails_render(self) -> None:
        result = _helm_template(
            "--set",
            "agent.replicaCount=3",
            "--set-json",
            _with_forge_config(_GATED_OPENAPI_SOURCE_CONFIG),
        )
        assert result.returncode != 0
        assert "single writer" in result.stderr

    def test_gated_openapi_approval_operations_with_autoscaling_fails_render(self) -> None:
        result = _helm_template(
            "--set",
            "autoscaling.enabled=true",
            "--set-json",
            _with_forge_config(_GATED_OPENAPI_APPROVAL_OPS_CONFIG),
        )
        assert result.returncode != 0
        assert "single writer" in result.stderr

    def test_gated_manual_tool_with_single_replica_renders_successfully(self) -> None:
        result = _helm_template(
            "--set",
            "agent.replicaCount=1",
            "--set-json",
            _with_forge_config(_GATED_MANUAL_TOOL_CONFIG),
        )
        assert result.returncode == 0, result.stderr

    def test_ungated_tools_with_multiple_replicas_renders_successfully(self) -> None:
        """The guard must not fire for an ordinary (non-gated) tool --
        only actual approval-gated tools require a single writer."""
        result = _helm_template(
            "--set", "agent.replicaCount=3", "--set-json", _with_forge_config(_UNGATED_CONFIG)
        )
        assert result.returncode == 0, result.stderr

    def test_no_forge_config_with_multiple_replicas_renders_successfully(self) -> None:
        result = _helm_template("--set", "agent.replicaCount=3")
        assert result.returncode == 0, result.stderr


class TestLayeredConfigEnvVars:
    """Layered BASE+OVERLAY config (design doc): FORGE_CONFIG_SEED_PATH /
    FORGE_CONFIG_OVERLAY_PATH are only rendered when persistence is
    enabled (there is no durable place to write the overlay otherwise),
    and the pre-existing single-replica RWO fail-guard still trips
    regardless -- the new env vars don't create a second, unguarded
    write path.
    """

    def test_overlay_env_vars_rendered_when_persistence_enabled(self) -> None:
        result = _helm_template("--set", "persistence.enabled=true")
        assert result.returncode == 0, result.stderr
        assert "FORGE_CONFIG_SEED_PATH" in result.stdout
        assert "FORGE_CONFIG_OVERLAY_PATH" in result.stdout
        assert "/app/data/overlay/forge.overlay.yaml" in result.stdout

    def test_overlay_env_vars_absent_when_persistence_disabled(self) -> None:
        result = _helm_template("--set", "persistence.enabled=false")
        assert result.returncode == 0, result.stderr
        assert "FORGE_CONFIG_SEED_PATH" not in result.stdout
        assert "FORGE_CONFIG_OVERLAY_PATH" not in result.stdout

    def test_single_replica_fail_guard_still_trips_with_overlay_enabled(self) -> None:
        result = _helm_template(
            "--set", "persistence.enabled=true", "--set", "agent.replicaCount=2"
        )
        assert result.returncode != 0
        assert "single writer" in result.stderr

    def test_autoscaling_still_trips_the_fail_guard_with_overlay_enabled(self) -> None:
        result = _helm_template(
            "--set", "persistence.enabled=true", "--set", "autoscaling.enabled=true"
        )
        assert result.returncode != 0
        assert "single writer" in result.stderr
