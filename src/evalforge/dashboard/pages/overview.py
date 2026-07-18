"""Evaluation workspace overview backed only by published aggregate data."""

from __future__ import annotations

from typing import Any

import streamlit as st

from evalforge.dashboard.components import (
    MetricCard,
    first_value,
    format_count,
    format_micro_usd,
    format_percent,
    format_score,
    format_timestamp,
    page_header,
    render_api_error,
    render_empty_state,
    render_metric_cards,
    render_partial_state,
    render_status_badge,
    resource_id,
    resource_label,
)
from evalforge.dashboard.pages.common import client, load_resource, nested_summary
from evalforge.dashboard.state import navigate_to, select_run


def render() -> None:
    page_header(
        "Evaluation workspace",
        "Review recent evaluations and open the work that needs attention.",
        eyebrow="Workspace",
    )

    api = client()
    overview, overview_error = load_resource("evaluation overview", api.overview)
    capabilities, capability_error = load_resource("provider capabilities", api.capabilities)

    if overview_error:
        render_api_error(overview_error, title="The evaluation workspace is unavailable")
        render_empty_state(
            "Reconnect the local service",
            "Once it is available, the deterministic demo can run without a provider key.",
            icon=":material/monitor_heart:",
        )
        return
    if not isinstance(overview, dict):
        render_empty_state(
            "No workspace data is available",
            "Start a deterministic evaluation to create the first evidence set.",
        )
        return

    summary = nested_summary(overview)
    recent = _records(first_value(overview, "recent_runs", default=[]))
    if capability_error:
        render_partial_state(
            "Evaluation data loaded, but current execution capability could not be confirmed."
        )
    _render_workspace_mode(capabilities)

    action_copy, action = st.columns([3, 1], vertical_alignment="center")
    with action_copy:
        st.subheader("Continue evaluating")
        st.caption(
            "Use a saved benchmark to compare prompt and model candidates on the same cases."
        )
    with action:
        if st.button(
            "New evaluation",
            type="primary",
            icon=":material/add:",
            width="stretch",
        ):
            navigate_to("run_evaluation")

    render_metric_cards(
        [
            MetricCard(
                "Runs",
                format_count(first_value(summary, "runs", "total_runs", "run_count")),
                help_text="All persisted evaluation runs in this workspace.",
            ),
            MetricCard(
                "Completed",
                format_count(first_value(summary, "completed_runs")),
                help_text="Runs that reached a completed terminal state.",
            ),
            MetricCard(
                "Results checked",
                format_count(first_value(summary, "evaluated_results", "results")),
                help_text="Result rows that reached a completed or error outcome.",
            ),
            MetricCard(
                "Average quality",
                format_score(first_value(summary, "mean_quality", "mean_score")),
                help_text="Mean aggregate score over results with applicable quality evidence.",
            ),
        ]
    )

    if not _positive_int(first_value(summary, "runs", "total_runs", default=0)):
        render_empty_state(
            "Your workspace is ready",
            "Run the local sample to see scores, case evidence, and candidate comparisons.",
            icon=":material/check_circle:",
        )

    recent_column, evidence_column = st.columns([3, 2])
    with recent_column:
        _render_recent_runs(recent)
    with evidence_column:
        _render_evidence_coverage(summary)


def _render_workspace_mode(capabilities: Any) -> None:
    if _real_runs_enabled(capabilities):
        st.caption(
            "External provider execution is available. Every paid run still requires explicit "
            "cost review."
        )
    else:
        st.caption(
            "Offline demo workspace · Deterministic fixtures are available without provider "
            "requests or billable usage."
        )


def _real_runs_enabled(capabilities: Any) -> bool:
    if not isinstance(capabilities, dict):
        return False
    direct = capabilities.get("real_runs_enabled")
    providers = capabilities.get("providers")
    nested = providers.get("real_runs_enabled") if isinstance(providers, dict) else False
    return direct is True or nested is True


def _render_recent_runs(runs: list[dict[str, Any]]) -> None:
    st.subheader("Recent evaluations")
    if not runs:
        render_empty_state(
            "No recent evaluations",
            "Finished and active runs will appear here.",
            icon=":material/history:",
        )
        return

    for run in runs[:8]:
        run_id = resource_id(run)
        fallback = f"Run {run_id[:8]}" if run_id else "Evaluation run"
        name = resource_label(run, fallback=fallback)
        completed = _nonnegative_int(first_value(run, "completed_items", default=0))
        total = _nonnegative_int(first_value(run, "total_items", default=0))
        with st.container(border=True):
            identity, action = st.columns([4, 1.2], vertical_alignment="center")
            with identity:
                st.text(name)
                render_status_badge(str(first_value(run, "status", default="unknown")))
                detail_parts: list[str] = []
                if total:
                    detail_parts.append(f"{completed:,} of {total:,} results")
                detail_parts.append(format_timestamp(first_value(run, "created_at", "started_at")))
                st.caption(" · ".join(detail_parts))
            with action:
                if run_id and st.button(
                    "View",
                    key=f"open-overview-run-{run_id}",
                    width="stretch",
                ):
                    select_run(run_id)
                    navigate_to("run_detail")


def _render_evidence_coverage(summary: dict[str, Any]) -> None:
    st.subheader("Evidence coverage")
    total_results = _nonnegative_int(first_value(summary, "results", default=0))
    known_items = _nonnegative_int(first_value(summary, "known_cost_items", default=0))
    ambiguous = _nonnegative_int(first_value(summary, "billing_ambiguous_results", default=0))
    unavailable = _nonnegative_int(first_value(summary, "unavailable_cost_results", default=0))
    success_rate = first_value(summary, "result_success_rate")

    with st.container(border=True):
        if known_items:
            st.text(format_micro_usd(first_value(summary, "known_cost_micro_usd")))
            st.caption(f"Recorded pricing covers {known_items:,} of {total_results:,} results.")
        else:
            st.text("Pricing is unavailable")
            st.caption("No result in this workspace has recorded pricing evidence yet.")

        st.divider()
        st.text(f"Result success · {format_percent(success_rate)}")
        if ambiguous:
            st.caption(f"{ambiguous:,} results have billing-ambiguous provider evidence.")
        if unavailable:
            st.caption(f"{unavailable:,} results do not have usable pricing evidence.")
        if not ambiguous and not unavailable:
            st.caption("No pricing-evidence gaps are recorded in the current totals.")


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _positive_int(value: Any) -> bool:
    return _nonnegative_int(value) > 0
