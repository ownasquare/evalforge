"""Run-scoped human-calibration template and immutable report routes."""

from __future__ import annotations

from datetime import UTC
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response, status

from evalforge.api.dependencies import EditorWorkspaceDep, SessionDep, ViewerWorkspaceDep
from evalforge.audit import AuditRecorder
from evalforge.errors import EvalForgeError, LimitError
from evalforge.evaluation.calibration_io import MAX_CALIBRATION_FILE_BYTES, CalibrationInputError
from evalforge.evaluation.calibration_service import CalibrationService
from evalforge.models import (
    CalibrationReport,
    CalibrationReportIntegrityError,
    validate_calibration_report_integrity,
)
from evalforge.observability import current_request_id
from evalforge.schemas import (
    CalibrationImportRead,
    CalibrationReportPage,
    CalibrationReportRead,
)

router = APIRouter(tags=["calibrations"])


@router.get("/runs/{run_id}/calibrations/template")
def download_calibration_template(
    run_id: str,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    candidate_id: Annotated[str, Query(min_length=36, max_length=36)],
    metric_name: Annotated[str, Query(min_length=1, max_length=128)],
    file_format: Annotated[Literal["json", "csv"], Query(alias="format")] = "csv",
) -> Response:
    service = CalibrationService(session, workspace)
    try:
        template = service.render_template(
            run_id,
            candidate_id=candidate_id,
            metric_name=metric_name,
            file_format=file_format,
        )
    except CalibrationInputError as exc:
        raise _safe_calibration_error() from exc
    AuditRecorder(session).record(
        workspace,
        action="calibration.template_export",
        resource_type="evaluation_run",
        resource_id=run_id,
        outcome="success",
        request_id=current_request_id(),
        metadata={"sample_size": template.sample_size},
    )
    session.commit()
    return Response(
        content=template.content,
        media_type=template.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{template.filename}"',
            "X-EvalForge-Sample-Size": str(template.sample_size),
        },
    )


@router.post(
    "/runs/{run_id}/calibrations",
    response_model=CalibrationImportRead,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": (
                "A CSV or JSON calibration-label manifest streamed into at most 2 MiB of "
                "memory and never parsed as multipart form data."
            ),
            "content": {
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
                "application/json": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def import_calibration_report(
    run_id: str,
    request: Request,
    response: Response,
    session: SessionDep,
    workspace: EditorWorkspaceDep,
    candidate_id: Annotated[str, Query(min_length=36, max_length=36)],
    metric_name: Annotated[str, Query(min_length=1, max_length=128)],
    selected_threshold: Annotated[float, Query(ge=0, le=1)],
    file_format: Annotated[Literal["json", "csv"], Query(alias="format")],
) -> dict[str, Any]:
    _validate_calibration_content_type(request, file_format=file_format)
    payload = await _bounded_calibration_body(request)
    service = CalibrationService(session, workspace)
    try:
        result = service.import_report(
            run_id,
            candidate_id=candidate_id,
            metric_name=metric_name,
            selected_threshold=selected_threshold,
            payload=payload,
            filename=f"labels.{file_format}",
        )
    except CalibrationInputError as exc:
        raise _safe_calibration_error() from exc

    report = _report_read(result.report)
    AuditRecorder(session).record(
        workspace,
        action="calibration.import",
        resource_type="calibration_report",
        resource_id=result.report.id,
        outcome="success",
        request_id=current_request_id(),
        metadata={
            "label_manifest_sha256": result.report.manifest_sha256,
            "report_sha256": result.report.report_sha256,
            "reviewer_count": result.report.reviewer_count,
            "sample_size": result.report.sample_size,
        },
    )
    session.commit()
    response.status_code = (
        status.HTTP_201_CREATED if result.status == "created" else status.HTTP_200_OK
    )
    return {"status": result.status, "report": report.model_dump(mode="json")}


@router.get("/runs/{run_id}/calibrations", response_model=CalibrationReportPage)
def list_calibration_reports(
    run_id: str,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    candidate_id: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    metric_name: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> dict[str, Any]:
    reports, total = CalibrationService(session, workspace).list_reports(
        run_id,
        page=page,
        limit=limit,
        candidate_id=candidate_id,
        metric_name=metric_name,
    )
    return {
        "items": [_report_read(report).model_dump(mode="json") for report in reports],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get(
    "/runs/{run_id}/calibrations/{report_id}",
    response_model=CalibrationReportRead,
)
def get_calibration_report(
    run_id: str,
    report_id: str,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
) -> dict[str, Any]:
    report = CalibrationService(session, workspace).get_report(run_id, report_id)
    return _report_read(report).model_dump(mode="json")


def _safe_calibration_error() -> EvalForgeError:
    return EvalForgeError(
        "calibration_validation_failed",
        "Calibration labels are invalid or do not match the selected immutable run evidence.",
        status_code=422,
    )


async def _bounded_calibration_body(request: Request) -> bytes:
    """Read reviewer evidence directly into bounded memory without multipart temp files."""

    declared_length = request.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > MAX_CALIBRATION_FILE_BYTES:
        raise LimitError("Calibration imports may not exceed 2 MiB.", status_code=413)
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_CALIBRATION_FILE_BYTES:
            raise LimitError("Calibration imports may not exceed 2 MiB.", status_code=413)
        payload.extend(chunk)
    return bytes(payload)


def _validate_calibration_content_type(
    request: Request,
    *,
    file_format: Literal["json", "csv"],
) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    expected = {"application/json"} if file_format == "json" else {"text/csv", "application/csv"}
    if media_type not in expected:
        raise EvalForgeError(
            "unsupported_media_type",
            "Calibration content type does not match the selected CSV or JSON format.",
            status_code=415,
        )


def _report_read(report: CalibrationReport) -> CalibrationReportRead:
    try:
        validate_calibration_report_integrity(report)
    except CalibrationReportIntegrityError:
        raise RuntimeError("persisted calibration report failed integrity validation") from None
    payload = report.report_payload
    dataset = payload.get("dataset") if isinstance(payload, dict) else None
    confusion_matrix = payload.get("confusion_matrix") if isinstance(payload, dict) else None
    if (
        not isinstance(dataset, dict)
        or not isinstance(confusion_matrix, dict)
        or payload.get("label_manifest_sha256") != report.manifest_sha256
    ):
        raise RuntimeError("persisted calibration report failed integrity validation")
    created_at = report.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    return CalibrationReportRead.model_validate(
        {
            "id": report.id,
            "run_id": report.run_id,
            "candidate_id": report.run_candidate_id,
            "dataset": dataset,
            "metric": {
                "name": report.metric_name,
                "version": report.metric_version,
                "direction": report.metric_direction,
            },
            "selected_threshold": report.selected_threshold,
            "label_manifest_sha256": report.manifest_sha256,
            "report_sha256": report.report_sha256,
            "evidence_kind": report.evidence_kind,
            "production_validated": report.production_validated,
            "sample_size": report.sample_size,
            "human_pass_count": report.human_pass_count,
            "human_fail_count": report.human_fail_count,
            "reviewer_count": report.reviewer_count,
            "precision": report.precision,
            "recall": report.recall,
            "f1": report.f1,
            "confusion_matrix": confusion_matrix,
            "created_at": created_at,
        }
    )
