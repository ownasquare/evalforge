"""Paired, case-aligned comparison of prompt/model candidates."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from evalforge.dashboard.client import collection_items
from evalforge.dashboard.components import (
    MetricCard,
    first_value,
    format_currency,
    format_duration_ms,
    format_micro_usd,
    format_score,
    page_header,
    render_api_error,
    render_empty_state,
    render_metric_cards,
    resource_id,
    resource_label,
    style_figure,
)
from evalforge.dashboard.pages.common import client, list_payload, load_resource, run_label
from evalforge.dashboard.state import select_run, selected_run_id


def render() -> None:
    page_header(
        "Compare candidates",
        "Use paired case deltas—not unrelated averages—to see which candidate actually wins.",
        eyebrow="Decision workspace",
    )
    api = client()
    runs_payload, runs_error = load_resource("completed runs", api.runs)
    if runs_error:
        render_api_error(runs_error)
        return
    runs = list_payload(runs_payload)
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

    _render_decision_summary(comparison)
    _render_candidate_chart(candidates)
    _render_win_tie_loss(comparison)
    _render_paired_deltas(comparison)
    _render_candidate_table(candidates)


def _render_decision_summary(comparison: dict[str, Any]) -> None:
    winner = first_value(comparison, "winner_name", "recommended_candidate", "winner")
    confidence = first_value(comparison, "confidence", "winner_confidence")
    paired_cases = first_value(comparison, "paired_case_count", "comparable_cases", "case_count")
    tie_rate = first_value(comparison, "tie_rate")
    paired = _comparison_items(comparison, "paired_comparisons")
    if paired_cases is None and paired:
        paired_cases = sum(int(item.get("paired_cases", 0)) for item in paired)
    if tie_rate is None and paired:
        ties = sum(int(item.get("ties", 0)) for item in paired)
        total = sum(int(item.get("paired_cases", 0)) for item in paired)
        tie_rate = ties / total if total else None
    cards = [
        MetricCard("Paired cases", str(paired_cases) if paired_cases is not None else "—"),
        MetricCard("Tie rate", f"{float(tie_rate) * 100:.1f}%" if _number(tie_rate) else "—"),
        MetricCard("Confidence", format_score(confidence)),
    ]
    render_metric_cards(cards, max_columns=3)
    if winner:
        st.success("Leading candidate")
        st.text(str(winner))
        st.caption("Treat this as evaluation evidence, not an automatic production promotion.")


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
        color_continuous_scale=["#EF6A67", "#E8A317", "#16B8C8", "#6558F5"],
    )
    figure.update_layout(coloraxis_showscale=False)
    st.plotly_chart(style_figure(figure), width="stretch", config={"displayModeBar": False})


def _render_win_tie_loss(comparison: dict[str, Any]) -> None:
    payload = first_value(
        comparison,
        "win_tie_loss",
        "outcomes",
        "pairwise_outcomes",
        "paired_comparisons",
    )
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if all(isinstance(value, (int, float)) for value in payload.values()):
            rows = [{"Outcome": str(key).title(), "Count": value} for key, value in payload.items()]
        else:
            rows = collection_items(payload)
    elif isinstance(payload, list):
        raw_rows = [row for row in payload if isinstance(row, dict)]
        has_outcomes = all(
            any(key in row for key in ("wins", "ties", "losses")) for row in raw_rows
        )
        if raw_rows and has_outcomes:
            rows = [
                {"Outcome": "Win", "Count": sum(int(row.get("wins", 0)) for row in raw_rows)},
                {"Outcome": "Tie", "Count": sum(int(row.get("ties", 0)) for row in raw_rows)},
                {"Outcome": "Loss", "Count": sum(int(row.get("losses", 0)) for row in raw_rows)},
            ]
        else:
            rows = raw_rows
    if not rows:
        return
    st.subheader("Win / tie / loss")
    frame = pd.DataFrame(rows)
    outcome_column = next(
        (name for name in ("Outcome", "outcome", "name", "category") if name in frame.columns),
        None,
    )
    count_column = next(
        (name for name in ("Count", "count", "value", "total") if name in frame.columns),
        None,
    )
    if outcome_column and count_column:
        figure = px.bar(
            frame,
            x=outcome_column,
            y=count_column,
            color=outcome_column,
            color_discrete_map={
                "Win": "#1E9E72",
                "Tie": "#E8A317",
                "Loss": "#EF6A67",
                "win": "#1E9E72",
                "tie": "#E8A317",
                "loss": "#EF6A67",
            },
        )
        figure.update_layout(showlegend=False)
        st.plotly_chart(
            style_figure(figure, height=300),
            width="stretch",
            config={"displayModeBar": False},
        )


def _render_paired_deltas(comparison: dict[str, Any]) -> None:
    rows = _comparison_items(
        comparison,
        "paired_deltas",
        "case_deltas",
        "pairs",
        "paired_comparisons",
    )
    st.subheader("Paired case deltas")
    if not rows:
        render_empty_state(
            "No paired deltas",
            "The API did not return case-aligned delta evidence for this run.",
        )
        return
    frame = pd.DataFrame(rows)
    preferred = [
        column
        for column in (
            "case_name",
            "case_id",
            "candidate_a",
            "candidate_b",
            "score_a",
            "score_b",
            "delta",
            "winner",
            "baseline_candidate_id",
            "challenger_candidate_id",
            "paired_cases",
            "mean_delta",
            "wins",
            "ties",
            "losses",
        )
        if column in frame.columns
    ]
    st.dataframe(frame[preferred] if preferred else frame, hide_index=True, width="stretch")


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
