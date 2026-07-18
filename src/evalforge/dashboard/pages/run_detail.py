"""Run history, provenance, per-metric evidence, and result exploration."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from evalforge.dashboard.client import ApiError, collection_items, public_payload
from evalforge.dashboard.components import (
    MetricCard,
    first_value,
    format_currency,
    format_duration_ms,
    format_micro_usd,
    format_percent,
    format_score,
    format_timestamp,
    is_terminal_status,
    normalized_metric_rows,
    page_header,
    render_api_error,
    render_empty_state,
    render_metric_cards,
    render_partial_state,
    render_progress,
    render_status_badge,
    resource_id,
    resource_label,
    safe_json_panel,
    safe_text_panel,
    style_figure,
)
from evalforge.dashboard.pages.common import (
    client,
    list_payload,
    load_resource,
    nested_summary,
    run_label,
)
from evalforge.dashboard.state import select_run, selected_run_id


def render() -> None:
    page_header(
        "Run detail & history",
        "Trace every aggregate back to its case, output, metric evidence, and immutable snapshot.",
        eyebrow="Evidence explorer",
    )
    api = client()
    runs_payload, runs_error = load_resource("run history", api.runs)
    if runs_error:
        render_api_error(runs_error)
        return
    runs = list_payload(runs_payload)
    if not runs:
        render_empty_state(
            "No evaluation runs yet",
            "Submit a deterministic evaluation to populate run history and result evidence.",
            icon=":material/history:",
        )
        return

    run_by_id = {resource_id(run): run for run in runs if resource_id(run)}
    run_ids = list(run_by_id)
    preferred = selected_run_id()
    default_index = run_ids.index(preferred) if preferred in run_ids else 0
    run_id = st.selectbox(
        "Evaluation run",
        options=run_ids,
        index=default_index,
        format_func=lambda value: run_label(run_by_id[value]),
    )
    select_run(run_id)

    run, run_error = load_resource("run detail", lambda: api.run(run_id))
    if run_error:
        render_api_error(run_error)
        return
    if not isinstance(run, dict):
        render_empty_state("Run detail is unavailable", "Choose another run or retry.")
        return

    results, result_total, results_error = _load_all_results(api, run_id)
    if results_error:
        if not results:
            embedded = run.get("results")
            results = collection_items(embedded) if embedded is not None else []
        if result_total is not None and results:
            render_partial_state(
                f"Loaded {len(results):,} of {result_total:,} result rows before the result "
                "feed became unavailable. Aggregates below reflect only the loaded rows."
            )
        else:
            render_partial_state(
                "Run metadata loaded, but the dedicated result feed is unavailable. "
                "Showing embedded results when present."
            )

    _render_run_header(run, api)
    _render_analytics(run, results)
    _render_results(results)


def _load_all_results(
    api: Any,
    run_id: str,
    *,
    page_size: int = 500,
) -> tuple[list[dict[str, Any]], int | None, ApiError | None]:
    """Load the complete bounded result collection without hiding pagination gaps."""

    try:
        payload = api.run_results(run_id, limit=page_size, page=1)
    except ApiError as error:
        return [], None, error

    results = list_payload(payload)
    total = _page_total(payload, default=len(results))
    if total <= len(results):
        return results, total, None

    page_count = math.ceil(total / page_size)
    for page in range(2, page_count + 1):
        try:
            page_payload = api.run_results(run_id, limit=page_size, page=page)
        except ApiError as error:
            return results, total, error
        results.extend(list_payload(page_payload))
    if len(results) < total:
        return (
            results,
            total,
            ApiError("The API returned fewer result rows than its pagination total"),
        )
    return results, total, None


def _page_total(payload: Any, *, default: int) -> int:
    if isinstance(payload, dict):
        value = payload.get("total")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return default


def _render_run_header(run: dict[str, Any], api: Any) -> None:
    name = resource_label(run, fallback="Evaluation run")
    status = str(first_value(run, "status", "state", default="unknown"))
    top_left, top_right = st.columns([3, 1], vertical_alignment="center")
    with top_left:
        st.subheader("Evaluation run")
        st.text(name)
        st.text(f"Run ID: {resource_id(run)}")
        st.text(format_timestamp(first_value(run, "created_at", "started_at")))
    with top_right:
        render_status_badge(status)

    completed = first_value(run, "completed_items", "completed_count", default=0)
    total = first_value(run, "total_items", "total_count", default=0)
    render_progress(completed, total, status=status)

    if not is_terminal_status(status):
        with st.expander("Run controls", icon=":material/tune:"):
            confirm_cancel = st.checkbox(
                "I want to cancel this active evaluation.",
                key=f"cancel-confirm-{resource_id(run)}",
            )
            if st.button(
                "Cancel run",
                disabled=not confirm_cancel,
                icon=":material/cancel:",
                key=f"cancel-{resource_id(run)}",
            ):
                try:
                    api.cancel_run(resource_id(run))
                except ApiError as error:
                    render_api_error(error, title="The run could not be cancelled")
                else:
                    st.success("Cancellation requested.")
                    api.clear_cache()
                    st.rerun()


def _render_analytics(run: dict[str, Any], results: list[dict[str, Any]]) -> None:
    summary = nested_summary(run)
    derived = _derived_result_summary(results)
    render_metric_cards(
        [
            MetricCard(
                "Pass rate",
                format_percent(
                    first_value(
                        summary,
                        "pass_rate",
                        "overall_pass_rate",
                        default=derived.get("pass_rate"),
                    )
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
                        default=derived.get("mean_score"),
                    )
                ),
            ),
            MetricCard(
                "P95 latency",
                format_duration_ms(
                    first_value(
                        summary,
                        "p95_latency_ms",
                        "latency_p95_ms",
                        default=derived.get("p95_latency_ms"),
                    )
                ),
            ),
            MetricCard(
                _detail_cost_label(summary, derived),
                _detail_cost(summary, derived),
                help_text=_detail_cost_help(summary, derived),
            ),
        ]
    )

    metric_means = _metric_means(summary, results)
    if not metric_means:
        render_empty_state(
            "No applicable metric aggregates",
            "Metric charts appear after applicable results are recorded.",
        )
        return
    names = list(metric_means)
    scores = [metric_means[name] for name in names]
    frame = pd.DataFrame({"Metric": names, "Score": scores})
    left, right = st.columns(2)
    with left:
        st.subheader("Metric means")
        figure = px.bar(
            frame,
            x="Score",
            y="Metric",
            orientation="h",
            range_x=[0, 1],
            color="Score",
            color_continuous_scale=["#EF6A67", "#E8A317", "#16B8C8", "#6558F5"],
        )
        figure.update_layout(coloraxis_showscale=False)
        st.plotly_chart(style_figure(figure), width="stretch", config={"displayModeBar": False})
    with right:
        st.subheader("Quality profile")
        radar = go.Figure(
            data=[
                go.Scatterpolar(
                    r=[*scores, scores[0]],
                    theta=[*names, names[0]],
                    fill="toself",
                    fillcolor="rgba(101, 88, 245, 0.18)",
                    line={"color": "#6558F5", "width": 3},
                    name="Mean score",
                )
            ]
        )
        radar.update_layout(
            polar={"radialaxis": {"visible": True, "range": [0, 1]}},
            showlegend=False,
        )
        st.plotly_chart(style_figure(radar), width="stretch", config={"displayModeBar": False})


def _render_results(results: list[dict[str, Any]]) -> None:
    st.subheader("Case-level evidence")
    if not results:
        render_empty_state(
            "No result rows yet",
            "Queued and running evaluations populate this section as items finish.",
        )
        return

    candidate_options = _candidate_options(results)
    selected_candidate = st.selectbox(
        "Candidate filter",
        options=["all", *candidate_options],
        format_func=lambda value: "All candidates" if value == "all" else candidate_options[value],
    )
    filtered = [
        result
        for result in results
        if selected_candidate == "all" or _candidate_id(result) == selected_candidate
    ]
    if len(filtered) > 50:
        render_partial_state("Showing the first 50 matching results. Refine the candidate filter.")

    for index, result in enumerate(filtered[:50], start=1):
        status = str(first_value(result, "status", "state", default="completed"))
        with st.expander(
            f"Result {index} · {status.replace('_', ' ').title()}",
            icon=":material/article:",
        ):
            _render_result(result)


def _render_result(result: dict[str, Any]) -> None:
    input_snapshot = result.get("input_snapshot")
    snapshot = input_snapshot if isinstance(input_snapshot, dict) else {}
    identity, score_column, latency_column = st.columns([3, 1, 1])
    with identity:
        st.text(resource_label(snapshot or result, fallback="Test case result"))
        candidate = first_value(result, "candidate_name", "model_name", "prompt_name")
        if candidate:
            st.text(f"Candidate: {candidate}")
    with score_column:
        st.metric("Quality", format_score(first_value(result, "aggregate_score", "score")))
    with latency_column:
        st.metric("Latency", format_duration_ms(first_value(result, "latency_ms")))

    output = first_value(result, "output_text", "output", "actual_output", "response_text")
    reference = first_value(
        result,
        "expected_output",
        "reference",
        "reference_output",
        default=first_value(snapshot, "expected_output", "reference_output"),
    )
    context = first_value(
        result,
        "context_text",
        "context",
        "source_context",
        default=first_value(snapshot, "context_text", "context"),
    )
    text_columns = st.columns(3)
    with text_columns[0]:
        safe_text_panel("Model output", output)
    with text_columns[1]:
        safe_text_panel("Reference", reference)
    with text_columns[2]:
        safe_text_panel("Source context", context)

    metrics = normalized_metric_rows(
        first_value(result, "metric_results", "metrics", "scores", default=[])
    )
    if metrics:
        display_rows: list[dict[str, Any]] = []
        for metric in metrics:
            passed = first_value(metric, "passed")
            display_rows.append(
                {
                    "Metric": first_value(metric, "name", "metric", default="Metric"),
                    "Score": format_score(first_value(metric, "score", "value")),
                    "Status": first_value(metric, "applicability", "status", default="applicable"),
                    "Passed": _passed_label(passed),
                    "Reason": first_value(metric, "reason", "explanation", default=""),
                }
            )
        st.dataframe(pd.DataFrame(display_rows), hide_index=True, width="stretch")
        with st.expander("Metric evidence", icon=":material/fact_check:"):
            for metric in metrics:
                st.text(str(first_value(metric, "name", "metric", default="Metric")))
                safe_json_panel("Evidence", public_payload(metric.get("evidence", {})))

    error_message = first_value(result, "error_message", "error", "failure_reason")
    if error_message:
        st.error("This result contains an execution error.")
        safe_text_panel("Error detail", error_message)
        error_type = first_value(result, "error_type", "error_code", "failure_category")
        if error_type:
            safe_text_panel("Error classification", error_type)

    provenance = {
        key: result[key]
        for key in (
            "request_id",
            "case_hash",
            "prompt_hash",
            "provider",
            "model",
            "api_mode",
            "retry_count",
            "usage",
            "estimated_cost",
            "estimated_cost_micro_usd",
            "cost_source",
            "metric_versions",
            "provider_metadata",
            "input_snapshot",
            "case_snapshot",
            "prompt_snapshot",
            "model_snapshot",
        )
        if key in result
    }
    with st.expander("Immutable provenance", icon=":material/fingerprint:"):
        safe_json_panel("Stored snapshot", public_payload(provenance))


def _candidate_options(results: list[dict[str, Any]]) -> dict[str, str]:
    options: dict[str, str] = {}
    for result in results:
        candidate_id = _candidate_id(result)
        if not candidate_id:
            continue
        label = first_value(
            result,
            "candidate_name",
            "model_name",
            "prompt_name",
            default=f"Candidate {candidate_id[:8]}",
        )
        options[candidate_id] = str(label)
    return options


def _passed_label(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "—"


def _candidate_id(result: dict[str, Any]) -> str:
    value = first_value(result, "candidate_id", "run_candidate_id", "model_profile_id")
    return str(value) if value is not None else ""


def _metric_means(
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, float]:
    stored = first_value(summary, "metric_means", "per_metric_means", "metrics")
    if isinstance(stored, dict):
        means: dict[str, float] = {}
        for name, value in stored.items():
            score = value.get("score") if isinstance(value, dict) else value
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                means[str(name)] = float(score)
        if means:
            return means

    values: dict[str, list[float]] = defaultdict(list)
    for result in results:
        metrics = normalized_metric_rows(
            first_value(result, "metric_results", "metrics", "scores", default=[])
        )
        for metric in metrics:
            score = first_value(metric, "score", "value")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                values[str(first_value(metric, "name", "metric", default="Metric"))].append(
                    float(score)
                )
    return {name: sum(scores) / len(scores) for name, scores in values.items() if scores}


def _derived_result_summary(
    results: list[dict[str, Any]],
) -> dict[str, float | int | None]:
    scores = [
        float(result["aggregate_score"])
        for result in results
        if isinstance(result.get("aggregate_score"), (int, float))
        and not isinstance(result.get("aggregate_score"), bool)
    ]
    passed = [
        bool(result["aggregate_passed"])
        for result in results
        if isinstance(result.get("aggregate_passed"), bool)
    ]
    latencies = sorted(
        float(result["latency_ms"])
        for result in results
        if isinstance(result.get("latency_ms"), (int, float))
        and not isinstance(result.get("latency_ms"), bool)
    )
    known_costs = [
        float(result["estimated_cost_micro_usd"])
        for result in results
        if isinstance(result.get("estimated_cost_micro_usd"), (int, float))
        and not isinstance(result.get("estimated_cost_micro_usd"), bool)
    ]
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1) if latencies else 0
    return {
        "mean_score": sum(scores) / len(scores) if scores else None,
        "pass_rate": sum(passed) / len(passed) if passed else None,
        "p95_latency_ms": latencies[p95_index] if latencies else None,
        "known_cost_micro_usd": sum(known_costs) if known_costs else None,
        "known_cost_items": len(known_costs),
        "result_count": len(results),
    }


def _detail_cost(
    summary: dict[str, Any],
    derived: dict[str, float | int | None],
) -> str:
    micro_usd = first_value(
        summary,
        "known_cost_micro_usd",
        default=derived.get("known_cost_micro_usd"),
    )
    if micro_usd is not None:
        return format_micro_usd(micro_usd)
    return format_currency(first_value(summary, "estimated_cost", "cost_usd", "total_cost_usd"))


def _detail_cost_label(
    summary: dict[str, Any],
    derived: dict[str, float | int | None],
) -> str:
    known = first_value(
        summary,
        "known_cost_micro_usd",
        default=derived.get("known_cost_micro_usd"),
    )
    return "Known spend" if known is not None else "Estimated cost"


def _detail_cost_help(
    summary: dict[str, Any],
    derived: dict[str, float | int | None],
) -> str:
    known = first_value(
        summary,
        "known_cost_micro_usd",
        default=derived.get("known_cost_micro_usd"),
    )
    if known is None:
        return "No result-level recorded pricing is available; this value is an API estimate."

    known_items = first_value(
        summary,
        "known_cost_items",
        default=derived.get("known_cost_items"),
    )
    total_items = first_value(
        summary,
        "completed",
        "completed_items",
        default=derived.get("result_count"),
    )
    if isinstance(known_items, int) and isinstance(total_items, int) and total_items > 0:
        if known_items == total_items:
            return f"Recorded pricing covers all {total_items:,} loaded results."
        return (
            f"Recorded pricing covers {known_items:,} of {total_items:,} loaded results; "
            "unpriced results are excluded."
        )
    return "Sum of results with recorded pricing; pricing coverage is unavailable."
