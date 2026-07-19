"""Run-bound calibration templates and immutable aggregate report persistence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalforge.evaluation.calibration_io import (
    CalibrationInputError,
    CalibrationTemplateRow,
    DatasetIdentity,
    MetricIdentity,
    build_calibration_report,
    formula_safe_case_external_id,
    load_calibration_manifest_bytes,
    render_calibration_template,
)
from evalforge.models import (
    CalibrationReport,
    EvaluationResult,
    EvaluationRun,
    ResultStatus,
    RunCandidate,
)
from evalforge.repositories import CalibrationReportRepository, EvaluationRunRepository
from evalforge.security.permissions import WorkspaceContext


@dataclass(frozen=True, slots=True)
class CalibrationTemplateFile:
    """Downloadable server-derived template with no reviewer decisions."""

    content: bytes
    filename: str
    media_type: str
    sample_size: int


@dataclass(frozen=True, slots=True)
class CalibrationImportResult:
    """Created or idempotently replayed immutable calibration evidence."""

    report: CalibrationReport
    status: Literal["created", "already_exists"]


@dataclass(frozen=True, slots=True)
class _CalibrationScope:
    run: EvaluationRun
    candidate: RunCandidate
    dataset: DatasetIdentity
    metric: MetricIdentity
    rows: tuple[CalibrationTemplateRow, ...]

    @property
    def rows_by_result_id(self) -> dict[str, CalibrationTemplateRow]:
        return {row.item_id: row for row in self.rows}


class CalibrationService:
    """Validate private reviewer input against immutable evaluation evidence."""

    def __init__(self, session: Session, context: WorkspaceContext) -> None:
        self.session = session
        self.context = context
        self.runs = EvaluationRunRepository(session, context)
        self.reports = CalibrationReportRepository(session, context)

    def render_template(
        self,
        run_id: str,
        *,
        candidate_id: str,
        metric_name: str,
        file_format: Literal["json", "csv"] = "csv",
    ) -> CalibrationTemplateFile:
        scope = self._load_scope(run_id, candidate_id=candidate_id, metric_name=metric_name)
        content = render_calibration_template(
            dataset=scope.dataset,
            metric=scope.metric,
            rows=scope.rows,
            file_format=file_format,
        )
        extension = "json" if file_format == "json" else "csv"
        media_type = "application/json" if file_format == "json" else "text/csv"
        return CalibrationTemplateFile(
            content=content,
            filename=(
                f"evalforge-calibration-{scope.run.id}-{scope.candidate.id}-"
                f"{scope.metric.name}.{extension}"
            ),
            media_type=media_type,
            sample_size=len(scope.rows),
        )

    def import_report(
        self,
        run_id: str,
        *,
        candidate_id: str,
        metric_name: str,
        selected_threshold: float,
        payload: bytes,
        filename: str,
    ) -> CalibrationImportResult:
        threshold = _selected_threshold(selected_threshold)
        scope = self._load_scope(run_id, candidate_id=candidate_id, metric_name=metric_name)
        manifest = load_calibration_manifest_bytes(payload, filename=filename)
        if manifest.dataset != scope.dataset or manifest.metric != scope.metric:
            raise CalibrationInputError(
                "calibration manifest identity does not match stored run evidence"
            )

        stored_rows = scope.rows_by_result_id
        for label in manifest.labels:
            stored = stored_rows.get(label.item_id)
            if (
                stored is None
                or label.score != stored.score
                or label.case_position != stored.case_position
                or label.case_external_id
                not in {
                    stored.case_external_id,
                    formula_safe_case_external_id(stored.case_external_id or ""),
                }
            ):
                raise CalibrationInputError(
                    "calibration manifest results do not match stored run evidence"
                )

        try:
            package = build_calibration_report(manifest, selected_threshold=threshold)
        except ValueError:
            raise CalibrationInputError("selected threshold must be between 0 and 1") from None
        report_payload = package.payload
        report = CalibrationReport(
            workspace_id=self.context.workspace_id,
            run_id=scope.run.id,
            run_candidate_id=scope.candidate.id,
            metric_name=scope.metric.name,
            metric_version=scope.metric.version,
            metric_direction=scope.metric.direction.value,
            selected_threshold=threshold,
            manifest_sha256=package.label_manifest_sha256,
            report_sha256=package.payload_sha256,
            schema_version=str(report_payload["schema_version"]),
            evidence_kind=str(report_payload["evidence_kind"]),
            production_validated=False,
            sample_size=int(report_payload["sample_size"]),
            human_pass_count=int(report_payload["human_pass_count"]),
            human_fail_count=int(report_payload["human_fail_count"]),
            reviewer_count=int(report_payload["reviewer_count"]),
            precision=float(report_payload["precision"]),
            recall=float(report_payload["recall"]),
            f1=float(report_payload["f1"]),
            report_payload=report_payload,
            created_by_user_id=self.context.user_id,
        )
        persisted, created = self.reports.create_idempotent(report)
        return CalibrationImportResult(
            report=persisted,
            status="created" if created else "already_exists",
        )

    def list_reports(
        self,
        run_id: str,
        *,
        page: int = 1,
        limit: int = 50,
        candidate_id: str | None = None,
        metric_name: str | None = None,
    ) -> tuple[list[CalibrationReport], int]:
        return self.reports.list(
            run_id,
            page=page,
            limit=limit,
            candidate_id=candidate_id,
            metric_name=metric_name,
        )

    def get_report(self, run_id: str, report_id: str) -> CalibrationReport:
        return self.reports.get(run_id, report_id)

    def _load_scope(
        self,
        run_id: str,
        *,
        candidate_id: str,
        metric_name: str,
    ) -> _CalibrationScope:
        run = self.runs.get(run_id)
        if not run.status.is_terminal:
            raise CalibrationInputError("calibration requires a terminal evaluation run")
        candidate = self.runs.get_candidate(candidate_id)
        if candidate.run_id != run.id or not candidate.status.is_terminal:
            raise CalibrationInputError(
                "calibration candidate does not match the terminal evaluation run"
            )

        dataset = _dataset_identity(run)
        metric = _metric_identity(run, metric_name)
        results = self.session.scalars(
            select(EvaluationResult)
            .where(
                EvaluationResult.workspace_id == self.context.workspace_id,
                EvaluationResult.run_id == run.id,
                EvaluationResult.run_candidate_id == candidate.id,
                EvaluationResult.status == ResultStatus.COMPLETED,
            )
            .order_by(EvaluationResult.id)
        ).all()
        rows: list[CalibrationTemplateRow] = []
        for result in results:
            applicability = result.metric_applicability.get(metric.name)
            if applicability != "applicable":
                continue
            if (
                result.metric_versions.get(metric.name) != metric.version
                or result.metric_directions.get(metric.name) != metric.direction.value
            ):
                raise CalibrationInputError(
                    "stored result metric identity does not match the run configuration"
                )
            metric_result = result.metric_results.get(metric.name)
            score = metric_result.get("score") if isinstance(metric_result, dict) else None
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0.0 <= float(score) <= 1.0
            ):
                raise CalibrationInputError("stored applicable metric result is invalid")
            case_position = result.input_snapshot.get("position")
            case_external_id = result.input_snapshot.get("external_id")
            if (
                isinstance(case_position, bool)
                or not isinstance(case_position, int)
                or case_position < 0
                or not isinstance(case_external_id, str)
                or not case_external_id
                or len(case_external_id) > 200
            ):
                raise CalibrationInputError("stored result case review identity is invalid")
            rows.append(
                CalibrationTemplateRow(
                    item_id=result.id,
                    case_position=case_position,
                    case_external_id=case_external_id,
                    score=float(score),
                )
            )
        if not rows:
            raise CalibrationInputError(
                "calibration requires at least one completed applicable metric result"
            )
        return _CalibrationScope(
            run=run,
            candidate=candidate,
            dataset=dataset,
            metric=metric,
            rows=tuple(rows),
        )


def _dataset_identity(run: EvaluationRun) -> DatasetIdentity:
    version = run.dataset_snapshot.get("version")
    try:
        return DatasetIdentity(
            id=run.dataset_id,
            version=str(version),
            sha256=run.dataset_hash,
        )
    except ValueError:
        raise CalibrationInputError("stored run dataset identity is invalid") from None


def _metric_identity(run: EvaluationRun, metric_name: str) -> MetricIdentity:
    snapshot = run.metric_configuration_snapshot
    versions = snapshot.get("versions")
    directions = snapshot.get("directions")
    version = versions.get(metric_name) if isinstance(versions, dict) else None
    direction = directions.get(metric_name) if isinstance(directions, dict) else None
    configured_rows = snapshot.get("metrics")
    if isinstance(configured_rows, list):
        for configured in configured_rows:
            if isinstance(configured, dict) and configured.get("name") == metric_name:
                if configured.get("enabled", True) is False:
                    break
                version = configured.get("version", version)
                direction = configured.get("direction", direction)
                break
    if not isinstance(version, str) or not isinstance(direction, str):
        raise CalibrationInputError("metric is not configured for this evaluation run")
    try:
        return MetricIdentity(name=metric_name, version=version, direction=direction)
    except ValueError:
        raise CalibrationInputError("stored run metric identity is invalid") from None


def _selected_threshold(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationInputError("selected threshold must be between 0 and 1")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise CalibrationInputError("selected threshold must be between 0 and 1")
    return 0.0 if normalized == 0.0 else normalized
