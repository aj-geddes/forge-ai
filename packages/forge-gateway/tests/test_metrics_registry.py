from __future__ import annotations

import pytest
from forge_gateway import metrics_registry
from forge_gateway.metrics_registry import (
    record_agent_invocation,
    record_http_request,
    record_tool_invocation,
)
from prometheus_client import REGISTRY


class TestMetricFamiliesExist:
    """Test that all expected metric families are registered."""

    def test_http_requests_counter_family_exists(self) -> None:
        """Verify forge_http_requests metric family is registered."""
        metric_names = {m.name for m in REGISTRY.collect()}
        assert "forge_http_requests" in metric_names

    def test_http_request_duration_histogram_family_exists(self) -> None:
        """Verify forge_http_request_duration_seconds metric family is registered."""
        metric_names = {m.name for m in REGISTRY.collect()}
        assert "forge_http_request_duration_seconds" in metric_names

    def test_agent_invocations_counter_family_exists(self) -> None:
        """Verify forge_agent_invocations metric family is registered."""
        metric_names = {m.name for m in REGISTRY.collect()}
        assert "forge_agent_invocations" in metric_names

    def test_agent_invocation_duration_histogram_family_exists(self) -> None:
        """Verify forge_agent_invocation_duration_seconds metric family is registered."""
        metric_names = {m.name for m in REGISTRY.collect()}
        assert "forge_agent_invocation_duration_seconds" in metric_names

    def test_tool_invocations_counter_family_exists(self) -> None:
        """Verify forge_tool_invocations metric family is registered."""
        metric_names = {m.name for m in REGISTRY.collect()}
        assert "forge_tool_invocations" in metric_names


class TestRecordHttpRequest:
    """Test record_http_request function."""

    def test_records_counter_with_exact_labels(self) -> None:
        """Verify counter increments for exact label set."""
        method = "unit-test-GET"
        path = "/unit-test/path/1"
        status_code = 201
        duration_seconds = 0.123

        # Get initial counter value
        initial_count = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": method, "path": path, "status": str(status_code)},
        )
        if initial_count is None:
            initial_count = 0

        # Record the request
        record_http_request(method, path, status_code, duration_seconds)

        # Verify counter incremented by 1
        final_count = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": method, "path": path, "status": str(status_code)},
        )
        assert final_count == initial_count + 1

    def test_records_histogram_count(self) -> None:
        """Verify histogram count increments."""
        method = "unit-test-POST"
        path = "/unit-test/path/2"
        status_code = 200
        duration_seconds = 0.456

        # Get initial histogram count
        initial_count = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": method, "path": path, "status": str(status_code)},
        )
        if initial_count is None:
            initial_count = 0

        # Record the request
        record_http_request(method, path, status_code, duration_seconds)

        # Verify histogram count incremented by 1
        final_count = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": method, "path": path, "status": str(status_code)},
        )
        assert final_count == initial_count + 1

    def test_records_histogram_sum(self) -> None:
        """Verify histogram sum includes the recorded duration."""
        method = "unit-test-PUT"
        path = "/unit-test/path/3"
        status_code = 204
        duration_seconds = 0.789

        # Get initial histogram sum
        initial_sum = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_sum",
            {"method": method, "path": path, "status": str(status_code)},
        )
        if initial_sum is None:
            initial_sum = 0.0

        # Record the request
        record_http_request(method, path, status_code, duration_seconds)

        # Verify histogram sum increased by the duration
        final_sum = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_sum",
            {"method": method, "path": path, "status": str(status_code)},
        )
        assert final_sum == pytest.approx(initial_sum + duration_seconds, rel=1e-9)

    def test_different_status_codes_tracked_independently(self) -> None:
        """Verify different status codes are tracked separately."""
        method = "unit-test-DELETE"
        path = "/unit-test/path/4"

        # Record two requests with different status codes
        record_http_request(method, path, 200, 0.1)
        record_http_request(method, path, 404, 0.2)

        # Verify each status code has its own counter
        count_200 = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": method, "path": path, "status": "200"},
        )
        count_404 = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": method, "path": path, "status": "404"},
        )

        assert count_200 == 1
        assert count_404 == 1


class TestRecordAgentInvocation:
    """Test record_agent_invocation function."""

    def test_records_counter_with_exact_labels(self) -> None:
        """Verify counter increments for exact label set."""
        interface = "unit-test-interface-1"
        status = "success"
        duration_seconds = 1.234

        # Get initial counter value
        initial_count = REGISTRY.get_sample_value(
            "forge_agent_invocations_total",
            {"interface": interface, "status": status},
        )
        if initial_count is None:
            initial_count = 0

        # Record the invocation
        record_agent_invocation(interface, status, duration_seconds)

        # Verify counter incremented by 1
        final_count = REGISTRY.get_sample_value(
            "forge_agent_invocations_total",
            {"interface": interface, "status": status},
        )
        assert final_count == initial_count + 1

    def test_records_histogram_count(self) -> None:
        """Verify histogram count increments."""
        interface = "unit-test-interface-2"
        status = "success"
        duration_seconds = 2.345

        # Get initial histogram count
        initial_count = REGISTRY.get_sample_value(
            "forge_agent_invocation_duration_seconds_count",
            {"interface": interface},
        )
        if initial_count is None:
            initial_count = 0

        # Record the invocation
        record_agent_invocation(interface, status, duration_seconds)

        # Verify histogram count incremented by 1
        final_count = REGISTRY.get_sample_value(
            "forge_agent_invocation_duration_seconds_count",
            {"interface": interface},
        )
        assert final_count == initial_count + 1

    def test_records_histogram_sum(self) -> None:
        """Verify histogram sum includes the recorded duration."""
        interface = "unit-test-interface-3"
        status = "success"
        duration_seconds = 3.456

        # Get initial histogram sum
        initial_sum = REGISTRY.get_sample_value(
            "forge_agent_invocation_duration_seconds_sum",
            {"interface": interface},
        )
        if initial_sum is None:
            initial_sum = 0.0

        # Record the invocation
        record_agent_invocation(interface, status, duration_seconds)

        # Verify histogram sum increased by the duration
        final_sum = REGISTRY.get_sample_value(
            "forge_agent_invocation_duration_seconds_sum",
            {"interface": interface},
        )
        assert final_sum == pytest.approx(initial_sum + duration_seconds, rel=1e-9)

    def test_different_statuses_tracked_independently(self) -> None:
        """Verify metrics for different statuses are tracked separately."""
        interface = "unit-test-interface-4"

        # Record invocations with different statuses
        record_agent_invocation(interface, "success", 1.0)
        record_agent_invocation(interface, "failure", 2.0)

        # Verify each status has its own counter
        count_success = REGISTRY.get_sample_value(
            "forge_agent_invocations_total",
            {"interface": interface, "status": "success"},
        )
        count_failure = REGISTRY.get_sample_value(
            "forge_agent_invocations_total",
            {"interface": interface, "status": "failure"},
        )

        assert count_success == 1
        assert count_failure == 1


class TestRecordToolInvocation:
    """Test record_tool_invocation function."""

    def test_records_counter_with_correct_label(self) -> None:
        """Verify counter increments with correct tool name label."""
        tool_name = "unit-test-tool-alpha"

        # Get initial counter value
        initial_count = REGISTRY.get_sample_value(
            "forge_tool_invocations_total",
            {"tool": tool_name},
        )
        if initial_count is None:
            initial_count = 0

        # Record the invocation
        record_tool_invocation(tool_name)

        # Verify increment
        final_count = REGISTRY.get_sample_value(
            "forge_tool_invocations_total",
            {"tool": tool_name},
        )
        assert final_count == initial_count + 1

    def test_different_tools_tracked_independently(self) -> None:
        """Verify metrics for different tools are tracked separately."""
        tool1 = "unit-test-tool-beta"
        tool2 = "unit-test-tool-gamma"

        # Record tool1
        record_tool_invocation(tool1)
        # Record tool2
        record_tool_invocation(tool2)

        # Verify both tools have count=1
        count1 = REGISTRY.get_sample_value(
            "forge_tool_invocations_total",
            {"tool": tool1},
        )
        count2 = REGISTRY.get_sample_value(
            "forge_tool_invocations_total",
            {"tool": tool2},
        )
        assert count1 == 1
        assert count2 == 1


class TestGracefulDegradationWhenPrometheusUnavailable:
    def test_record_http_request_is_noop_when_prometheus_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(metrics_registry, "PROMETHEUS_AVAILABLE", False)
        assert record_http_request("GET", "/x", 200, 0.01) is None

    def test_record_agent_invocation_is_noop_when_prometheus_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(metrics_registry, "PROMETHEUS_AVAILABLE", False)
        assert record_agent_invocation("chat", "success", 0.1) is None

    def test_record_tool_invocation_is_noop_when_prometheus_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(metrics_registry, "PROMETHEUS_AVAILABLE", False)
        assert record_tool_invocation("test-tool") is None
