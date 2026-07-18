"""Run comparison and dashboard analytics over immutable persisted results."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean, median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from evalforge.models import EvaluationResult, EvaluationRun, ResultStatus, RunCandidate, RunStatus


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return round(ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction), 3)


def build_overview(session: Session) -> dict[str, Any]:
    """Build bounded aggregate data for the dashboard landing page."""
    total_runs = session.scalar(select(func.count()).select_from(EvaluationRun)) or 0
    completed_runs = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(EvaluationRun.status.in_([RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_ERRORS]))
        )
        or 0
    )
    total_results = session.scalar(select(func.count()).select_from(EvaluationResult)) or 0
    evaluated_results = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.status.in_([ResultStatus.COMPLETED, ResultStatus.ERROR]))
        )
        or 0
    )
    successful_results = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.status == ResultStatus.COMPLETED)
        )
        or 0
    )
    mean_quality = session.scalar(
        select(func.avg(EvaluationResult.aggregate_score)).where(
            EvaluationResult.aggregate_score.is_not(None)
        )
    )
    known_cost = session.scalar(
        select(func.sum(EvaluationResult.estimated_cost_micro_usd)).where(
            EvaluationResult.estimated_cost_micro_usd.is_not(None)
        )
    )
    known_cost_items = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.estimated_cost_micro_usd.is_not(None))
        )
        or 0
    )
    ambiguous_cost_results = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.cost_source == "billing_ambiguous")
        )
        or 0
    )
    unavailable_cost_results = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.cost_source.in_(["usage_unavailable", "pricing_unavailable"]))
        )
        or 0
    )
    recent = list(
        session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(8))
    )
    return {
        "totals": {
            "runs": total_runs,
            "completed_runs": completed_runs,
            "results": total_results,
            "evaluated_results": evaluated_results,
            "successful_results": successful_results,
            "result_success_rate": (
                round(successful_results / evaluated_results, 4) if evaluated_results else None
            ),
            "mean_quality": round(float(mean_quality), 4) if mean_quality is not None else None,
            "known_cost_micro_usd": int(known_cost) if known_cost is not None else None,
            "known_cost_items": known_cost_items,
            "billing_ambiguous_results": ambiguous_cost_results,
            "unavailable_cost_results": unavailable_cost_results,
        },
        "recent_runs": [
            {
                "id": run.id,
                "name": run.name,
                "status": run.status.value,
                "progress": (run.completed_items / run.total_items if run.total_items else 0.0),
                "completed_items": run.completed_items,
                "total_items": run.total_items,
                "created_at": run.created_at.isoformat(),
            }
            for run in recent
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_run_comparison(session: Session, run_id: str) -> dict[str, Any]:
    """Compare variants with explicit denominators and paired case deltas."""
    run = session.scalar(
        select(EvaluationRun)
        .where(EvaluationRun.id == run_id)
        .options(
            selectinload(EvaluationRun.candidates),
            selectinload(EvaluationRun.results),
        )
    )
    if run is None:
        raise LookupError("run not found")
    candidates = sorted(run.candidates, key=lambda item: item.ordinal)
    results_by_candidate: dict[str, list[EvaluationResult]] = defaultdict(list)
    for result in run.results:
        results_by_candidate[result.run_candidate_id].append(result)

    summaries: list[dict[str, Any]] = []
    for candidate in candidates:
        results = results_by_candidate[candidate.id]
        completed = [item for item in results if item.status is ResultStatus.COMPLETED]
        generated = [
            item for item in results if item.provider is not None or item.output_text is not None
        ]
        usage_reported = [
            item
            for item in generated
            if item.cost_source in {"reported_usage", "synthetic"}
            or bool(item.provider_metadata.get("usage_reported"))
        ]
        scores = [item.aggregate_score for item in completed if item.aggregate_score is not None]
        pass_values = [
            item.aggregate_passed for item in completed if item.aggregate_passed is not None
        ]
        latencies = [float(item.latency_ms) for item in generated if item.latency_ms is not None]
        known_costs = [
            item.estimated_cost_micro_usd
            for item in generated
            if item.estimated_cost_micro_usd is not None
        ]
        per_metric: dict[str, list[float]] = defaultdict(list)
        for item in completed:
            for name, metric in item.metric_results.items():
                if name == "aggregate_quality":
                    continue
                score = metric.get("score")
                if score is not None:
                    per_metric[name].append(float(score))
        summaries.append(
            {
                "candidate_id": candidate.id,
                "label": candidate.label,
                "sample_size": len(results),
                "completed": len(completed),
                "generated": len(generated),
                "errors": sum(item.status is ResultStatus.ERROR for item in results),
                "mean_quality": round(mean(scores), 4) if scores else None,
                "pass_rate": (
                    round(sum(bool(value) for value in pass_values) / len(pass_values), 4)
                    if pass_values
                    else None
                ),
                "median_latency_ms": round(median(latencies), 3) if latencies else None,
                "p95_latency_ms": _percentile(latencies, 0.95),
                "known_cost_micro_usd": sum(known_costs),
                "known_cost_items": len(known_costs),
                "billing_ambiguous_items": sum(
                    item.cost_source == "billing_ambiguous" for item in results
                ),
                "unavailable_cost_items": sum(
                    item.cost_source in {"usage_unavailable", "pricing_unavailable"}
                    for item in results
                ),
                "total_tokens": sum(item.total_tokens for item in usage_reported),
                "token_usage_items": len(usage_reported),
                "metrics": {
                    name: {"mean": round(mean(values), 4), "count": len(values)}
                    for name, values in sorted(per_metric.items())
                },
            }
        )

    paired = _paired_deltas(candidates, results_by_candidate)
    paired_case_deltas = _paired_case_deltas(candidates, results_by_candidate)
    return {
        "run_id": run.id,
        "status": run.status.value,
        "baseline_candidate_id": candidates[0].id if candidates else None,
        "baseline_candidate_label": candidates[0].label if candidates else None,
        "candidates": summaries,
        "paired_comparisons": paired,
        "paired_case_deltas": paired_case_deltas,
        "quality_note": (
            "Mean quality uses only applicable, explicitly weighted quality metrics. "
            "Operational latency, reported token usage, and known cost include every "
            "persisted provider response, even when scoring later fails."
        ),
    }


def _paired_deltas(
    candidates: list[RunCandidate],
    results_by_candidate: dict[str, list[EvaluationResult]],
) -> list[dict[str, Any]]:
    if len(candidates) < 2:
        return []
    baseline = candidates[0]
    baseline_by_case = _scored_results_by_case(results_by_candidate[baseline.id])
    comparisons: list[dict[str, Any]] = []
    for challenger in candidates[1:]:
        challenger_by_case = _scored_results_by_case(results_by_candidate[challenger.id])
        shared = sorted(set(baseline_by_case) & set(challenger_by_case))
        deltas: list[float] = []
        for case_id in shared:
            challenger_score = challenger_by_case[case_id].aggregate_score
            baseline_score = baseline_by_case[case_id].aggregate_score
            if challenger_score is not None and baseline_score is not None:
                deltas.append(float(challenger_score) - float(baseline_score))
        epsilon = 1e-9
        comparisons.append(
            {
                "baseline_candidate_id": baseline.id,
                "baseline_label": baseline.label,
                "challenger_candidate_id": challenger.id,
                "challenger_label": challenger.label,
                "paired_cases": len(deltas),
                "mean_delta": round(mean(deltas), 4) if deltas else None,
                "wins": sum(delta > epsilon for delta in deltas),
                "ties": sum(abs(delta) <= epsilon for delta in deltas),
                "losses": sum(delta < -epsilon for delta in deltas),
            }
        )
    return comparisons


def _paired_case_deltas(
    candidates: list[RunCandidate],
    results_by_candidate: dict[str, list[EvaluationResult]],
) -> list[dict[str, Any]]:
    """Return one bounded evidence row per shared scored case and challenger."""

    if len(candidates) < 2:
        return []
    baseline = candidates[0]
    baseline_by_case = _scored_results_by_case(results_by_candidate[baseline.id])
    rows: list[dict[str, Any]] = []
    epsilon = 1e-9
    for challenger in candidates[1:]:
        challenger_by_case = _scored_results_by_case(results_by_candidate[challenger.id])
        for case_id in sorted(set(baseline_by_case) & set(challenger_by_case)):
            baseline_result = baseline_by_case[case_id]
            challenger_result = challenger_by_case[case_id]
            baseline_score = float(baseline_result.aggregate_score)  # type: ignore[arg-type]
            challenger_score = float(challenger_result.aggregate_score)  # type: ignore[arg-type]
            delta = challenger_score - baseline_score
            if delta > epsilon:
                outcome = "win"
            elif delta < -epsilon:
                outcome = "loss"
            else:
                outcome = "tie"
            external_id = baseline_result.input_snapshot.get("external_id")
            rows.append(
                {
                    "baseline_candidate_id": baseline.id,
                    "baseline_label": baseline.label,
                    "challenger_candidate_id": challenger.id,
                    "challenger_label": challenger.label,
                    "test_case_id": case_id,
                    "case_external_id": str(external_id) if external_id is not None else None,
                    "baseline_score": round(baseline_score, 4),
                    "challenger_score": round(challenger_score, 4),
                    "delta": round(delta, 4),
                    "outcome": outcome,
                }
            )
    return rows


def _scored_results_by_case(
    results: list[EvaluationResult],
) -> dict[str, EvaluationResult]:
    return {
        result.test_case_id: result
        for result in results
        if result.status is ResultStatus.COMPLETED and result.aggregate_score is not None
    }
