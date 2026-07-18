from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from evalforge.analytics import build_overview, build_run_comparison
from evalforge.models import (
    ApiMode,
    EvaluationResult,
    ModelProfile,
    ResultStatus,
    RunCandidate,
    RunStatus,
    canonical_json_hash,
)


@pytest.mark.integration
def test_overview_reports_unavailable_cost_when_no_result_has_pricing(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    sample_result.estimated_cost_micro_usd = None
    sample_result.cost_source = "pricing_unavailable"
    session.add(sample_result)
    session.commit()

    totals = build_overview(session)["totals"]

    assert totals["known_cost_micro_usd"] is None
    assert totals["known_cost_items"] == 0


@pytest.mark.integration
def test_overview_preserves_known_zero_cost_with_positive_pricing_coverage(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    sample_result.estimated_cost_micro_usd = 0
    sample_result.cost_source = "synthetic"
    session.add(sample_result)
    session.commit()

    totals = build_overview(session)["totals"]

    assert totals["known_cost_micro_usd"] == 0
    assert totals["known_cost_items"] == 1


@pytest.mark.integration
def test_comparison_includes_labeled_case_aligned_deltas(
    session: Session,
    sample_result: EvaluationResult,
) -> None:
    sample_result.aggregate_score = 0.4
    sample_result.aggregate_passed = False
    sample_result.status = ResultStatus.COMPLETED
    sample_result.candidate.status = RunStatus.COMPLETED
    sample_result.run.status = RunStatus.COMPLETED
    sample_result.run.total_items = 2
    sample_result.run.completed_items = 2
    sample_result.run.succeeded_items = 2
    session.add(sample_result)
    session.flush()

    challenger = _add_challenger_result(session, sample_result, score=0.7)
    session.commit()

    comparison = build_run_comparison(session, sample_result.run_id)

    assert comparison["paired_comparisons"] == [
        {
            "baseline_candidate_id": sample_result.run_candidate_id,
            "baseline_label": sample_result.candidate.label,
            "challenger_candidate_id": challenger.run_candidate_id,
            "challenger_label": challenger.candidate.label,
            "paired_cases": 1,
            "mean_delta": 0.3,
            "wins": 1,
            "ties": 0,
            "losses": 0,
        }
    ]
    assert comparison["paired_case_deltas"] == [
        {
            "baseline_candidate_id": sample_result.run_candidate_id,
            "baseline_label": sample_result.candidate.label,
            "challenger_candidate_id": challenger.run_candidate_id,
            "challenger_label": challenger.candidate.label,
            "test_case_id": sample_result.test_case_id,
            "case_external_id": "case-1",
            "baseline_score": 0.4,
            "challenger_score": 0.7,
            "delta": 0.3,
            "outcome": "win",
        }
    ]


def _add_challenger_result(
    session: Session,
    baseline: EvaluationResult,
    *,
    score: float,
) -> EvaluationResult:
    model_snapshot = {
        "name": "Offline challenger",
        "version": 1,
        "provider": "deterministic",
        "model_name": "challenger",
        "api_mode": "deterministic",
        "generation_parameters": {"temperature": 0},
        "input_price_micro_usd_per_million_tokens": 0,
        "output_price_micro_usd_per_million_tokens": 0,
        "pricing_source": "deterministic",
    }
    model = ModelProfile(
        name="Offline challenger",
        description=None,
        version=1,
        provider="deterministic",
        model_name="challenger",
        api_mode=ApiMode.DETERMINISTIC,
        generation_parameters={"temperature": 0},
        input_price_micro_usd_per_million_tokens=0,
        output_price_micro_usd_per_million_tokens=0,
        pricing_source="deterministic",
        profile_hash=canonical_json_hash(model_snapshot),
        enabled=True,
        metadata_json={},
    )
    session.add(model)
    session.flush()

    candidate = RunCandidate(
        run=baseline.run,
        prompt_template=baseline.candidate.prompt_template,
        model_profile=model,
        ordinal=1,
        label="Direct answer / Offline challenger",
        prompt_snapshot=deepcopy(baseline.prompt_snapshot),
        prompt_hash=baseline.prompt_hash,
        model_snapshot=model_snapshot,
        model_hash=model.profile_hash,
        generation_parameters_snapshot={"temperature": 0},
        candidate_hash=canonical_json_hash(
            {
                "prompt": baseline.prompt_snapshot,
                "model": model_snapshot,
                "temperature": 0,
            }
        ),
        status=RunStatus.COMPLETED,
        total_items=1,
        completed_items=1,
    )
    session.add(candidate)
    session.flush()

    result = EvaluationResult(
        run=baseline.run,
        candidate=candidate,
        test_case=baseline.test_case,
        input_snapshot=deepcopy(baseline.input_snapshot),
        case_hash=baseline.case_hash,
        prompt_snapshot=deepcopy(baseline.prompt_snapshot),
        prompt_hash=baseline.prompt_hash,
        model_snapshot=model_snapshot,
        model_hash=model.profile_hash,
        generation_parameters_snapshot={"temperature": 0},
        rendered_system_prompt=baseline.rendered_system_prompt,
        rendered_user_prompt=baseline.rendered_user_prompt,
        output_text="Paris, France",
        metric_versions=deepcopy(baseline.metric_versions),
        metric_directions=deepcopy(baseline.metric_directions),
        metric_applicability=deepcopy(baseline.metric_applicability),
        metric_results={"correctness": {"score": score, "passed": True}},
        aggregate_score=score,
        aggregate_passed=True,
        effective_metric_weight=1.0,
        provider="deterministic",
        model_name="challenger",
        api_mode=ApiMode.DETERMINISTIC,
        retry_count=0,
        latency_ms=2,
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        estimated_cost_micro_usd=0,
        cost_source="synthetic",
        provider_metadata={},
        status=ResultStatus.COMPLETED,
    )
    session.add(result)
    session.flush()
    return result
