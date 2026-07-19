"""Run history, provenance, per-metric evidence, and result exploration."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd
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
)
from evalforge.dashboard.pages.common import (
    client,
    list_payload,
    load_all_runs,
    load_resource,
    nested_summary,
    run_label,
)
from evalforge.dashboard.state import can_edit, navigate_to, select_run, selected_run_id

_EVIDENCE_PAGE_SIZE = 50
_MAX_CALIBRATION_UPLOAD_BYTES = 2 * 1024 * 1024
_CALIBRATION_TEMPLATE_STATE_KEY = "_evalforge_run_export_calibration_template"


def render() -> None:
    page_header(
        "Results",
        "Understand what passed, what needs attention, and how candidates compare.",
        eyebrow="Evaluation evidence",
    )
    api = client()
    runs, run_total, runs_error = load_all_runs(api)
    if runs_error and not runs:
        render_api_error(runs_error)
        return
    if runs_error and run_total is not None:
        render_partial_state(
            f"Loaded {len(runs):,} of {run_total:,} evaluation runs. Older history is "
            "temporarily unavailable."
        )
    if not runs:
        render_empty_state(
            "No evaluation results yet",
            "Start an evaluation to see scores, outputs, and candidate comparisons here.",
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

    _render_run_identity(run)
    _render_result_conclusion(run, results)
    _render_run_actions(run, api)
    _render_analytics(run, results)
    _render_human_calibration(run, results, api)
    _render_results(results, candidate_labels=_candidate_labels(run))


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


def _render_result_conclusion(run: dict[str, Any], results: list[dict[str, Any]]) -> None:
    tone, title, message = _result_conclusion(run, results)
    renderer = {
        "success": st.success,
        "warning": st.warning,
        "info": st.info,
    }[tone]
    renderer(f"**{title}**\n\n{message}")
    if _can_compare(run) and st.button(
        "Compare candidates",
        type="primary",
        icon=":material/compare_arrows:",
        help="Open the shared-case comparison for this evaluation.",
    ):
        select_run(resource_id(run))
        navigate_to("compare")


def _result_conclusion(
    run: dict[str, Any],
    results: list[dict[str, Any]],
) -> tuple[str, str, str]:
    status = str(first_value(run, "status", "state", default="unknown")).lower()
    if not is_terminal_status(status):
        return (
            "info",
            "Evaluation in progress",
            "Results will update as each case finishes.",
        )
    if status in {"failed", "cancelled", "interrupted"} and not results:
        return (
            "warning",
            "No comparable results were produced",
            "Review the status detail, then run the evaluation again when the issue is resolved.",
        )

    target_misses = sum(_has_target_miss(result) for result in results)
    if status in {"completed_with_errors", "partial"}:
        return (
            "warning",
            "Review incomplete results",
            "Some cases did not finish successfully. Check the case evidence before "
            "comparing candidates.",
        )
    if target_misses:
        label = "result" if target_misses == 1 else "results"
        return (
            "warning",
            "Review target misses",
            f"{target_misses} scored {label} missed at least one target. Review those cases "
            "before deciding.",
        )
    if _can_compare(run):
        return (
            "success",
            "Ready to compare candidates",
            "The evaluation finished. Compare the candidates on the same cases before "
            "choosing one.",
        )
    return (
        "success",
        "Evaluation complete",
        "Review the scorecard and case evidence before deciding whether this candidate is ready.",
    )


def _can_compare(run: dict[str, Any]) -> bool:
    status = str(first_value(run, "status", "state", default="")).lower()
    candidates = run.get("candidates")
    count = (
        len([candidate for candidate in candidates if isinstance(candidate, dict)])
        if isinstance(candidates, list)
        else 0
    )
    return status in {"completed", "completed_with_errors", "partial"} and count >= 2


def _render_run_identity(run: dict[str, Any]) -> None:
    name = resource_label(run, fallback="Evaluation run")
    status = str(first_value(run, "status", "state", default="unknown"))
    top_left, top_right = st.columns([3, 1], vertical_alignment="center")
    with top_left:
        st.subheader(name)
        st.caption(format_timestamp(first_value(run, "created_at", "started_at")))
    with top_right:
        render_status_badge(status)

    completed = first_value(run, "completed_items", "completed_count", default=0)
    total = first_value(run, "total_items", "total_count", default=0)
    render_progress(completed, total, status=status)


def _render_run_actions(run: dict[str, Any], api: Any) -> None:
    """Keep technical evidence and controls after the decision-oriented summary."""

    status = str(first_value(run, "status", "state", default="unknown"))

    with st.expander("Audit details", icon=":material/fingerprint:"):
        st.caption("Run ID")
        st.code(resource_id(run), language=None)
        status_reason = first_value(run, "status_reason", "error_message")
        if status_reason:
            safe_text_panel("Status detail", status_reason)

    _render_export_controls(resource_id(run), api)

    if not is_terminal_status(status) and can_edit():
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


def _render_export_controls(run_id: str, api: Any) -> None:
    if not run_id:
        return
    with st.expander("Export evidence", icon=":material/download:"):
        st.caption(
            "Create a versioned, tamper-evident evidence package for review, or download "
            "the underlying record in a familiar format. Nothing is sent to another service."
        )
        disclosure_profile = st.selectbox(
            "Export contents",
            options=["content_redacted", "full_evidence"],
            format_func=lambda value: (
                "Scores and metadata — recommended"
                if value == "content_redacted"
                else "Full stored content"
            ),
            key=f"package-profile-{run_id}",
            help=(
                "The recommended export replaces prompts, inputs, references, outputs, and "
                "context with redaction markers while retaining scores, hashes, and provenance."
            ),
        )
        full_evidence_confirmed = True
        if disclosure_profile == "full_evidence":
            st.warning(
                "Full evidence can contain prompts, test inputs, references, context, and model "
                "outputs. Review your data-handling policy before sharing it."
            )
            full_evidence_confirmed = st.checkbox(
                "I understand that these exports include stored evaluation content.",
                key=f"package-full-confirm-{run_id}",
            )
        if st.button(
            "Prepare evidence package",
            disabled=not full_evidence_confirmed,
            icon=":material/verified:",
            key=f"prepare-run-package-{run_id}",
            width="stretch",
        ):
            _prepare_export(
                api,
                run_id,
                "package",
                disclosure_profile=disclosure_profile,
            )
        package_data = _prepared_export(
            run_id,
            "package",
            disclosure_profile=disclosure_profile,
        )
        if package_data is not None:
            st.download_button(
                "Download evidence package",
                data=package_data,
                file_name=f"evaluation-{run_id}-{disclosure_profile}.json",
                mime="application/vnd.evalforge.run-export+json",
                key=f"download-run-package-{run_id}-{disclosure_profile}",
                width="stretch",
            )

        st.divider()
        st.caption("JSON and CSV use the same content choice and sharing safeguard.")
        json_column, csv_column = st.columns(2)
        with json_column:
            if st.button(
                "Prepare JSON",
                disabled=not full_evidence_confirmed,
                key=f"prepare-run-json-{run_id}",
                width="stretch",
            ):
                _prepare_export(api, run_id, "json", disclosure_profile=disclosure_profile)
            json_data = _prepared_export(run_id, "json", disclosure_profile=disclosure_profile)
            if json_data is not None:
                st.download_button(
                    "Download JSON",
                    data=json_data,
                    file_name=f"evaluation-{run_id}-{disclosure_profile}.json",
                    mime="application/json",
                    key=f"download-run-json-{run_id}-{disclosure_profile}",
                    width="stretch",
                )
        with csv_column:
            if st.button(
                "Prepare CSV",
                disabled=not full_evidence_confirmed,
                key=f"prepare-run-csv-{run_id}",
                width="stretch",
            ):
                _prepare_export(api, run_id, "csv", disclosure_profile=disclosure_profile)
            csv_data = _prepared_export(run_id, "csv", disclosure_profile=disclosure_profile)
            if csv_data is not None:
                st.download_button(
                    "Download CSV",
                    data=csv_data,
                    file_name=f"evaluation-{run_id}-{disclosure_profile}.csv",
                    mime="text/csv",
                    key=f"download-run-csv-{run_id}-{disclosure_profile}",
                    width="stretch",
                )


def _prepare_export(
    api: Any,
    run_id: str,
    export_format: str,
    *,
    disclosure_profile: str = "content_redacted",
) -> None:
    try:
        data = api.export_run(
            run_id,
            export_format=export_format,
            disclosure_profile=disclosure_profile,
        )
    except ApiError as error:
        render_api_error(error, title=f"The {export_format.upper()} export could not be prepared")
        return
    st.session_state[f"_evalforge_run_export_{export_format}"] = {
        "run_id": run_id,
        "disclosure_profile": disclosure_profile,
        "data": data,
    }


def _prepared_export(
    run_id: str,
    export_format: str,
    *,
    disclosure_profile: str = "content_redacted",
) -> bytes | None:
    value = st.session_state.get(f"_evalforge_run_export_{export_format}")
    if (
        not isinstance(value, dict)
        or value.get("run_id") != run_id
        or value.get("disclosure_profile") != disclosure_profile
    ):
        return None
    data = value.get("data")
    return data if isinstance(data, bytes) else None


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

    metric_rows = _metric_scorecard_rows(run, summary, results)
    if not metric_rows:
        render_empty_state(
            "No applicable metric aggregates",
            "The scorecard appears after applicable results are recorded.",
        )
        return
    st.subheader("Metric scorecard")
    st.caption(
        "Scores stay on their stored scale. Read each row's direction and target before "
        "interpreting whether a higher or lower value is better."
    )
    st.dataframe(pd.DataFrame(metric_rows), hide_index=True, width="stretch")


def _render_human_calibration(
    run: dict[str, Any],
    results: list[dict[str, Any]],
    api: Any,
) -> None:
    """Render an optional, progressively disclosed offline calibration workflow."""

    run_id = resource_id(run)
    status = str(first_value(run, "status", "state", default="unknown"))
    with st.container(border=True):
        st.markdown("### Human calibration")
        st.caption(
            "Optional human-review evidence, not production validation. Completed files are "
            "sent to this EvalForge server for in-memory validation; raw labels and reviewer "
            "identifiers are not stored."
        )
        if not run_id:
            st.info("Run identity is unavailable, so calibration cannot be prepared.")
            return
        if not is_terminal_status(status):
            st.info(
                "Calibration becomes available after the evaluation finishes.",
                icon=":material/schedule:",
            )
            return
        if not st.toggle(
            "Show calibration tools",
            value=False,
            key=f"_evalforge_run_export_calibration-open-{run_id}",
            help="Loads the template and saved calibration history for this run.",
        ):
            return

        candidates = _candidate_options(results, _candidate_labels(run))
        if not candidates:
            st.info(
                "No completed candidate scores are available for calibration.",
                icon=":material/info:",
            )
            return

        candidate_column, metric_column = st.columns(2)
        with candidate_column:
            candidate_id = st.selectbox(
                "Candidate",
                options=list(candidates),
                format_func=lambda value: candidates[value],
                key=f"_evalforge_run_export_calibration-candidate-{run_id}",
                help="Calibration always stays linked to one immutable run candidate.",
            )

        metric_options = _calibration_metric_options(run, results, candidate_id)
        if not metric_options:
            st.info(
                "This candidate has no completed, applicable metric scores to calibrate.",
                icon=":material/info:",
            )
            return
        with metric_column:
            metric_name = st.selectbox(
                "Metric",
                options=list(metric_options),
                format_func=lambda value: (
                    f"{_metric_label(value)} · "
                    f"{_direction_label(metric_options[value].get('direction'))}"
                ),
                key=f"_evalforge_run_export_calibration-metric-{run_id}-{candidate_id}",
                help="Only completed scores that can be verified against this run are listed.",
            )

        imported_report: dict[str, Any] | None = None
        template_column, import_column = st.columns(2)
        with template_column:
            st.markdown("#### 1. Download the label template")
            st.caption(
                "Rows follow the case order shown below. Fill both human_passed and reviewer_id; "
                "use an anonymous reviewer code, never a person's name."
            )
            template = _prepared_calibration_template(
                run_id,
                candidate_id=candidate_id,
                metric_name=metric_name,
            )
            if template is None:
                _prepare_calibration_template(
                    api,
                    run_id,
                    candidate_id=candidate_id,
                    metric_name=metric_name,
                )
                template = _prepared_calibration_template(
                    run_id,
                    candidate_id=candidate_id,
                    metric_name=metric_name,
                )
            if template is not None:
                st.download_button(
                    "Download label template",
                    data=template,
                    file_name=(
                        f"evalforge-{_filename_token(run_id)}-"
                        f"{_filename_token(metric_name)}-labels.csv"
                    ),
                    mime="text/csv",
                    key=(
                        f"_evalforge_run_export_calibration-download-"
                        f"{run_id}-{candidate_id}-{metric_name}"
                    ),
                    width="stretch",
                )

        with import_column:
            st.markdown("#### 2. Import completed labels")
            if not can_edit():
                st.caption("Read-only access · Editors can import completed label files.")
            else:
                selected_threshold = st.number_input(
                    "Threshold",
                    min_value=0.0,
                    max_value=1.0,
                    value=_calibration_default_threshold(metric_options[metric_name]),
                    step=0.01,
                    format="%.3f",
                    key=(
                        f"_evalforge_run_export_calibration-threshold-"
                        f"{run_id}-{candidate_id}-{metric_name}"
                    ),
                    help=(
                        "The report measures how this threshold agrees with the uploaded human "
                        "decisions. It does not change the evaluation configuration."
                    ),
                )
                uploaded = st.file_uploader(
                    "Completed CSV or JSON",
                    type=["csv", "json"],
                    accept_multiple_files=False,
                    key=(
                        f"_evalforge_run_export_calibration-upload-"
                        f"{run_id}-{candidate_id}-{metric_name}"
                    ),
                    help=(
                        "Maximum 2 MB. Raw labels and reviewer identifiers are validated in "
                        "memory and are not stored in calibration reports."
                    ),
                )
                upload_size = getattr(uploaded, "size", None)
                oversized = isinstance(upload_size, int) and (
                    upload_size > _MAX_CALIBRATION_UPLOAD_BYTES
                )
                if oversized:
                    st.error("The calibration file exceeds the 2 MB dashboard limit.")
                if st.button(
                    "Import calibration",
                    type="primary",
                    icon=":material/upload_file:",
                    disabled=uploaded is None or oversized,
                    key=(
                        f"_evalforge_run_export_calibration-import-"
                        f"{run_id}-{candidate_id}-{metric_name}"
                    ),
                    width="stretch",
                ):
                    imported_report = _import_calibration(
                        api,
                        run_id,
                        candidate_id=candidate_id,
                        metric_name=metric_name,
                        selected_threshold=float(selected_threshold),
                        uploaded=uploaded,
                    )

        _render_calibration_history(
            api,
            run_id,
            candidate_id=candidate_id,
            candidate_label=candidates[candidate_id],
            metric_name=metric_name,
            imported_report=imported_report,
        )


def _prepare_calibration_template(
    api: Any,
    run_id: str,
    *,
    candidate_id: str,
    metric_name: str,
) -> None:
    try:
        data = api.calibration_template(
            run_id,
            candidate_id=candidate_id,
            metric_name=metric_name,
            template_format="csv",
        )
    except ApiError as error:
        render_api_error(error, title="The calibration template could not be prepared")
        return
    st.session_state[_CALIBRATION_TEMPLATE_STATE_KEY] = {
        "run_id": run_id,
        "candidate_id": candidate_id,
        "metric_name": metric_name,
        "data": data,
    }


def _prepared_calibration_template(
    run_id: str,
    *,
    candidate_id: str,
    metric_name: str,
) -> bytes | None:
    value = st.session_state.get(_CALIBRATION_TEMPLATE_STATE_KEY)
    if (
        not isinstance(value, dict)
        or value.get("run_id") != run_id
        or value.get("candidate_id") != candidate_id
        or value.get("metric_name") != metric_name
    ):
        return None
    data = value.get("data")
    return data if isinstance(data, bytes) else None


def _import_calibration(
    api: Any,
    run_id: str,
    *,
    candidate_id: str,
    metric_name: str,
    selected_threshold: float,
    uploaded: Any,
) -> dict[str, Any] | None:
    content = uploaded.getvalue()
    if len(content) > _MAX_CALIBRATION_UPLOAD_BYTES:
        st.error("The calibration file exceeds the 2 MB dashboard limit.")
        return None
    filename = str(getattr(uploaded, "name", "labels.csv"))
    content_type = "application/json" if filename.lower().endswith(".json") else "text/csv"
    try:
        response = api.import_calibration(
            run_id,
            candidate_id=candidate_id,
            metric_name=metric_name,
            selected_threshold=selected_threshold,
            filename=filename,
            content=content,
            content_type=content_type,
        )
    except ApiError as error:
        render_api_error(error, title="The calibration could not be imported")
        return None

    report = response.get("report")
    status = str(response.get("status", "created"))
    if status == "already_exists":
        st.info(
            "This exact calibration already exists. No duplicate report was created.",
            icon=":material/check_circle:",
        )
    else:
        st.success(
            "Calibration evidence saved. The raw label file was not stored.",
            icon=":material/check_circle:",
        )
    return report if isinstance(report, dict) else None


def _render_calibration_history(
    api: Any,
    run_id: str,
    *,
    candidate_id: str,
    candidate_label: str,
    metric_name: str,
    imported_report: dict[str, Any] | None,
) -> None:
    try:
        payload = api.calibration_reports(
            run_id,
            candidate_id=candidate_id,
            metric_name=metric_name,
            limit=100,
            page=1,
        )
    except ApiError as error:
        render_api_error(error, title="Calibration history could not be loaded")
        if imported_report is None:
            return
        reports = [imported_report]
        total = 1
    else:
        reports = collection_items(payload)
        total = _page_total(payload, default=len(reports))
        if imported_report is not None and not any(
            report.get("id") == imported_report.get("id") for report in reports
        ):
            reports.append(imported_report)

    selected = [
        report
        for report in reports
        if _calibration_report_candidate_id(report) == candidate_id
        and _calibration_report_metric(report).get("name") == metric_name
    ]
    selected.sort(key=lambda report: str(report.get("created_at", "")), reverse=True)
    st.divider()
    if not selected:
        st.info(
            "No calibration reports yet. Download a template when human review would help.",
            icon=":material/science:",
        )
        return

    latest = selected[0]
    st.markdown("**Latest report**")
    _render_calibration_evidence_labels(latest)
    st.caption(_calibration_report_caption(latest, candidate_label=candidate_label))
    _render_calibration_report_cards(latest)

    if len(selected) > 1:
        with st.expander(
            f"Previous reports ({len(selected) - 1})",
            icon=":material/history:",
        ):
            for report in selected[1:]:
                with st.container(border=True):
                    st.caption(_calibration_report_caption(report, candidate_label=candidate_label))
                    _render_calibration_report_cards(report)

    if total > len(reports):
        st.caption(f"Showing the newest {len(reports):,} of {total:,} run reports.")

    with st.expander("Technical details", icon=":material/fingerprint:"):
        st.caption("Immutable identities and hashes for independent verification.")
        safe_json_panel(
            "Calibration report provenance",
            [_calibration_technical_details(report) for report in selected],
        )


def _render_calibration_evidence_labels(report: dict[str, Any]) -> None:
    evidence_column, validation_column = st.columns(2)
    with evidence_column:
        st.badge("Offline evidence", color="blue", icon=":material/offline_bolt:")
    with validation_column:
        production_validated = report.get("production_validated") is True
        st.badge(
            "Production validated" if production_validated else "Not production validated",
            color="green" if production_validated else "gray",
            icon=":material/verified_user:" if production_validated else ":material/info:",
        )


def _render_calibration_report_cards(report: dict[str, Any]) -> None:
    render_metric_cards(
        [
            MetricCard("Sample size", _count_label(report.get("sample_size"))),
            MetricCard("Precision", format_percent(report.get("precision"))),
            MetricCard("Recall", format_percent(report.get("recall"))),
            MetricCard("F1", format_percent(report.get("f1"))),
        ]
    )


def _calibration_report_caption(
    report: dict[str, Any],
    *,
    candidate_label: str,
) -> str:
    metric = _calibration_report_metric(report)
    threshold = format_score(report.get("selected_threshold"))
    direction = _direction_label(metric.get("direction"))
    created = format_timestamp(report.get("created_at"))
    return (
        f"{candidate_label} · {_metric_label(str(metric.get('name', 'Metric')))} · "
        f"{direction} · threshold {threshold} · {created}"
    )


def _calibration_technical_details(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_id": report.get("id"),
        "run_id": report.get("run_id"),
        "candidate_id": _calibration_report_candidate_id(report),
        "dataset": report.get("dataset"),
        "metric": _calibration_report_metric(report),
        "label_manifest_sha256": first_value(
            report,
            "label_manifest_sha256",
            "manifest_sha256",
        ),
        "report_sha256": report.get("report_sha256"),
        "confusion_matrix": report.get("confusion_matrix"),
        "human_pass_count": report.get("human_pass_count"),
        "human_fail_count": report.get("human_fail_count"),
        "reviewer_count": report.get("reviewer_count"),
        "evidence_kind": report.get("evidence_kind"),
        "production_validated": report.get("production_validated"),
    }


def _calibration_report_candidate_id(report: dict[str, Any]) -> str:
    value = first_value(report, "candidate_id", "run_candidate_id")
    return str(value) if value is not None else ""


def _calibration_report_metric(report: dict[str, Any]) -> dict[str, Any]:
    metric = report.get("metric")
    if isinstance(metric, dict):
        return metric
    return {
        "name": report.get("metric_name"),
        "version": report.get("metric_version"),
        "direction": report.get("metric_direction"),
    }


def _calibration_metric_options(
    run: dict[str, Any],
    results: list[dict[str, Any]],
    candidate_id: str,
) -> dict[str, dict[str, Any]]:
    eligible: set[str] = set()
    for result in results:
        if _candidate_id(result) != candidate_id:
            continue
        metrics = normalized_metric_rows(
            first_value(result, "metric_results", "metrics", "scores", default=[])
        )
        for metric in metrics:
            name = str(first_value(metric, "name", "metric", default=""))
            applicability = str(
                first_value(metric, "applicability", "status", default="applicable")
            ).lower()
            score = first_value(metric, "score", "value")
            if (
                name
                and name != "aggregate_quality"
                and applicability
                not in {
                    "not_applicable",
                    "error",
                    "errored",
                    "failed",
                    "failure",
                    "invalid",
                }
                and isinstance(score, (int, float))
                and not isinstance(score, bool)
                and math.isfinite(float(score))
            ):
                eligible.add(name)

    metadata = _metric_metadata(run, results)
    ordered = [name for name in metadata if name in eligible]
    ordered.extend(sorted(eligible - set(ordered)))
    return {name: metadata.get(name, {}) for name in ordered}


def _calibration_default_threshold(metadata: dict[str, Any]) -> float:
    value = metadata.get("threshold")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    ):
        return float(value)
    return 0.5


def _count_label(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"{value:,}"
    return "—"


def _filename_token(value: str) -> str:
    token = "".join(
        character if character.isascii() and character.isalnum() else "-" for character in value
    ).strip("-")
    return token[:80] or "calibration"


def _render_results(
    results: list[dict[str, Any]],
    *,
    candidate_labels: dict[str, str] | None = None,
) -> None:
    st.subheader("Case-level evidence")
    if not results:
        render_empty_state(
            "No result rows yet",
            "Queued and running evaluations populate this section as items finish.",
        )
        return

    immutable_labels = candidate_labels or {}
    candidate_options = _candidate_options(results, immutable_labels)
    candidate_column, attention_column = st.columns(2)
    with candidate_column:
        selected_candidate = st.selectbox(
            "Candidate filter",
            options=["all", *candidate_options],
            format_func=lambda value: (
                "All candidates" if value == "all" else candidate_options[value]
            ),
        )
    with attention_column:
        attention_filter = st.selectbox(
            "Case filter",
            options=["all", "needs_attention"],
            format_func=lambda value: "All cases" if value == "all" else "Needs attention",
            help="Shows failed, errored, or below-target results.",
        )
    filtered = [
        result
        for result in results
        if (selected_candidate == "all" or _candidate_id(result) == selected_candidate)
        and (attention_filter == "all" or _needs_attention(result))
    ]
    if not filtered:
        st.info("No cases match these filters.", icon=":material/filter_alt_off:")
        return
    page_number = 1
    page_count = max(1, math.ceil(len(filtered) / _EVIDENCE_PAGE_SIZE))
    if page_count > 1:
        page_number = st.selectbox(
            "Evidence page",
            options=list(range(1, page_count + 1)),
            format_func=lambda value: f"Page {value} of {page_count}",
            key=f"result-evidence-page-{selected_candidate}-{attention_filter}",
            help=(
                "Every matching result remains available; pages keep the review screen responsive."
            ),
        )
    page_results, first_position, last_position = _result_page(filtered, page_number)
    if page_count > 1:
        st.caption(f"Showing results {first_position:,}-{last_position:,} of {len(filtered):,}.")

    for index, result in enumerate(page_results, start=first_position):
        status = str(first_value(result, "status", "state", default="completed"))
        case_identity = _case_identity(result, fallback=f"Case {index}")
        with st.expander(
            f"{case_identity} · {status.replace('_', ' ').title()}",
            icon=":material/article:",
        ):
            _render_result(
                result,
                candidate_label=immutable_labels.get(_candidate_id(result)),
            )


def _result_page(
    results: list[dict[str, Any]],
    page_number: int,
    *,
    page_size: int = _EVIDENCE_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int, int]:
    """Return one clamped, one-indexed page without making later evidence unreachable."""

    if page_size < 1:
        raise ValueError("page_size must be positive")
    page_count = max(1, math.ceil(len(results) / page_size))
    safe_page = min(max(page_number, 1), page_count)
    start = (safe_page - 1) * page_size
    page = results[start : start + page_size]
    first_position = start + 1 if page else 0
    return page, first_position, start + len(page)


def _render_result(result: dict[str, Any], *, candidate_label: str | None = None) -> None:
    input_snapshot = result.get("input_snapshot")
    snapshot = input_snapshot if isinstance(input_snapshot, dict) else {}
    identity, score_column, latency_column = st.columns([3, 1, 1])
    with identity:
        st.text(_case_identity(result, fallback="Test case result"))
        candidate = candidate_label or first_value(
            result,
            "candidate_name",
            "model_name",
            "prompt_name",
        )
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
    output_column, reference_column = st.columns(2)
    with output_column:
        safe_text_panel("Model output", output)
    with reference_column:
        safe_text_panel("Reference", reference)
    if context:
        with st.expander("Source context", icon=":material/source:"):
            safe_text_panel("Source context", context)

    metrics = normalized_metric_rows(
        first_value(result, "metric_results", "metrics", "scores", default=[])
    )
    if metrics:
        display_rows: list[dict[str, Any]] = []
        for metric in metrics:
            passed = first_value(metric, "passed")
            applicability = str(
                first_value(metric, "applicability", "status", default="applicable")
            )
            display_rows.append(
                {
                    "Metric": _metric_label(
                        str(first_value(metric, "name", "metric", default="Metric"))
                    ),
                    "Score": format_score(first_value(metric, "score", "value")),
                    "Direction": _direction_label(first_value(metric, "direction")),
                    "Target": _target_label(
                        first_value(metric, "direction"),
                        first_value(metric, "threshold"),
                    ),
                    "Status": _applicability_label(applicability),
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


def _candidate_labels(run: dict[str, Any]) -> dict[str, str]:
    value = run.get("candidates")
    candidates = (
        [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    )
    labels: dict[str, str] = {}
    for candidate in candidates:
        candidate_id = first_value(candidate, "id", "candidate_id", "run_candidate_id")
        label = first_value(candidate, "label", "name", "title")
        if candidate_id is not None and label is not None:
            labels[str(candidate_id)] = str(label)
    return labels


def _candidate_options(
    results: list[dict[str, Any]],
    candidate_labels: dict[str, str] | None = None,
) -> dict[str, str]:
    immutable_labels = candidate_labels or {}
    options: dict[str, str] = {}
    for result in results:
        candidate_id = _candidate_id(result)
        if not candidate_id:
            continue
        label = immutable_labels.get(candidate_id) or str(
            first_value(
                result,
                "candidate_name",
                "model_name",
                "prompt_name",
                default=f"Candidate {candidate_id[:8]}",
            )
        )
        options[candidate_id] = str(label)
    return options


def _case_identity(result: dict[str, Any], *, fallback: str) -> str:
    input_snapshot = result.get("input_snapshot")
    snapshot = input_snapshot if isinstance(input_snapshot, dict) else {}
    value = first_value(snapshot, "external_id", "name", "title", "label")
    if value is None:
        value = first_value(result, "case_name", "external_id", "test_case_id")
    if value is None:
        case_hash = result.get("case_hash")
        if isinstance(case_hash, str) and case_hash:
            value = f"Case {case_hash[:8]}"
    return str(value) if value is not None else fallback


def _passed_label(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "—"


def _candidate_id(result: dict[str, Any]) -> str:
    value = first_value(result, "candidate_id", "run_candidate_id", "model_profile_id")
    return str(value) if value is not None else ""


def _needs_attention(result: dict[str, Any]) -> bool:
    status = str(first_value(result, "status", "state", default="completed")).lower()
    if status not in {"completed", "success", "succeeded"}:
        return True
    return bool(first_value(result, "error_message", "error", "failure_reason")) or (
        _has_target_miss(result)
    )


def _has_target_miss(result: dict[str, Any]) -> bool:
    """Return whether any stored aggregate or applicable metric needs review."""

    if result.get("aggregate_passed") is False:
        return True
    metrics = normalized_metric_rows(
        first_value(result, "metric_results", "metrics", "scores", default=[])
    )
    error_states = {"error", "errored", "failed", "failure", "invalid"}
    for metric in metrics:
        if metric.get("passed") is False:
            return True
        states = (metric.get("applicability"), metric.get("status"))
        if any(str(value).strip().lower() in error_states for value in states if value is not None):
            return True
    return False


def _metric_scorecard_rows(
    run: dict[str, Any],
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> list[dict[str, str]]:
    means = {
        name: score
        for name, score in _metric_means(summary, results).items()
        if name != "aggregate_quality"
    }
    if not means:
        return []

    metadata = _metric_metadata(run, results)
    configured_order = [name for name in metadata if name in means]
    metric_names = [*configured_order, *sorted(set(means) - set(configured_order))]
    applicable_counts = _metric_applicable_counts(results)
    denominator = len(results)
    rows: list[dict[str, str]] = []
    for name in metric_names:
        metric_metadata = metadata.get(name, {})
        direction = metric_metadata.get("direction")
        threshold = metric_metadata.get("threshold")
        rows.append(
            {
                "Metric": _metric_label(name),
                "Mean score": format_score(means[name]),
                "Direction": _direction_label(direction),
                "Target": _target_label(direction, threshold),
                "Applicable results": (
                    f"{applicable_counts.get(name, 0):,} / {denominator:,}" if denominator else "—"
                ),
            }
        )
    return rows


def _metric_metadata(
    run: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    snapshot = run.get("metric_configuration_snapshot")
    if isinstance(snapshot, dict):
        configured = snapshot.get("metrics")
        if isinstance(configured, list):
            for item in configured:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if isinstance(name, str) and name != "aggregate_quality":
                    metadata[name] = {
                        "direction": item.get("direction"),
                        "threshold": item.get("threshold"),
                    }
        directions = snapshot.get("directions")
        if isinstance(directions, dict):
            for name, direction in directions.items():
                if str(name) == "aggregate_quality":
                    continue
                metadata.setdefault(str(name), {})["direction"] = direction

    for result in results:
        metrics = normalized_metric_rows(
            first_value(result, "metric_results", "metrics", "scores", default=[])
        )
        for metric in metrics:
            name = str(first_value(metric, "name", "metric", default=""))
            if not name or name == "aggregate_quality":
                continue
            target = metadata.setdefault(name, {})
            if target.get("direction") is None and metric.get("direction") is not None:
                target["direction"] = metric["direction"]
            if target.get("threshold") is None and metric.get("threshold") is not None:
                target["threshold"] = metric["threshold"]
    return metadata


def _metric_applicable_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for result in results:
        metrics = normalized_metric_rows(
            first_value(result, "metric_results", "metrics", "scores", default=[])
        )
        for metric in metrics:
            name = str(first_value(metric, "name", "metric", default=""))
            applicability = str(
                first_value(metric, "applicability", "status", default="applicable")
            ).lower()
            score = first_value(metric, "score", "value")
            if (
                name
                and name != "aggregate_quality"
                and applicability != "not_applicable"
                and isinstance(score, (int, float))
                and not isinstance(score, bool)
            ):
                counts[name] += 1
    return dict(counts)


def _metric_label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize() or "Metric"


def _direction_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "higher_is_better":
        return "Higher is better"
    if normalized == "lower_is_better":
        return "Lower is better"
    return "Direction unavailable"


def _target_label(direction: Any, threshold: Any) -> str:
    rendered_threshold = format_score(threshold)
    if rendered_threshold == "—":
        return "Not configured"
    normalized = str(direction or "").strip().lower()
    if normalized == "higher_is_better":
        return f"at least {rendered_threshold}"
    if normalized == "lower_is_better":
        return f"at most {rendered_threshold}"
    return rendered_threshold


def _applicability_label(value: str) -> str:
    if value.strip().lower() == "not_applicable":
        return "Not scored"
    return value.replace("_", " ").strip().title()


def _metric_means(
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, float]:
    stored = first_value(summary, "metric_means", "per_metric_means", "metrics")
    if isinstance(stored, dict):
        means: dict[str, float] = {}
        for name, value in stored.items():
            score = first_value(value, "score", "mean") if isinstance(value, dict) else value
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
            name = str(first_value(metric, "name", "metric", default="Metric"))
            if name == "aggregate_quality":
                continue
            score = first_value(metric, "score", "value")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                values[name].append(float(score))
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
