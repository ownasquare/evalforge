from __future__ import annotations

import csv
import io
import json
from copy import deepcopy

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from evalforge.evaluation.calibration_io import CalibrationInputError
from evalforge.evaluation.calibration_service import CalibrationService
from evalforge.models import (
    CalibrationReport,
    EvaluationResult,
    EvaluationRun,
    ResultStatus,
    RunStatus,
    canonical_json_hash,
)
from evalforge.models import TestCase as DomainTestCase
from evalforge.security.permissions import WorkspaceContext, WorkspaceRole


def _finish_run(sample_result: EvaluationResult, session: Session) -> CalibrationService:
    sample_result.run.status = RunStatus.COMPLETED
    sample_result.run.completed_items = sample_result.run.total_items
    sample_result.run.succeeded_items = sample_result.run.total_items
    sample_result.candidate.status = RunStatus.COMPLETED
    sample_result.candidate.completed_items = sample_result.candidate.total_items
    session.add(sample_result)
    session.commit()
    return CalibrationService(session, _context_for(sample_result))


def _context_for(result: EvaluationResult) -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=result.workspace_id,
        user_id=result.run.requested_by_user_id or "",
        role=WorkspaceRole.OWNER,
        workspace_name="Local workspace",
        display_name="Local owner",
    )


def _filled_json(content: bytes, *, reviewer_id: str = "reviewer-01") -> bytes:
    payload = json.loads(content)
    for index, row in enumerate(payload["labels"]):
        row["human_passed"] = index % 2 == 0
        row["reviewer_id"] = reviewer_id
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _add_second_result(session: Session, source: EvaluationResult) -> EvaluationResult:
    case_payload = {
        "external_id": "case-2",
        "position": 1,
        "input": "What is the capital of Italy?",
        "context": "Italy's capital is Rome.",
        "expected_output": "Rome",
        "required_phrases": ["Rome"],
        "constraints": {},
        "tags": ["geography"],
        "metadata": {},
    }
    case_hash = canonical_json_hash(case_payload)
    session.add(source)
    case = DomainTestCase(
        workspace_id=source.workspace_id,
        dataset=source.test_case.dataset,
        external_id="case-2",
        position=1,
        input_text=case_payload["input"],
        context_text=case_payload["context"],
        expected_output=case_payload["expected_output"],
        required_phrases=["Rome"],
        constraints_json={},
        tags=["geography"],
        metadata_json={},
        case_hash=case_hash,
    )
    session.add(case)
    session.flush()
    result = EvaluationResult(
        workspace_id=source.workspace_id,
        run=source.run,
        candidate=source.candidate,
        test_case=case,
        input_snapshot=case.snapshot(),
        case_hash=case_hash,
        prompt_snapshot=deepcopy(source.prompt_snapshot),
        prompt_hash=source.prompt_hash,
        model_snapshot=deepcopy(source.model_snapshot),
        model_hash=source.model_hash,
        generation_parameters_snapshot=deepcopy(source.generation_parameters_snapshot),
        rendered_system_prompt=source.rendered_system_prompt,
        rendered_user_prompt="What is the capital of Italy?\nItaly's capital is Rome.",
        output_text="Rome",
        metric_versions={"correctness": "1.0.0"},
        metric_directions={"correctness": "higher_is_better"},
        metric_applicability={"correctness": "applicable"},
        metric_results={"correctness": {"score": 0.25, "passed": False}},
        aggregate_score=0.25,
        aggregate_passed=False,
        effective_metric_weight=1.0,
        provider="deterministic",
        model_name="balanced",
        api_mode=source.api_mode,
        retry_count=0,
        latency_ms=1,
        input_tokens=10,
        output_tokens=1,
        total_tokens=11,
        estimated_cost_micro_usd=0,
        cost_source="deterministic",
        provider_metadata={},
        status=ResultStatus.COMPLETED,
    )
    source.run.total_items = 2
    source.run.completed_items = 2
    source.run.succeeded_items = 2
    source.candidate.total_items = 2
    source.candidate.completed_items = 2
    return result


def test_template_import_persists_only_minimized_report_and_replays_idempotently(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    service = _finish_run(sample_result, session)
    template = service.render_template(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        file_format="json",
    )
    template_payload = json.loads(template.content)

    assert template.media_type == "application/json"
    assert template.sample_size == 1
    assert template_payload["labels"] == [
        {
            "case_external_id": "case-1",
            "case_position": 0,
            "human_passed": None,
            "item_id": sample_result.id,
            "reviewer_id": "",
            "score": 1.0,
        }
    ]

    private_upload = _filled_json(template.content, reviewer_id="private-reviewer")
    created = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=0.7,
        payload=private_upload,
        filename="labels.json",
    )
    replay = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=0.7,
        payload=private_upload,
        filename="labels.json",
    )
    signed_zero = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=-0.0,
        payload=private_upload,
        filename="labels.json",
    )
    unsigned_zero = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=0.0,
        payload=private_upload,
        filename="labels.json",
    )
    session.commit()

    assert created.status == "created"
    assert replay.status == "already_exists"
    assert replay.report.id == created.report.id
    assert signed_zero.status == "created"
    assert unsigned_zero.status == "already_exists"
    assert unsigned_zero.report.id == signed_zero.report.id
    assert created.report.evidence_kind == "offline_statistical_evidence"
    assert created.report.production_validated is False
    assert created.report.sample_size == 1
    minimized = json.dumps(created.report.report_payload, sort_keys=True)
    assert sample_result.id not in minimized
    assert "private-reviewer" not in minimized
    assert "human_passed" not in minimized
    rows, total = service.list_reports(sample_result.run_id, page=1, limit=1)
    assert total == 2
    assert len(rows) == 1
    assert service.get_report(sample_result.run_id, created.report.id).id == created.report.id
    assert set(session.scalars(select(CalibrationReport)).all()) == {
        created.report,
        signed_zero.report,
    }


def test_import_accepts_a_subset_and_a_new_threshold_creates_new_evidence(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    second = _add_second_result(session, sample_result)
    session.add_all([sample_result, second])
    service = _finish_run(sample_result, session)
    template = service.render_template(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        file_format="json",
    )
    payload = json.loads(_filled_json(template.content))
    payload["labels"] = payload["labels"][:1]
    upload = json.dumps(payload).encode("utf-8")

    first = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=0.5,
        payload=upload,
        filename="labels.json",
    )
    second_threshold = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=0.6,
        payload=upload,
        filename="labels.json",
    )

    assert first.report.sample_size == 1
    assert second_threshold.report.id != first.report.id
    assert service.list_reports(sample_result.run_id)[1] == 2


def test_lower_is_better_csv_template_and_import(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    session.execute(
        update(EvaluationRun)
        .where(EvaluationRun.id == sample_result.run.id)
        .values(
            metric_configuration_snapshot={
                "versions": {"correctness": "1.0.0"},
                "directions": {"correctness": "lower_is_better"},
            }
        )
    )
    session.expire(sample_result.run, ["metric_configuration_snapshot"])
    sample_result.metric_directions = {"correctness": "lower_is_better"}
    sample_result.metric_results = {"correctness": {"score": 0.2, "passed": True}}
    service = _finish_run(sample_result, session)
    assert sample_result.run.metric_configuration_snapshot["directions"] == {
        "correctness": "lower_is_better"
    }
    assert sample_result.metric_directions == {"correctness": "lower_is_better"}
    template = service.render_template(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        file_format="csv",
    )
    rows = list(csv.DictReader(io.StringIO(template.content.decode("utf-8"))))
    rows[0]["human_passed"] = "true"
    rows[0]["reviewer_id"] = "reviewer-low"
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

    imported = service.import_report(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        selected_threshold=0.3,
        payload=stream.getvalue().encode("utf-8"),
        filename="labels.csv",
    )

    assert template.media_type == "text/csv"
    assert imported.report.metric_direction == "lower_is_better"
    assert imported.report.f1 == 1.0


def test_template_rejects_nonterminal_and_empty_applicable_evidence(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    sample_result.metric_applicability = {"correctness": "not_applicable"}
    session.add(sample_result)
    session.flush()
    service = CalibrationService(session, _context_for(sample_result))
    with pytest.raises(CalibrationInputError, match="terminal"):
        service.render_template(
            sample_result.run_id,
            candidate_id=sample_result.run_candidate_id,
            metric_name="correctness",
        )

    sample_result.run.status = RunStatus.COMPLETED
    sample_result.candidate.status = RunStatus.COMPLETED
    with pytest.raises(CalibrationInputError, match="applicable"):
        service.render_template(
            sample_result.run_id,
            candidate_id=sample_result.run_candidate_id,
            metric_name="correctness",
        )


def test_import_rejects_mismatched_evidence(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    service = _finish_run(sample_result, session)
    template = service.render_template(
        sample_result.run_id,
        candidate_id=sample_result.run_candidate_id,
        metric_name="correctness",
        file_format="json",
    )
    baseline = json.loads(_filled_json(template.content))
    mutations = (
        lambda value: value["dataset"].update({"sha256": "0" * 64}),
        lambda value: value["metric"].update({"version": "wrong-version"}),
        lambda value: value["labels"][0].update({"item_id": "missing-result"}),
        lambda value: value["labels"][0].update({"case_external_id": "wrong-case"}),
        lambda value: value["labels"][0].update({"case_position": 99}),
        lambda value: value["labels"][0].update({"score": 0.5}),
    )
    for mutation in mutations:
        changed = deepcopy(baseline)
        mutation(changed)
        with pytest.raises(CalibrationInputError, match="stored run evidence"):
            service.import_report(
                sample_result.run_id,
                candidate_id=sample_result.run_candidate_id,
                metric_name="correctness",
                selected_threshold=0.7,
                payload=json.dumps(changed).encode("utf-8"),
                filename="labels.json",
            )
