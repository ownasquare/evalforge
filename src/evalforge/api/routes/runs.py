"""Run preflight, submission, history, cancellation, result, and export routes."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from evalforge.api.dependencies import (
    ContainerDep,
    EditorWorkspaceDep,
    EvaluationServiceDep,
    SessionDep,
    ViewerWorkspaceDep,
)
from evalforge.audit import AuditRecorder
from evalforge.errors import NotFoundError
from evalforge.exports import DisclosureProfile, build_export_package, disclose_run_evidence
from evalforge.models import RunStatus
from evalforge.observability import current_request_id
from evalforge.repositories import EvaluationRunRepository
from evalforge.repositories import NotFoundError as RepositoryNotFoundError
from evalforge.schemas import (
    EvaluationResultRead,
    EvaluationRunApiDetail,
    EvaluationRunCreate,
    EvaluationRunDetail,
    EvaluationRunPreflightRead,
    EvaluationRunSummary,
    Page,
)

router = APIRouter(tags=["runs"])


@router.post("/runs/preflight", response_model=EvaluationRunPreflightRead)
def preflight_run(
    data: EvaluationRunCreate,
    service: EvaluationServiceDep,
    workspace: EditorWorkspaceDep,
) -> dict[str, Any]:
    return service.preflight(data, workspace)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=EvaluationRunSummary)
async def create_run(
    data: EvaluationRunCreate,
    response: Response,
    service: EvaluationServiceDep,
    container: ContainerDep,
    workspace: EditorWorkspaceDep,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> dict[str, Any]:
    if idempotency_key:
        data = data.model_copy(update={"idempotency_key": idempotency_key})
    run = service.create_run(data, workspace, request_id=current_request_id())
    await container.executor.submit(run.id)
    response.headers["Location"] = f"/api/v1/runs/{run.id}"
    return EvaluationRunSummary.model_validate(run).model_dump(mode="json")


@router.get("/runs", response_model=Page[EvaluationRunSummary])
def list_runs(
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    rows, total = EvaluationRunRepository(session, workspace).list(
        page=page, limit=limit, status=run_status
    )
    return {
        "items": [EvaluationRunSummary.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/runs/{run_id}", response_model=EvaluationRunApiDetail)
def get_run(run_id: str, session: SessionDep, workspace: ViewerWorkspaceDep) -> dict[str, Any]:
    run = EvaluationRunRepository(session, workspace).get(run_id, with_candidates=True)
    return EvaluationRunApiDetail.model_validate(run).model_dump(mode="json")


@router.post("/runs/{run_id}/cancel", response_model=EvaluationRunSummary)
def cancel_run(
    run_id: str,
    service: EvaluationServiceDep,
    workspace: EditorWorkspaceDep,
) -> dict[str, Any]:
    run = service.cancel_run(run_id, workspace, request_id=current_request_id())
    return EvaluationRunSummary.model_validate(run).model_dump(mode="json")


@router.get("/runs/{run_id}/results", response_model=Page[EvaluationResultRead])
def list_run_results(
    run_id: str,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    rows, total = EvaluationRunRepository(session, workspace).list_results(
        run_id, page=page, limit=limit
    )
    return {
        "items": [EvaluationResultRead.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/runs/{run_id}/export")
def export_run(
    run_id: str,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    format: str = Query(default="json", pattern="^(json|csv|package)$"),
    disclosure_profile: Annotated[DisclosureProfile, Query()] = (
        DisclosureProfile.CONTENT_REDACTED
    ),
) -> Response:
    try:
        run = EvaluationRunRepository(session, workspace).get(run_id, with_detail=True)
    except RepositoryNotFoundError as exc:
        raise NotFoundError("Evaluation run") from exc
    evidence = EvaluationRunDetail.model_validate(run).model_dump(mode="json")
    disclosed_evidence = disclose_run_evidence(evidence, disclosure_profile)
    package = None
    if format == "package":
        package = build_export_package(
            evidence,
            application_version=run.application_version,
            metric_versions=_metric_versions(run),
            disclosure_profile=disclosure_profile,
        )
    audit_metadata: dict[str, Any] = {
        "format": format,
        "disclosure_profile": disclosure_profile.value,
    }
    if package is not None:
        audit_metadata["package_sha256"] = package.payload_sha256
    AuditRecorder(session).record(
        workspace,
        action="run.export",
        resource_type="evaluation_run",
        resource_id=run.id,
        outcome="success",
        request_id=current_request_id(),
        metadata=audit_metadata,
    )
    session.commit()
    if package is not None:
        return Response(
            package.envelope_bytes,
            media_type="application/vnd.evalforge.run-export+json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="run-{run.id}-{disclosure_profile.value}.json"'
                ),
                "X-EvalForge-Payload-SHA256": package.payload_sha256,
            },
        )
    if format == "json":
        return Response(
            json.dumps(disclosed_evidence, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="run-{run.id}-{disclosure_profile.value}.json"'
                )
            },
        )
    buffer = StringIO()
    columns = [
        "candidate_id",
        "test_case_id",
        "status",
        "input",
        "expected_output",
        "output",
        "aggregate_quality",
        "latency_ms",
        "total_tokens",
        "cost_micro_usd",
        "cost_source",
        "error_type",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    raw_results = disclosed_evidence.get("results", [])
    if not isinstance(raw_results, list):
        raise RuntimeError("run export evidence contains invalid result rows")
    rows = [item for item in raw_results if isinstance(item, dict)]
    for result in sorted(
        rows,
        key=lambda item: (
            str(item.get("run_candidate_id", "")),
            str(item.get("test_case_id", "")),
        ),
    ):
        input_snapshot = result.get("input_snapshot", {})
        if not isinstance(input_snapshot, dict):
            input_snapshot = {}
        writer.writerow(
            {
                "candidate_id": result.get("run_candidate_id", ""),
                "test_case_id": result.get("test_case_id", ""),
                "status": result.get("status", ""),
                "input": _csv_safe(str(input_snapshot.get("input", ""))),
                "expected_output": _csv_safe(str(input_snapshot.get("expected_output", ""))),
                "output": _csv_safe(str(result.get("output_text") or "")),
                "aggregate_quality": result.get("aggregate_score"),
                "latency_ms": result.get("latency_ms"),
                "total_tokens": result.get("total_tokens"),
                "cost_micro_usd": result.get("estimated_cost_micro_usd"),
                "cost_source": result.get("cost_source"),
                "error_type": result.get("error_type"),
            }
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="run-{run.id}-{disclosure_profile.value}.csv"'
            )
        },
    )


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def _metric_versions(run: Any) -> dict[str, str]:
    """Read the immutable run-level metric versions used by every result row."""

    snapshot = run.metric_configuration_snapshot
    raw_versions = snapshot.get("versions") if isinstance(snapshot, dict) else None
    if isinstance(raw_versions, dict):
        snapshot_versions = {
            str(name): str(version)
            for name, version in raw_versions.items()
            if str(name).strip() and str(version).strip()
        }
        if snapshot_versions:
            return snapshot_versions

    versions: dict[str, str] = {}
    for result in run.results:
        for name, version in result.metric_versions.items():
            normalized_name = str(name)
            normalized_version = str(version)
            existing = versions.get(normalized_name)
            if existing is not None and existing != normalized_version:
                raise RuntimeError("run evidence contains inconsistent metric versions")
            versions[normalized_name] = normalized_version
    return versions
