from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from evalforge.api.app import create_app
from evalforge.config import Settings
from evalforge.container import AppContainer, build_container
from evalforge.evaluation.calibration_io import MAX_CALIBRATION_FILE_BYTES
from evalforge.models import AuditEvent, CalibrationReport


@pytest.fixture
def calibration_api(tmp_path: Path) -> Iterator[tuple[TestClient, AppContainer]]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'calibration-api.db'}",
        seed_demo=True,
        auto_migrate=False,
        real_runs_enabled=False,
        openai_api_key=None,
    )
    container = build_container(settings, migrate=True)
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            yield client, container
    finally:
        container.engine.dispose()


def _named(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(item for item in items if item["name"] == name)


def _completed_run(client: TestClient) -> dict[str, Any]:
    dataset = _named(client.get("/api/v1/datasets").json()["items"], "Grounded product Q&A")
    prompt = _named(client.get("/api/v1/prompts").json()["items"], "Grounded answer")
    model = _named(client.get("/api/v1/models").json()["items"], "Demo Reliable")
    response = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "calibration-api-run"},
        json={
            "name": "Calibration API proof",
            "dataset_id": dataset["id"],
            "prompt_ids": [prompt["id"]],
            "model_ids": [model["id"]],
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = client.get(f"/api/v1/runs/{run_id}")
        assert current.status_code == 200, current.text
        if current.json()["status"] in {"completed", "completed_with_errors", "failed"}:
            break
        time.sleep(0.02)
    assert current.json()["status"] == "completed"
    return current.json()


def _filled_json_template(
    client: TestClient,
    *,
    run_id: str,
    candidate_id: str,
    reviewer_id: str,
) -> tuple[bytes, dict[str, Any]]:
    response = client.get(
        f"/api/v1/runs/{run_id}/calibrations/template",
        params={
            "candidate_id": candidate_id,
            "metric_name": "correctness",
            "format": "json",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert int(response.headers["x-evalforge-sample-size"]) > 0

    manifest = response.json()
    assert manifest["labels"]
    assert all(row["human_passed"] is None for row in manifest["labels"])
    assert all(row["reviewer_id"] == "" for row in manifest["labels"])
    for index, row in enumerate(manifest["labels"]):
        row["human_passed"] = index % 2 == 0
        row["reviewer_id"] = reviewer_id
    return json.dumps(manifest, separators=(",", ":")).encode("utf-8"), manifest


def _post_calibration(
    client: TestClient,
    *,
    run_id: str,
    form: dict[str, str],
    payload: bytes | str,
    filename: str = "labels.json",
    headers: dict[str, str] | None = None,
) -> Any:
    file_format = "json" if filename.lower().endswith(".json") else "csv"
    request_headers = {
        "Content-Type": "application/json" if file_format == "json" else "text/csv",
        **(headers or {}),
    }
    return client.post(
        f"/api/v1/runs/{run_id}/calibrations",
        params={**form, "format": file_format},
        content=payload,
        headers=request_headers,
    )


@pytest.mark.integration
def test_calibration_openapi_documents_the_bounded_raw_body(
    calibration_api: tuple[TestClient, AppContainer],
) -> None:
    client, _container = calibration_api
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/runs/{run_id}/calibrations"][
        "post"
    ]
    request_body = operation["requestBody"]

    assert request_body["required"] is True
    assert "2 MiB" in request_body["description"]
    assert set(request_body["content"]) == {"application/json", "text/csv"}
    assert all(
        media["schema"] == {"type": "string", "format": "binary"}
        for media in request_body["content"].values()
    )


@pytest.mark.integration
def test_calibration_api_is_private_immutable_and_idempotent(
    calibration_api: tuple[TestClient, AppContainer],
) -> None:
    client, container = calibration_api
    run = _completed_run(client)
    run_id = run["id"]
    candidate_id = run["candidates"][0]["id"]
    reviewer_canary = "reviewer-private-canary"
    upload, manifest = _filled_json_template(
        client,
        run_id=run_id,
        candidate_id=candidate_id,
        reviewer_id=reviewer_canary,
    )
    form = {
        "candidate_id": candidate_id,
        "metric_name": "correctness",
        "selected_threshold": "0.7",
    }

    created = _post_calibration(
        client,
        run_id=run_id,
        form=form,
        payload=upload,
        headers={"X-Request-ID": "calibration-import-created"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "created"
    report = created.json()["report"]
    assert report["run_id"] == run_id
    assert report["candidate_id"] == candidate_id
    assert report["metric"]["name"] == "correctness"
    assert report["selected_threshold"] == 0.7
    assert report["evidence_kind"] == "offline_statistical_evidence"
    assert report["production_validated"] is False
    assert report["sample_size"] == len(manifest["labels"])
    serialized_report = json.dumps(report, sort_keys=True)
    assert reviewer_canary not in serialized_report
    assert "human_passed" not in serialized_report
    assert "labels" not in serialized_report

    replay = _post_calibration(
        client,
        run_id=run_id,
        form=form,
        payload=upload,
        headers={"X-Request-ID": "calibration-import-replay"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "already_exists"
    assert replay.json()["report"]["id"] == report["id"]

    history = client.get(f"/api/v1/runs/{run_id}/calibrations")
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["page"] == 1
    assert history.json()["limit"] == 50
    assert history.json()["items"] == [report]
    scoped_history = client.get(
        f"/api/v1/runs/{run_id}/calibrations",
        params={"candidate_id": candidate_id, "metric_name": "correctness", "limit": 1},
    )
    assert scoped_history.status_code == 200, scoped_history.text
    assert scoped_history.json()["total"] == 1
    assert scoped_history.json()["items"] == [report]
    empty_scope = client.get(
        f"/api/v1/runs/{run_id}/calibrations",
        params={"candidate_id": candidate_id, "metric_name": "relevance"},
    )
    assert empty_scope.status_code == 200, empty_scope.text
    assert empty_scope.json()["total"] == 0
    assert empty_scope.json()["items"] == []
    detail = client.get(f"/api/v1/runs/{run_id}/calibrations/{report['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json() == report

    with container.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("calibration.%"))
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    assert {event.action for event in events} == {
        "calibration.import",
        "calibration.template_export",
    }
    assert {event.request_id for event in events if event.action == "calibration.import"} == {
        "calibration-import-created",
        "calibration-import-replay",
    }
    for event in events:
        assert set(event.metadata_json) <= {
            "label_manifest_sha256",
            "report_sha256",
            "reviewer_count",
            "sample_size",
        }
    serialized_audit = json.dumps([event.metadata_json for event in events], sort_keys=True)
    assert reviewer_canary not in serialized_audit
    assert "human_passed" not in serialized_audit

    with container.session_factory() as session:
        session.execute(
            update(CalibrationReport)
            .where(CalibrationReport.id == report["id"])
            .values(report_sha256="f" * 64)
        )
        session.commit()
    corrupted = client.get(f"/api/v1/runs/{run_id}/calibrations/{report['id']}")
    assert corrupted.status_code == 500
    assert corrupted.json()["error"]["code"] == "internal_error"
    assert "hash" not in corrupted.text.casefold()


@pytest.mark.integration
def test_calibration_api_rejects_unsafe_or_mismatched_uploads(
    calibration_api: tuple[TestClient, AppContainer],
) -> None:
    client, _container = calibration_api
    run = _completed_run(client)
    run_id = run["id"]
    candidate_id = run["candidates"][0]["id"]
    _upload, manifest = _filled_json_template(
        client,
        run_id=run_id,
        candidate_id=candidate_id,
        reviewer_id="reviewer-mismatch",
    )
    form = {
        "candidate_id": candidate_id,
        "metric_name": "correctness",
        "selected_threshold": "0.7",
    }

    original_score = float(manifest["labels"][0]["score"])
    manifest["labels"][0]["score"] = 0.0 if original_score != 0.0 else 1.0
    mismatched = _post_calibration(
        client,
        run_id=run_id,
        form=form,
        payload=json.dumps(manifest, separators=(",", ":")),
    )
    assert mismatched.status_code == 422, mismatched.text
    assert mismatched.json()["error"]["code"] == "calibration_validation_failed"
    assert "reviewer-mismatch" not in mismatched.text

    malformed = _post_calibration(
        client,
        run_id=run_id,
        form=form,
        payload=b"{not-json",
    )
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["error"]["code"] == "calibration_validation_failed"

    unsafe_simple_request = client.post(
        f"/api/v1/runs/{run_id}/calibrations",
        params={**form, "format": "json"},
        content=b"{}",
        headers={"Content-Type": "text/plain"},
    )
    assert unsafe_simple_request.status_code == 415
    assert unsafe_simple_request.json()["error"]["code"] == "unsupported_media_type"

    oversized = _post_calibration(
        client,
        run_id=run_id,
        form=form,
        payload=b"x" * (MAX_CALIBRATION_FILE_BYTES + 1),
    )
    assert oversized.status_code == 413, oversized.text
    assert oversized.json()["error"]["code"] == "limit_exceeded"

    missing = client.get(f"/api/v1/runs/{run_id}/calibrations/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
