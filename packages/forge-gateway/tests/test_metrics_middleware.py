from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from forge_gateway.middleware.metrics import PrometheusMetricsMiddleware
from prometheus_client import REGISTRY


def create_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(PrometheusMetricsMiddleware)

    @app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, Any]:
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        raise HTTPException(status_code=500, detail="Something went wrong")

    return app


class TestPrometheusMetricsMiddleware:
    def test_records_same_path_label_for_different_concrete_paths(self) -> None:
        app = create_test_app()
        client = TestClient(app)

        # Record baseline counts before requests
        counter_before = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": "GET", "path": "/items/{item_id}", "status": "200"},
        )
        histogram_count_before = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": "GET", "path": "/items/{item_id}", "status": "200"},
        )

        # Make two requests with different concrete paths
        for item_id in [11111111, 22222222]:
            response = client.get(f"/items/{item_id}")
            assert response.status_code == 200

        # Verify counter increased by 2 (both requests share same path label)
        counter_after = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": "GET", "path": "/items/{item_id}", "status": "200"},
        )
        histogram_count_after = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": "GET", "path": "/items/{item_id}", "status": "200"},
        )

        assert (counter_after or 0.0) - (counter_before or 0.0) == 2.0
        assert (histogram_count_after or 0.0) - (histogram_count_before or 0.0) == 2.0

    def test_records_metrics_for_500_error_response(self) -> None:
        app = create_test_app()
        client = TestClient(app)

        counter_before = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": "GET", "path": "/boom", "status": "500"},
        )
        histogram_count_before = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": "GET", "path": "/boom", "status": "500"},
        )

        response = client.get("/boom")
        assert response.status_code == 500

        counter_after = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": "GET", "path": "/boom", "status": "500"},
        )
        histogram_count_after = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": "GET", "path": "/boom", "status": "500"},
        )

        assert (counter_after or 0.0) - (counter_before or 0.0) == 1.0
        assert (histogram_count_after or 0.0) - (histogram_count_before or 0.0) == 1.0

    def test_records_metrics_with_fallback_path_for_unmatched_route(self) -> None:
        app = create_test_app()
        client = TestClient(app)

        counter_before = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": "GET", "path": "/nonexistent", "status": "404"},
        )
        histogram_count_before = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": "GET", "path": "/nonexistent", "status": "404"},
        )

        response = client.get("/nonexistent")
        assert response.status_code == 404

        counter_after = REGISTRY.get_sample_value(
            "forge_http_requests_total",
            {"method": "GET", "path": "/nonexistent", "status": "404"},
        )
        histogram_count_after = REGISTRY.get_sample_value(
            "forge_http_request_duration_seconds_count",
            {"method": "GET", "path": "/nonexistent", "status": "404"},
        )

        assert (counter_after or 0.0) - (counter_before or 0.0) == 1.0
        assert (histogram_count_after or 0.0) - (histogram_count_before or 0.0) == 1.0

    def test_does_not_alter_response_status_code_or_body(self) -> None:
        app = create_test_app()
        client = TestClient(app)
        item_id = 99999999
        response = client.get(f"/items/{item_id}")
        assert response.status_code == 200
        assert response.json() == {"item_id": item_id}
