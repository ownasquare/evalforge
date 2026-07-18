"""Run preflight, submission, history, cancellation, result, and export routes."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from evalforge.api.dependencies import ContainerDep, EvaluationServiceDep, SessionDep
from evalforge.errors import NotFoundError
from evalforge.models import RunStatus
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
def preflight_run(data: EvaluationRunCreate, service: EvaluationServiceDep) -> dict[str, Any]:
    return service.preflight(data)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=EvaluationRunSummary)
async def create_run(
    data: EvaluationRunCreate,
    response: Response,
    service: EvaluationServiceDep,
    container: ContainerDep,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> dict[str, Any]:
    if idempotency_key:
        data = data.model_copy(update={"idempotency_key": idempotency_key})
    run = service.create_run(data)
    await container.executor.submit(run.id)
    response.headers["Location"] = f"/api/v1/runs/{run.id}"
    return EvaluationRunSummary.model_validate(run).model_dump(mode="json")


@router.get("/runs", response_model=Page[EvaluationRunSummary])
def list_runs(
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    rows, total = EvaluationRunRepository(session).list(page=page, limit=limit, status=run_status)
    return {
        "items": [EvaluationRunSummary.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/runs/{run_id}", response_model=EvaluationRunApiDetail)
def get_run(run_id: str, session: SessionDep) -> dict[str, Any]:
    run = EvaluationRunRepository(session).get(run_id, with_candidates=True)
    return EvaluationRunApiDetail.model_validate(run).model_dump(mode="json")


@router.post("/runs/{run_id}/cancel", response_model=EvaluationRunSummary)
def cancel_run(run_id: str, service: EvaluationServiceDep) -> dict[str, Any]:
    run = service.cancel_run(run_id)
    return EvaluationRunSummary.model_validate(run).model_dump(mode="json")


@router.get("/runs/{run_id}/results", response_model=Page[EvaluationResultRead])
def list_run_results(
    run_id: str,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    rows, total = EvaluationRunRepository(session).list_results(run_id, page=page, limit=limit)
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
    format: str = Query(default="json", pattern="^(json|csv)$"),
) -> Response:
    try:
        run = EvaluationRunRepository(session).get(run_id, with_detail=True)
    except RepositoryNotFoundError as exc:
        raise NotFoundError("Evaluation run") from exc
    if format == "json":
        payload = EvaluationRunDetail.model_validate(run).model_dump(mode="json")
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="run-{run.id}.json"'},
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
    for result in sorted(run.results, key=lambda item: (item.run_candidate_id, item.test_case_id)):
        writer.writerow(
            {
                "candidate_id": result.run_candidate_id,
                "test_case_id": result.test_case_id,
                "status": result.status.value,
                "input": _csv_safe(str(result.input_snapshot.get("input", ""))),
                "expected_output": _csv_safe(str(result.input_snapshot.get("expected_output", ""))),
                "output": _csv_safe(result.output_text or ""),
                "aggregate_quality": result.aggregate_score,
                "latency_ms": result.latency_ms,
                "total_tokens": result.total_tokens,
                "cost_micro_usd": result.estimated_cost_micro_usd,
                "cost_source": result.cost_source,
                "error_type": result.error_type,
            }
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="run-{run.id}.csv"'},
    )


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
