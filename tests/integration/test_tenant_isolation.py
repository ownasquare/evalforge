from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from evalforge.models import (
    CalibrationReport,
    User,
    Workspace,
    WorkspaceMembership,
    canonical_json_hash,
)
from evalforge.repositories import (
    CalibrationReportRepository,
    DatasetRepository,
    EvaluationRunRepository,
    ModelProfileRepository,
    NotFoundError,
    PromptTemplateRepository,
)
from evalforge.schemas import (
    DatasetCreate,
    EvaluationRunCreate,
    ModelProfileCreate,
    PromptTemplateCreate,
)
from evalforge.schemas import (
    TestCaseCreate as CaseCreateSchema,
)
from evalforge.security.permissions import WorkspaceContext, WorkspaceRole


def _workspace_context(session: Session, suffix: str) -> WorkspaceContext:
    workspace = Workspace(slug=f"workspace-{suffix}", name=f"Workspace {suffix}")
    user = User(
        issuer="https://identity.example.test",
        subject=f"subject-{suffix}",
        display_name=f"Owner {suffix}",
    )
    membership = WorkspaceMembership(
        workspace=workspace,
        user=user,
        role=WorkspaceRole.OWNER,
    )
    session.add_all([workspace, user, membership])
    session.flush()
    return WorkspaceContext(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER,
        workspace_name=workspace.name,
        display_name=user.display_name or "Owner",
    )


def _create_matrix(session: Session, context: WorkspaceContext, *, key: str) -> str:
    dataset = DatasetRepository(session, context).create(
        DatasetCreate(
            name="Shared benchmark name",
            cases=[CaseCreateSchema(external_id="case-1", input_text="Say hello")],
        )
    )
    prompt = PromptTemplateRepository(session, context).create(
        PromptTemplateCreate(name="Shared prompt name", user_template="{input}")
    )
    model = ModelProfileRepository(session, context).create(
        ModelProfileCreate(
            name="Shared model name",
            provider="deterministic",
            model_name="balanced",
            api_mode="deterministic",
            input_price_micro_usd_per_million_tokens=0,
            output_price_micro_usd_per_million_tokens=0,
            pricing_source="deterministic",
        )
    )
    run = EvaluationRunRepository(session, context).create(
        EvaluationRunCreate(
            dataset_id=UUID(dataset.id),
            prompt_ids=[UUID(prompt.id)],
            model_ids=[UUID(model.id)],
            idempotency_key=key,
        ),
        application_version="test",
        requested_by_user_id=context.user_id,
        requested_by=context.display_name,
    )
    return run.id


def _create_calibration_report(
    session: Session,
    context: WorkspaceContext,
    *,
    run_id: str,
) -> CalibrationReport:
    run = EvaluationRunRepository(session, context).get(run_id, with_candidates=True)
    candidate = run.candidates[0]
    payload = {
        "calibration_set_sha256": "c" * 64,
        "confusion_matrix": {
            "false_negative": 0,
            "false_positive": 0,
            "true_negative": 0,
            "true_positive": 1,
        },
        "dataset": {
            "id": run.dataset_id,
            "version": str(run.dataset_snapshot.get("version", 1)),
            "sha256": run.dataset_hash,
        },
        "evidence_kind": "offline_statistical_evidence",
        "f1": 1.0,
        "human_fail_count": 0,
        "human_pass_count": 1,
        "label_manifest_sha256": "a" * 64,
        "metric": {
            "name": "correctness",
            "version": "1.0.0",
            "direction": "higher_is_better",
        },
        "precision": 1.0,
        "production_validated": False,
        "recall": 1.0,
        "reviewer_count": 1,
        "sample_size": 1,
        "schema_version": "evalforge.calibration-report.v1",
        "selected_threshold": 0.7,
    }
    report = CalibrationReport(
        workspace_id=context.workspace_id,
        run_id=run.id,
        run_candidate_id=candidate.id,
        metric_name="correctness",
        metric_version="1.0.0",
        metric_direction="higher_is_better",
        selected_threshold=0.7,
        manifest_sha256="a" * 64,
        report_sha256=canonical_json_hash(payload),
        schema_version="evalforge.calibration-report.v1",
        evidence_kind="offline_statistical_evidence",
        production_validated=False,
        sample_size=1,
        human_pass_count=1,
        human_fail_count=0,
        reviewer_count=1,
        precision=1.0,
        recall=1.0,
        f1=1.0,
        report_payload=payload,
        created_by_user_id=context.user_id,
    )
    created, was_created = CalibrationReportRepository(session, context).create_idempotent(report)
    assert was_created is True
    return created


@pytest.mark.integration
def test_names_and_idempotency_are_workspace_local(session: Session) -> None:
    first = _workspace_context(session, "a")
    second = _workspace_context(session, "b")

    first_run_id = _create_matrix(session, first, key="same-request")
    second_run_id = _create_matrix(session, second, key="same-request")
    session.commit()

    assert first_run_id != second_run_id
    assert DatasetRepository(session, first).list()[1] == 1
    assert DatasetRepository(session, second).list()[1] == 1
    assert EvaluationRunRepository(session, first).find_by_idempotency_key("same-request")
    assert EvaluationRunRepository(session, second).find_by_idempotency_key("same-request")


@pytest.mark.integration
def test_cross_workspace_ids_are_indistinguishable_from_missing(session: Session) -> None:
    first = _workspace_context(session, "a")
    second = _workspace_context(session, "b")
    first_run_id = _create_matrix(session, first, key="first-request")
    first_dataset = DatasetRepository(session, first).list()[0][0]
    session.commit()

    with pytest.raises(NotFoundError):
        DatasetRepository(session, second).get(first_dataset.id)
    with pytest.raises(NotFoundError):
        EvaluationRunRepository(session, second).get(first_run_id)


@pytest.mark.integration
def test_calibration_reports_cannot_be_enumerated_across_workspaces(session: Session) -> None:
    first = _workspace_context(session, "calibration-a")
    second = _workspace_context(session, "calibration-b")
    first_run_id = _create_matrix(session, first, key="calibration-first")
    report = _create_calibration_report(session, first, run_id=first_run_id)
    session.commit()

    with pytest.raises(NotFoundError):
        CalibrationReportRepository(session, second).get(first_run_id, report.id)
    with pytest.raises(NotFoundError):
        CalibrationReportRepository(session, second).list(first_run_id)


@pytest.mark.integration
def test_database_rejects_mixed_workspace_parentage(session: Session) -> None:
    first = _workspace_context(session, "a")
    second = _workspace_context(session, "b")
    dataset = DatasetRepository(session, first).create(
        DatasetCreate(
            name="First benchmark",
            cases=[CaseCreateSchema(external_id="case-1", input_text="One")],
        )
    )
    session.flush()

    # The tenant-preserving foreign key must reject a child claiming another
    # workspace while referencing this dataset.
    dataset.cases[0].workspace_id = second.workspace_id
    with pytest.raises(IntegrityError):
        session.commit()
