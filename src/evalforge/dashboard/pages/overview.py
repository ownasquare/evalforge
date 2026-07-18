"""Portfolio-level evaluation overview."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from evalforge.dashboard.client import collection_items
from evalforge.dashboard.components import (
    MetricCard,
    first_value,
    format_count,
    format_currency,
    format_micro_usd,
    format_percent,
    format_score,
    format_timestamp,
    page_header,
    render_api_error,
    render_demo_banner,
    render_empty_state,
    render_metric_cards,
    render_partial_state,
    render_status_badge,
    resource_label,
    style_figure,
)
from evalforge.dashboard.pages.common import client, load_resource, nested_summary
from evalforge.dashboard.state import navigate_to


def render() -> None:
    page_header(
        "EvalForge overview",
        "A production-minded view of benchmark quality, reliability, latency, and cost.",
        eyebrow="LLM evaluation control room",
    )

    api = client()
    overview, overview_error = load_resource("evaluation overview", api.overview)
    capabilities, capability_error = load_resource("provider capabilities", api.capabilities)

    if overview_error:
        render_api_error(overview_error)
        render_empty_state(
            "No overview is available yet",
            "Start the FastAPI service, seed the deterministic demo, then retry this page.",
            icon=":material/monitor_heart:",
        )
        return
    if not isinstance(overview, dict):
        render_empty_state(
            "No evaluation data yet",
            "Create a benchmark and submit your first run.",
        )
        return

    summary = nested_summary(overview)
    data_mode_value = first_value(overview, "data_mode", "integrity")
    if data_mode_value is not None:
        data_mode = str(data_mode_value).lower()
        synthetic = data_mode in {"demo", "deterministic", "fixture", "synthetic", "offline"}
        render_demo_banner(synthetic=synthetic)
    else:
        st.info(
            "Deterministic demo execution is available. Individual runs retain their own "
            "provider and provenance labels.",
            icon=":material/science:",
        )
    if capability_error:
        render_partial_state(
            "Evaluation analytics loaded, but provider capability status is temporarily "
            "unavailable."
        )

    quality_pass_rate = first_value(summary, "pass_rate", "overall_pass_rate")
    result_success_rate = first_value(summary, "result_success_rate")
    render_metric_cards(
        [
            MetricCard(
                "Total runs",
                format_count(first_value(summary, "total_runs", "run_count", "runs")),
                help_text="All persisted evaluation runs in this environment.",
            ),
            MetricCard(
                "Pass rate" if quality_pass_rate is not None else "Result success",
                format_percent(
                    quality_pass_rate if quality_pass_rate is not None else result_success_rate
                ),
                help_text=(
                    "Applicable results meeting their stored thresholds."
                    if quality_pass_rate is not None
                    else "Results completed without an execution error."
                ),
            ),
            MetricCard(
                "Mean quality",
                format_score(
                    first_value(
                        summary,
                        "mean_score",
                        "average_score",
                        "quality_score",
                        "mean_quality",
                    )
                ),
                help_text="Weighted mean over applicable quality metrics only.",
            ),
            MetricCard(
                _overview_cost_label(summary),
                _overview_cost(summary),
                help_text=(
                    "Sum of results with recorded pricing; unpriced results are excluded."
                    if summary.get("known_cost_micro_usd") is not None
                    else "Estimate reported by the API; unavailable pricing is not treated as zero."
                ),
            ),
        ]
    )

    action_left, action_right = st.columns([2, 1], vertical_alignment="center")
    with action_left:
        st.subheader("Run the offline benchmark")
        st.caption(
            "Use deterministic model profiles to validate the entire workflow without an API key."
        )
    with action_right:
        if st.button(
            "Start deterministic evaluation",
            type="primary",
            icon=":material/play_arrow:",
            width="stretch",
        ):
            navigate_to("run_evaluation")

    _render_trends_and_leaderboard(overview)
    _render_failures_and_activity(overview)
    _render_capability_note(capabilities)


def _render_trends_and_leaderboard(overview: dict[str, Any]) -> None:
    trend_data = first_value(overview, "trend", "score_trend", "quality_trend", default=[])
    leaderboard = first_value(
        overview,
        "leaderboard",
        "candidate_leaderboard",
        "candidates",
        default=[],
    )
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Quality trend")
        trend_items = collection_items(trend_data) if isinstance(trend_data, dict) else trend_data
        if isinstance(trend_items, list) and trend_items:
            frame = pd.DataFrame(trend_items)
            x_name = _first_column(frame, ("created_at", "timestamp", "date", "run"))
            y_name = _first_column(frame, ("mean_score", "score", "quality", "pass_rate"))
            if x_name and y_name:
                figure = px.line(
                    frame,
                    x=x_name,
                    y=y_name,
                    markers=True,
                    color_discrete_sequence=["#6558F5"],
                )
                figure.update_yaxes(range=[0, 1], title="Quality score")
                figure.update_xaxes(title=None)
                st.plotly_chart(
                    style_figure(figure),
                    width="stretch",
                    config={"displayModeBar": False},
                )
            else:
                render_empty_state(
                    "Trend needs more runs",
                    "Run at least two comparable benchmarks.",
                )
        else:
            render_empty_state(
                "Trend needs more runs",
                "Run at least two comparable benchmarks.",
            )

    with right:
        st.subheader("Candidate leaderboard")
        candidate_items = (
            collection_items(leaderboard) if isinstance(leaderboard, dict) else leaderboard
        )
        if isinstance(candidate_items, list) and candidate_items:
            for position, candidate in enumerate(candidate_items[:5], start=1):
                if not isinstance(candidate, dict):
                    continue
                with st.container(border=True):
                    name = resource_label(candidate, fallback=f"Candidate {position}")
                    st.text(f"{position}. {name}")
                    score = first_value(candidate, "mean_score", "score", "quality_score")
                    st.metric("Mean quality", format_score(score))
        else:
            render_empty_state(
                "No candidates ranked",
                "A completed run with two or more candidates will populate this leaderboard.",
            )


def _render_failures_and_activity(overview: dict[str, Any]) -> None:
    failures = first_value(overview, "failure_categories", "failures", "error_taxonomy", default=[])
    activity = first_value(overview, "recent_runs", "recent_activity", "runs", default=[])
    left, right = st.columns([2, 3])
    with left:
        st.subheader("Failure categories")
        failure_items = collection_items(failures) if isinstance(failures, dict) else failures
        if isinstance(failure_items, list) and failure_items:
            frame = pd.DataFrame(failure_items)
            category = _first_column(frame, ("category", "name", "error_type"))
            count = _first_column(frame, ("count", "total", "value"))
            if category and count:
                figure = px.bar(
                    frame,
                    x=count,
                    y=category,
                    orientation="h",
                    color_discrete_sequence=["#EF6A67"],
                )
                figure.update_layout(showlegend=False)
                st.plotly_chart(
                    style_figure(figure, height=300),
                    width="stretch",
                    config={"displayModeBar": False},
                )
        else:
            st.success("No recorded evaluation failures.", icon=":material/check_circle:")

    with right:
        st.subheader("Recent activity")
        activity_items = collection_items(activity) if isinstance(activity, dict) else activity
        if isinstance(activity_items, list) and activity_items:
            for run in activity_items[:6]:
                if not isinstance(run, dict):
                    continue
                columns = st.columns([3, 1, 2], vertical_alignment="center")
                with columns[0]:
                    st.text(resource_label(run, fallback="Evaluation run"))
                with columns[1]:
                    render_status_badge(str(first_value(run, "status", default="unknown")))
                with columns[2]:
                    st.caption(format_timestamp(first_value(run, "created_at", "started_at")))
        else:
            render_empty_state("No recent runs", "Submitted evaluations will appear here.")


def _render_capability_note(capabilities: Any) -> None:
    if not isinstance(capabilities, dict):
        return
    demo_available = bool(
        first_value(capabilities, "demo_available", "deterministic_available", default=True)
    )
    real_enabled = _real_runs_enabled(capabilities)
    with st.expander("Execution capability", icon=":material/settings_input_component:"):
        st.write(
            "Deterministic offline execution is available."
            if demo_available
            else "Deterministic execution is unavailable."
        )
        st.write(
            "Real-provider runs are enabled with backend-side controls."
            if real_enabled
            else "Real-provider runs are disabled; no paid call can be started from this dashboard."
        )


def _first_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def _overview_cost(summary: dict[str, Any]) -> str:
    micro_usd = summary.get("known_cost_micro_usd")
    if micro_usd is not None:
        return format_micro_usd(micro_usd)
    return format_currency(first_value(summary, "estimated_cost", "total_cost_usd", "cost_usd"))


def _overview_cost_label(summary: dict[str, Any]) -> str:
    return "Known spend" if summary.get("known_cost_micro_usd") is not None else "Estimated spend"


def _real_runs_enabled(capabilities: dict[str, Any]) -> bool:
    direct = first_value(capabilities, "real_runs_enabled")
    if isinstance(direct, bool):
        return direct
    providers = capabilities.get("providers")
    nested = providers.get("real_runs_enabled") if isinstance(providers, dict) else False
    return nested if isinstance(nested, bool) else False
