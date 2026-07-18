"""Paired, case-aligned comparison of prompt/model candidates."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from evalforge.dashboard.client import collection_items
from evalforge.dashboard.components import (
    CHART_SEQUENTIAL_SCALE,
    first_value,
    format_currency,
    format_duration_ms,
    format_micro_usd,
    format_score,
    page_header,
    render_api_error,
    render_empty_state,
    resource_id,
    resource_label,
    style_figure,
)
from evalforge.dashboard.pages.common import client, load_all_runs, load_resource, run_label
from evalforge.dashboard.state import select_run, selected_run_id


def render() -> None:
    page_header(
        "Compare",
        "Compare each challenger with the baseline on the same test cases.",
        eyebrow="Shared-case evidence",
    )
    api = client()
    runs, run_total, runs_error = load_all_runs(api)
    if runs_error and not runs:
        render_api_error(runs_error)
        return
    if runs_error and run_total is not None:
        st.warning(
            f"Loaded {len(runs):,} of {run_total:,} runs. Older comparison history is "
            "temporarily unavailable.",
            icon=":material/data_alert:",
        )
    eligible = [
        run
        for run in runs
        if str(first_value(run, "status", default="")).lower()
        in {"completed", "completed_with_errors", "partial"}
    ]
    if not eligible:
        render_empty_state(
            "No comparable run yet",
            "Complete a run with at least two prompt/model candidates to unlock paired comparison.",
            icon=":material/compare_arrows:",
        )
        return

    run_by_id = {resource_id(run): run for run in eligible if resource_id(run)}
    run_ids = list(run_by_id)
    preferred = selected_run_id()
    default_index = run_ids.index(preferred) if preferred in run_ids else 0
    run_id = st.selectbox(
        "Run to compare",
        options=run_ids,
        index=default_index,
        format_func=lambda value: run_label(run_by_id[value]),
    )
    select_run(run_id)

    comparison, comparison_error = load_resource(
        "paired comparison", lambda: api.run_comparison(run_id)
    )
    if comparison_error:
        render_api_error(comparison_error, title="Paired comparison is unavailable")
        return
    if not isinstance(comparison, dict):
        render_empty_state("No comparison data", "This run has no candidate comparison payload.")
        return

    candidates = _comparison_items(comparison, "candidates", "candidate_summaries", "leaderboard")
    if len(candidates) < 2:
        render_empty_state(
            "At least two candidates are required",
            "Re-run the benchmark with multiple prompt versions or model profiles.",
        )
        return

    candidate_labels = _candidate_label_map(candidates)
    _render_pairwise_summary(comparison, candidate_labels)
    _render_candidate_chart(candidates)
    _render_case_evidence(comparison, candidate_labels)
    _render_candidate_table(candidates)


def _render_pairwise_summary(
    comparison: dict[str, Any],
    candidate_labels: dict[str, str],
) -> None:
    st.subheader("Pairwise summary")
    st.caption(
        "Each row compares a challenger with the stored baseline over the same cases. "
        "These benchmark aggregates do not select or automatically promote a production "
        "candidate."
    )
    rows = _pairwise_summary_rows(comparison, candidate_labels)
    if not rows:
        render_empty_state(
            "No pairwise summary",
            "The API did not return a shared-case aggregate for these candidates.",
        )
        return
    for row in rows:
        with st.container(border=True):
            st.caption(f"Baseline · {row['Baseline']}")
            st.markdown(f"**Challenger · {row['Challenger']}**")
            paired_cases, quality_delta = st.columns(2)
            with paired_cases:
                st.caption("Shared cases")
                st.text(row["Paired cases"])
            with quality_delta:
                st.caption("Mean quality change")
                st.text(row["Mean quality delta"])
            st.caption("Case outcomes")
            st.write(
                f"Wins {row['Challenger wins']} · "
                f"Ties {row['Ties']} · "
                f"Regressions {row['Challenger regressions']}"
            )


def _render_candidate_chart(candidates: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        rows.append(
            {
                "Candidate": resource_label(candidate, fallback=f"Candidate {index}"),
                "Quality": first_value(
                    candidate,
                    "mean_score",
                    "mean_quality",
                    "score",
                    "quality_score",
                ),
                "Pass rate": first_value(candidate, "pass_rate"),
            }
        )
    frame = pd.DataFrame(rows)
    if "Quality" not in frame or frame["Quality"].isna().all():
        return
    st.subheader("Candidate quality")
    figure = px.bar(
        frame,
        x="Candidate",
        y="Quality",
        color="Quality",
        range_y=[0, 1],
        color_continuous_scale=list(CHART_SEQUENTIAL_SCALE),
    )
    figure.update_layout(coloraxis_showscale=False)
    st.plotly_chart(style_figure(figure), width="stretch", config={"displayModeBar": False})


def _render_case_evidence(
    comparison: dict[str, Any],
    candidate_labels: dict[str, str],
) -> None:
    rows = _case_evidence_rows(comparison, candidate_labels)
    st.subheader("Case evidence")
    if not rows:
        render_empty_state(
            "No case-level comparison evidence",
            "The API returned pairwise aggregates without individual shared-case deltas.",
        )
        return
    st.caption("Regressions are listed first, followed by improvements and ties.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_candidate_table(candidates: list[dict[str, Any]]) -> None:
    st.subheader("Operational trade-offs")
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        rows.append(
            {
                "Candidate": resource_label(candidate, fallback=f"Candidate {index}"),
                "Mean quality": format_score(
                    first_value(
                        candidate,
                        "mean_score",
                        "mean_quality",
                        "score",
                        "quality_score",
                    )
                ),
                "Pass rate": _percent(first_value(candidate, "pass_rate")),
                "Median latency": format_duration_ms(
                    first_value(candidate, "median_latency_ms", "latency_median_ms")
                ),
                "P95 latency": format_duration_ms(
                    first_value(candidate, "p95_latency_ms", "latency_p95_ms")
                ),
                "Known spend": _candidate_cost(candidate),
                "Pricing coverage": _pricing_coverage(candidate),
                "Errors": first_value(candidate, "error_count", "errors", default=0),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _candidate_label_map(candidates: Any) -> dict[str, str]:
    if not isinstance(candidates, list):
        return {}
    labels: dict[str, str] = {}
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        candidate_id = first_value(candidate, "candidate_id", "id", "run_candidate_id")
        if candidate_id is None:
            continue
        labels[str(candidate_id)] = resource_label(
            candidate,
            fallback=f"Candidate {index}",
        )
    return labels


def _pairwise_summary_rows(
    comparison: dict[str, Any],
    candidate_labels: dict[str, str],
) -> list[dict[str, str]]:
    pairs = _comparison_items(comparison, "paired_comparisons")
    default_baseline = first_value(comparison, "baseline_candidate_id")
    rows: list[dict[str, str]] = []
    for pair in pairs:
        baseline_id = first_value(
            pair,
            "baseline_candidate_id",
            default=default_baseline,
        )
        challenger_id = first_value(pair, "challenger_candidate_id")
        paired_cases = _count(first_value(pair, "paired_cases"))
        rows.append(
            {
                "Baseline": str(
                    first_value(
                        pair,
                        "baseline_label",
                        default=_candidate_label(baseline_id, candidate_labels),
                    )
                ),
                "Challenger": str(
                    first_value(
                        pair,
                        "challenger_label",
                        default=_candidate_label(challenger_id, candidate_labels),
                    )
                ),
                "Paired cases": f"{paired_cases:,}" if paired_cases is not None else "—",
                "Mean quality delta": _format_delta(first_value(pair, "mean_delta", "delta")),
                "Challenger wins": _fraction_label(pair.get("wins"), paired_cases),
                "Ties": _fraction_label(pair.get("ties"), paired_cases),
                "Challenger regressions": _fraction_label(pair.get("losses"), paired_cases),
            }
        )
    return rows


def _case_evidence_rows(
    comparison: dict[str, Any],
    candidate_labels: dict[str, str],
) -> list[dict[str, str]]:
    deltas = _comparison_items(
        comparison,
        "paired_case_deltas",
        "case_deltas",
        "pairs",
    )
    if not deltas:
        return []
    default_baseline = first_value(comparison, "baseline_candidate_id")
    ordered = sorted(
        enumerate(deltas),
        key=lambda indexed: (0 if _case_outcome(indexed[1]) == "Regression" else 1, indexed[0]),
    )
    rows: list[dict[str, str]] = []
    for position, (_, item) in enumerate(ordered, start=1):
        baseline_id = first_value(
            item,
            "baseline_candidate_id",
            "candidate_a_id",
            "candidate_a",
            default=default_baseline,
        )
        challenger_id = first_value(
            item,
            "challenger_candidate_id",
            "candidate_b_id",
            "candidate_b",
        )
        case_identity = first_value(
            item,
            "case_name",
            "case_label",
            "external_id",
            "case_external_id",
            "case_id",
            "test_case_id",
            default=f"Case {position}",
        )
        rows.append(
            {
                "Case": str(case_identity),
                "Baseline": str(
                    first_value(
                        item,
                        "baseline_label",
                        default=_candidate_label(baseline_id, candidate_labels),
                    )
                ),
                "Challenger": str(
                    first_value(
                        item,
                        "challenger_label",
                        default=_candidate_label(challenger_id, candidate_labels),
                    )
                ),
                "Baseline score": format_score(first_value(item, "baseline_score", "score_a")),
                "Challenger score": format_score(first_value(item, "challenger_score", "score_b")),
                "Delta": _format_delta(
                    first_value(item, "delta", "score_delta", "quality_delta", "mean_delta")
                ),
                "Outcome": _case_outcome(item),
            }
        )
    return rows


def _candidate_label(value: Any, candidate_labels: dict[str, str]) -> str:
    if value is None:
        return "Candidate unavailable"
    candidate_id = str(value)
    return candidate_labels.get(candidate_id, candidate_id)


def _case_outcome(item: dict[str, Any]) -> str:
    explicit = str(first_value(item, "outcome", "result", default="")).strip().lower()
    if explicit in {"regression", "loss", "worse"}:
        return "Regression"
    if explicit in {"improvement", "win", "better"}:
        return "Improvement"
    if explicit in {"tie", "unchanged", "equal"}:
        return "Tie"
    if item.get("regression") is True:
        return "Regression"
    delta = _finite_number(first_value(item, "delta", "score_delta", "quality_delta", "mean_delta"))
    if delta is not None:
        if delta < 0:
            return "Regression"
        if delta > 0:
            return "Improvement"
        return "Tie"
    winner = first_value(item, "winner", "winner_candidate_id")
    baseline_id = first_value(item, "baseline_candidate_id", "candidate_a_id")
    challenger_id = first_value(item, "challenger_candidate_id", "candidate_b_id")
    if winner is not None and baseline_id is not None and str(winner) == str(baseline_id):
        return "Regression"
    if winner is not None and challenger_id is not None and str(winner) == str(challenger_id):
        return "Improvement"
    return "Not classified"


def _fraction_label(value: Any, denominator: int | None) -> str:
    numerator = _count(value)
    if numerator is None or denominator is None:
        return "—"
    if denominator <= 0:
        return f"{numerator:,} / {denominator:,}"
    return f"{numerator:,} / {denominator:,} ({numerator / denominator * 100:.1f}%)"


def _format_delta(value: Any) -> str:
    number = _finite_number(value)
    if number is None:
        return "—"
    return f"{number:+.3f}"


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _comparison_items(comparison: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = comparison.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            items = collection_items(value)
            if items:
                return items
    return []


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _percent(value: Any) -> str:
    if not _number(value):
        return "—"
    number = float(value)
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.1f}%"


def _candidate_cost(candidate: dict[str, Any]) -> str:
    micro_usd = candidate.get("known_cost_micro_usd")
    if micro_usd is not None:
        known_items = candidate.get("known_cost_items")
        if isinstance(known_items, int) and not isinstance(known_items, bool) and known_items == 0:
            return "—"
        return format_micro_usd(micro_usd)
    return format_currency(first_value(candidate, "estimated_cost", "cost_usd"))


def _pricing_coverage(candidate: dict[str, Any]) -> str:
    known_items = candidate.get("known_cost_items")
    completed = first_value(candidate, "completed", "sample_size")
    if (
        isinstance(known_items, int)
        and not isinstance(known_items, bool)
        and isinstance(completed, int)
        and not isinstance(completed, bool)
    ):
        return f"{known_items:,}/{completed:,} results"
    return "Unavailable"
