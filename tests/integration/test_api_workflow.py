from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evalforge.api.app import create_app
from evalforge.config import Settings
from evalforge.container import AppContainer, build_container


@pytest.fixture
def api_client(tmp_path: Path) -> Iterator[tuple[TestClient, AppContainer]]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'api-workflow.db'}",
        seed_demo=True,
        auto_migrate=False,
        real_runs_enabled=False,
        openai_api_key=None,
    )
    container = build_container(settings, migrate=True)
    application = create_app(settings, container=container)
    with TestClient(application) as client:
        yield client, container
    container.engine.dispose()


def _named(items: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(item for item in items if item["name"] == name)


@pytest.mark.integration
def test_api_only_readiness_does_not_claim_an_external_worker_is_observed(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'api-only-readiness.db'}",
        seed_demo=False,
        auto_migrate=False,
        executor_mode="api_only",
    )
    container = build_container(settings, migrate=True)
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            readiness = client.get("/health/ready")
            assert readiness.status_code == 200
            assert readiness.json() == {
                "status": "ready",
                "database": "ready",
                "worker": "external_unobserved",
                "worker_observed": False,
                "executor_role": "api_only",
            }
    finally:
        container.engine.dispose()


@pytest.mark.integration
def test_complete_deterministic_evaluation_workflow(
    api_client: tuple[TestClient, AppContainer],
) -> None:
    client, _container = api_client

    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200
    capability_payload = capabilities.json()
    assert capability_payload["proof"]["demo_mode"] == "deterministic_fixture_backed"
    assert capability_payload["providers"]["real_runs_enabled"] is False
    assert "correctness" in {item["name"] for item in capability_payload["metrics"]}
    meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    assert meta.json()["executor"] == "embedded_single"
    assert meta.json()["registered_adapters"] == ["deterministic"]
    readiness = client.get("/health/ready")
    assert readiness.status_code == 200
    assert readiness.json() == {
        "status": "ready",
        "database": "ready",
        "worker": "ready",
        "worker_observed": True,
        "executor_role": "embedded_single",
    }
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    hostile_host = client.get("/health/live", headers={"Host": "attacker.example"})
    assert hostile_host.status_code == 400
    openapi = client.get("/openapi.json").json()
    dataset_list_schema = openapi["paths"]["/api/v1/datasets"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    run_schema = openapi["paths"]["/api/v1/runs/{run_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "$ref" in dataset_list_schema
    assert run_schema["$ref"].endswith("/EvaluationRunApiDetail")

    datasets = client.get("/api/v1/datasets").json()["items"]
    prompts = client.get("/api/v1/prompts").json()["items"]
    models = client.get("/api/v1/models").json()["items"]
    dataset = _named(datasets, "Grounded product Q&A")
    prompt = _named(prompts, "Grounded answer")
    reliable = _named(models, "Demo Reliable")
    risky = _named(models, "Demo Risky")
    request = {
        "name": "Reliable versus risky",
        "dataset_id": dataset["id"],
        "prompt_ids": [prompt["id"]],
        "model_ids": [reliable["id"], risky["id"]],
    }

    preflight = client.post("/api/v1/runs/preflight", json=request)
    assert preflight.status_code == 200
    assert preflight.json()["provider_call_count"] == preflight.json()["case_count"] * 2
    assert preflight.json()["automatic_provider_retries"] == 0
    assert (
        preflight.json()["maximum_provider_request_count"]
        == preflight.json()["provider_call_count"]
    )
    assert preflight.json()["real_provider"] is False

    created = client.post(
        "/api/v1/runs",
        json=request,
        headers={
            "Idempotency-Key": "workflow-proof-1",
            "X-Request-ID": "workflow-create-proof",
        },
    )
    assert created.status_code == 202
    run_id = created.json()["id"]
    assert created.headers["location"] == f"/api/v1/runs/{run_id}"

    replay = client.post(
        "/api/v1/runs", json=request, headers={"Idempotency-Key": "workflow-proof-1"}
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == run_id

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run_response = client.get(f"/api/v1/runs/{run_id}")
        assert run_response.status_code == 200
        if run_response.json()["status"] in {
            "completed",
            "completed_with_errors",
            "failed",
        }:
            break
        time.sleep(0.02)
    run = run_response.json()
    assert run["status"] == "completed"
    assert "dataset_snapshot" not in run
    assert "results" not in run
    assert "idempotency_key" not in run
    assert run["completed_items"] == run["total_items"]
    assert run["failed_items"] == 0
    assert len(run["candidates"]) == 2

    results_response = client.get(f"/api/v1/runs/{run_id}/results", params={"limit": 500})
    assert results_response.status_code == 200
    results = results_response.json()["items"]
    assert len(results) == run["total_items"]
    assert all(item["status"] == "completed" for item in results)
    assert all("aggregate_quality" in item["metric_results"] for item in results)
    assert all(item["cost_source"] == "synthetic" for item in results)
    assert all(item["estimated_cost_micro_usd"] == 0 for item in results)
    first_result = results[0]
    private_sentinels = {
        value
        for value in (
            first_result["input_snapshot"].get("input"),
            first_result["input_snapshot"].get("expected_output"),
            first_result["input_snapshot"].get("context"),
            first_result.get("output_text"),
            first_result.get("rendered_user_prompt"),
        )
        if isinstance(value, str) and value
    }
    reliable_scores = [
        item["aggregate_score"]
        for item in results
        if item["model_snapshot"]["name"] == "Demo Reliable"
    ]
    risky_scores = [
        item["aggregate_score"]
        for item in results
        if item["model_snapshot"]["name"] == "Demo Risky"
    ]
    assert sum(reliable_scores) / len(reliable_scores) > sum(risky_scores) / len(risky_scores)

    comparison = client.get(f"/api/v1/runs/{run_id}/comparison")
    assert comparison.status_code == 200
    assert len(comparison.json()["candidates"]) == 2
    exported = client.get(f"/api/v1/runs/{run_id}/export", params={"format": "csv"})
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "aggregate_quality" in exported.text.splitlines()[0]
    assert "[redacted]" in exported.text
    assert all(sentinel not in exported.text for sentinel in private_sentinels)
    json_export = client.get(f"/api/v1/runs/{run_id}/export", params={"format": "json"})
    assert json_export.status_code == 200
    assert json_export.json()["results"][0]["output_text"] == "[redacted]"
    assert all(sentinel not in json_export.text for sentinel in private_sentinels)
    full_json_export = client.get(
        f"/api/v1/runs/{run_id}/export",
        params={"format": "json", "disclosure_profile": "full_evidence"},
    )
    assert full_json_export.status_code == 200
    assert full_json_export.json()["preflight_snapshot"] == preflight.json()
    assert full_json_export.json()["results"][0]["output_text"] == first_result["output_text"]
    full_csv_export = client.get(
        f"/api/v1/runs/{run_id}/export",
        params={"format": "csv", "disclosure_profile": "full_evidence"},
    )
    assert full_csv_export.status_code == 200
    assert first_result["input_snapshot"]["input"] in full_csv_export.text
    assert first_result["output_text"] in full_csv_export.text
    package_export = client.get(
        f"/api/v1/runs/{run_id}/export",
        params={"format": "package"},
        headers={"X-Request-ID": "workflow-package-proof"},
    )
    assert package_export.status_code == 200
    assert package_export.headers["content-type"].startswith(
        "application/vnd.evalforge.run-export+json"
    )
    package = package_export.json()
    assert package["payload"]["schema_version"] == "evalforge.run-export.v1"
    assert package["payload"]["disclosure_profile"] == "content_redacted"
    assert package["payload"]["run"]["results"][0]["output_text"] == "[redacted]"
    canonical_payload = json.dumps(
        package["payload"],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert package["payload_sha256"] == hashlib.sha256(canonical_payload).hexdigest()
    assert package_export.headers["x-evalforge-payload-sha256"] == package["payload_sha256"]

    full_package = client.get(
        f"/api/v1/runs/{run_id}/export",
        params={"format": "package", "disclosure_profile": "full_evidence"},
    )
    assert full_package.status_code == 200
    assert full_package.json()["payload"]["run"]["results"][0]["output_text"] != "[redacted]"

    disabled = client.patch(f"/api/v1/models/{reliable['id']}", json={"enabled": False})
    assert disabled.status_code == 200
    replay_after_mutation = client.post(
        "/api/v1/runs", json=request, headers={"Idempotency-Key": "workflow-proof-1"}
    )
    assert replay_after_mutation.status_code == 202
    assert replay_after_mutation.json()["id"] == run_id
    history_item = client.get("/api/v1/runs").json()["items"][0]
    assert "dataset_snapshot" not in history_item
    assert "idempotency_key" not in history_item


@pytest.mark.integration
def test_api_validation_is_atomic_and_uses_safe_error_envelopes(
    api_client: tuple[TestClient, AppContainer],
) -> None:
    client, _container = api_client
    created = client.post(
        "/api/v1/datasets",
        json={"name": "Atomic import proof", "description": "No partial rows."},
    )
    assert created.status_code == 201
    dataset_id = created.json()["id"]
    invalid_import = client.post(
        f"/api/v1/datasets/{dataset_id}/imports",
        files={
            "file": (
                "cases.json",
                '[{"external_id":"valid","input":"hello"},{"external_id":"invalid"}]',
                "application/json",
            )
        },
    )
    assert invalid_import.status_code == 422
    error = invalid_import.json()["error"]
    assert error["code"] == "import_validation_failed"
    assert error["request_id"]
    assert len(error["details"]) == 1
    cases = client.get(f"/api/v1/datasets/{dataset_id}/cases")
    assert cases.status_code == 200
    assert cases.json()["total"] == 0

    null_name = client.patch(f"/api/v1/datasets/{dataset_id}", json={"name": None})
    assert null_name.status_code == 422
    assert null_name.json()["error"]["code"] == "validation_error"

    non_finite = client.post(
        "/api/v1/datasets",
        content='{"name":"Non-finite metadata","metadata_json":{"quality":NaN}}',
        headers={"Content-Type": "application/json"},
    )
    assert non_finite.status_code == 422
    assert non_finite.headers["x-request-id"]
    assert non_finite.headers["x-frame-options"] == "DENY"

    sensitive_metadata = client.post(
        "/api/v1/datasets",
        json={"name": "Sensitive metadata", "metadata_json": {"api_key": "must-reject"}},
    )
    assert sensitive_metadata.status_code == 422

    formula_case = client.post(
        f"/api/v1/datasets/{dataset_id}/cases",
        json={
            "external_id": "formula-proof",
            "position": 0,
            "input_text": "=SUM(1,1)",
            "expected_output": "safe text",
        },
    )
    assert formula_case.status_code == 201
    dataset_csv = client.get(f"/api/v1/datasets/{dataset_id}/export", params={"format": "csv"})
    assert dataset_csv.status_code == 200
    assert "'=SUM(1,1)" in dataset_csv.text

    not_found = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
    assert not_found.status_code == 404
    assert not_found.json()["error"]["message"] == "Resource not found."
    assert "x-content-type-options" in not_found.headers

    invalid_metrics = client.post(
        "/api/v1/runs/preflight",
        json={
            "dataset_id": "00000000-0000-0000-0000-000000000000",
            "prompt_ids": ["00000000-0000-0000-0000-000000000001"],
            "model_ids": ["00000000-0000-0000-0000-000000000002"],
            "metrics": [
                {
                    "name": "correctness",
                    "version": "unregistered-version",
                    "direction": "higher_is_better",
                    "weight": 1,
                    "threshold": 0.7,
                    "enabled": True,
                }
            ],
        },
    )
    assert invalid_metrics.status_code == 403
    assert invalid_metrics.json()["error"]["code"] == "capability_unavailable"

    model = _named(client.get("/api/v1/models").json()["items"], "Demo Reliable")
    invalid_parameters = client.patch(
        f"/api/v1/models/{model['id']}",
        json={"generation_parameters": {"top_p": 0.8}},
    )
    assert invalid_parameters.status_code == 403
    unchanged = client.get(f"/api/v1/models/{model['id']}").json()
    assert unchanged["generation_parameters"] == model["generation_parameters"]

    too_large = client.post(
        "/api/v1/datasets",
        content=b"{}",
        headers={"Content-Length": str((10 * 1024 * 1024) + 1)},
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "request_too_large"
    assert too_large.headers["x-content-type-options"] == "nosniff"

    oversized_idempotency_key = client.post(
        "/api/v1/runs",
        json={
            "dataset_id": "00000000-0000-0000-0000-000000000000",
            "prompt_ids": ["00000000-0000-0000-0000-000000000001"],
            "model_ids": ["00000000-0000-0000-0000-000000000002"],
        },
        headers={"Idempotency-Key": "x" * 256},
    )
    assert oversized_idempotency_key.status_code == 422
