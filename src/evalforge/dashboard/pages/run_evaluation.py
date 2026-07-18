"""Evaluation matrix builder and bounded live-progress surface."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import streamlit as st

from evalforge.dashboard.client import ApiError, JsonObject
from evalforge.dashboard.components import (
    first_value,
    format_count,
    format_micro_usd,
    is_demo_record,
    is_terminal_status,
    page_header,
    render_api_error,
    render_demo_banner,
    render_empty_state,
    render_flash,
    render_partial_state,
    render_progress,
    render_status_badge,
    resource_id,
    safe_json_panel,
)
from evalforge.dashboard.pages.common import client, list_payload, load_resource, option_map
from evalforge.dashboard.state import (
    active_run_id,
    can_edit,
    clear_active_run,
    navigate_to,
    select_run,
    set_flash,
)

_LAST_FINISHED_RUN_KEY = "_evalforge_last_finished_run_id"


def render() -> None:
    page_header(
        "New evaluation",
        "Choose a benchmark and candidates, then review the exact run before starting.",
        eyebrow="Evaluation setup",
    )
    if not can_edit():
        st.info(
            "Viewer access is read-only. Ask a workspace editor to start an evaluation.",
            icon=":material/visibility:",
        )
        return
    render_flash()
    _render_finished_run_action()

    current_run = active_run_id()
    if current_run:
        _poll_active_run(current_run)
        st.divider()

    api = client()
    datasets_payload, datasets_error = load_resource("datasets", api.datasets)
    prompts_payload, prompts_error = load_resource("prompt versions", api.prompts)
    models_payload, models_error = load_resource("model profiles", api.models)
    capabilities, capability_error = load_resource("execution capabilities", api.capabilities)

    errors = [error for error in (datasets_error, prompts_error, models_error) if error]
    if errors:
        render_api_error(errors[0], title="The evaluation builder is not ready")
        if len(errors) > 1:
            render_partial_state(f"{len(errors)} required API resources are unavailable.")
        return
    if capability_error:
        render_partial_state(
            "Datasets, prompts, and models loaded, but server limits could not be verified. "
            "Submission is paused until capability preflight is available."
        )

    datasets = list_payload(datasets_payload)
    prompts = list_payload(prompts_payload)
    models = list_payload(models_payload)
    if not datasets or not prompts or not models:
        missing = [
            label
            for label, items in (
                ("datasets", datasets),
                ("prompt versions", prompts),
                ("models", models),
            )
            if not items
        ]
        render_empty_state(
            "Finish benchmark setup first",
            f"Add {', '.join(missing)} before starting an evaluation.",
            icon=":material/checklist:",
        )
        if st.button("Open benchmarks", icon=":material/arrow_forward:"):
            navigate_to("test_cases")
        return

    dataset_options = option_map(datasets, fallback="Dataset")
    prompt_options = option_map(prompts, fallback="Prompt")
    model_options = option_map(models, fallback="Model")
    dataset_by_id = {resource_id(item): item for item in datasets}
    model_by_id = {resource_id(item): item for item in models}

    st.subheader("1. Name and benchmark")
    with st.container(border=True):
        dataset_id = st.selectbox(
            "Benchmark dataset",
            options=list(dataset_options),
            format_func=lambda value: dataset_options.get(value, value),
            help="Every selected candidate runs against this versioned dataset.",
        )
        suggested_name = f"{dataset_options.get(dataset_id, 'Benchmark')} review"
        run_name = st.text_input(
            "Run name",
            value=suggested_name,
            max_chars=200,
            placeholder="For example: Support answers — release candidate",
            help="A clear name makes run history and exports easier to find later.",
        )
    dataset_detail, dataset_detail_error = load_resource(
        "selected dataset", lambda: api.dataset(dataset_id)
    )

    st.subheader("2. Candidates")
    with st.container(border=True):
        left, right = st.columns(2)
        with left:
            prompt_defaults = list(prompt_options)[:1]
            prompt_ids = st.multiselect(
                "Prompt versions",
                options=list(prompt_options),
                default=prompt_defaults,
                format_func=lambda value: prompt_options.get(value, value),
                help="Each selected prompt is paired with every selected model profile.",
            )
        with right:
            demo_model_ids = [
                model_id for model_id, model in model_by_id.items() if is_demo_record(model)
            ][:2]
            model_ids = st.multiselect(
                "Model profiles",
                options=list(model_options),
                default=demo_model_ids or list(model_options)[:1],
                format_func=lambda value: model_options.get(value, value),
                help="The first prompt/model pair becomes the comparison baseline.",
            )

    selected_models = [model_by_id[model_id] for model_id in model_ids if model_id in model_by_id]
    has_real_provider = any(not is_demo_record(model) for model in selected_models)
    render_demo_banner(synthetic=not has_real_provider)
    _render_candidate_preview(
        prompt_ids=prompt_ids,
        model_ids=model_ids,
        prompt_options=prompt_options,
        model_options=model_options,
        model_by_id=model_by_id,
    )

    if isinstance(dataset_detail, dict):
        dataset = dataset_detail
    else:
        dataset = dataset_by_id.get(dataset_id, {})
    case_count = _case_count(dataset)
    call_count = case_count * len(prompt_ids) * len(model_ids)
    max_calls = _limit(capabilities, "max_calls_per_run", "max_run_items")
    server_cost_limit = _limit(
        capabilities,
        "max_estimated_cost_micro_usd_per_run",
        "max_estimated_cost_micro_usd",
    )
    real_enabled = _real_runs_enabled(capabilities)

    st.subheader("3. Check and start")
    preflight_columns = st.columns(4)
    preflight_columns[0].metric("Test cases", format_count(case_count))
    preflight_columns[1].metric("Prompt versions", format_count(len(prompt_ids)))
    preflight_columns[2].metric("Model profiles", format_count(len(model_ids)))
    preflight_columns[3].metric("Planned calls", format_count(call_count))
    st.caption(
        "Server preflight calculates a conservative input-token bound and a known-price "
        "cost estimate before any run is created."
    )

    blockers: list[str] = []
    if not run_name.strip():
        blockers.append("Enter a run name.")
    if not prompt_ids:
        blockers.append("Select at least one prompt version.")
    if not model_ids:
        blockers.append("Select at least one model profile.")
    if case_count < 1:
        blockers.append("The selected dataset has no test cases.")
    if max_calls is not None and call_count > max_calls:
        blockers.append(f"The matrix exceeds the server limit of {max_calls:,} calls.")
    if capability_error:
        blockers.append("Server preflight limits are unavailable.")
    if dataset_detail_error:
        blockers.append("The selected dataset detail could not be loaded.")
    if has_real_provider and not real_enabled:
        blockers.append("Real-provider runs are disabled by the backend.")

    acknowledged_cost = True
    acknowledged_transfer = True
    spend_limit_micro_usd: int | None = None
    if has_real_provider:
        st.markdown("**Provider approval**")
        acknowledged_transfer = st.checkbox(
            "I approve sending this benchmark's prompts, inputs, and context to the selected "
            "external providers.",
            value=False,
        )
        acknowledged_cost = st.checkbox(
            "I understand external provider use may incur charges.",
            value=False,
        )
        spend_limit_usd = st.number_input(
            "Estimated-spend ceiling (USD)",
            min_value=0.000001,
            max_value=(server_cost_limit / 1_000_000 if server_cost_limit is not None else None),
            value=None,
            step=0.01,
            format="%.6f",
            help=(
                "The server rejects the run when its known-price preflight estimate exceeds "
                "this amount. This is not a final invoice or provider-side billing limit."
            ),
        )
        if spend_limit_usd is not None:
            spend_limit_micro_usd = max(1, round(float(spend_limit_usd) * 1_000_000))
        if server_cost_limit is not None:
            st.caption(
                f"The server also caps estimated spend at {format_micro_usd(server_cost_limit)}."
            )
        if not acknowledged_transfer:
            blockers.append("Approve the external data transfer before submission.")
        if not acknowledged_cost:
            blockers.append("Confirm the provider-cost disclosure before submission.")
        if spend_limit_micro_usd is None:
            blockers.append("Set an estimated-spend ceiling before submission.")

    for blocker in blockers:
        st.warning(blocker, icon=":material/warning:")

    preflight_payload = {
        "name": run_name.strip(),
        "dataset_id": dataset_id,
        "prompt_ids": prompt_ids,
        "model_ids": model_ids,
        "acknowledge_real_cost": bool(has_real_provider and acknowledged_cost),
        "acknowledge_unknown_cost": False,
        "acknowledge_external_data_transfer": bool(has_real_provider and acknowledged_transfer),
        "spend_limit_micro_usd": spend_limit_micro_usd,
    }
    signature = (
        run_name.strip(),
        dataset_id,
        tuple(prompt_ids),
        tuple(model_ids),
        bool(has_real_provider and acknowledged_cost),
        bool(has_real_provider and acknowledged_transfer),
        spend_limit_micro_usd,
    )
    if st.button(
        "Check setup",
        icon=":material/check_circle:",
        disabled=bool(blockers),
        width="stretch",
    ):
        try:
            preflight = api.preflight_run(preflight_payload)
        except ApiError as error:
            st.session_state.pop("_evalforge_run_preflight", None)
            render_api_error(error, title="Server preflight did not pass")
        else:
            st.session_state["_evalforge_run_preflight"] = {
                "signature": signature,
                "data": preflight,
                "idempotency_key": str(uuid4()),
            }

    preflight_state = st.session_state.get("_evalforge_run_preflight")
    preflight_data = preflight_state.get("data", {}) if isinstance(preflight_state, dict) else {}
    preflight_ready = (
        isinstance(preflight_state, dict)
        and preflight_state.get("signature") == signature
        and isinstance(preflight_state.get("idempotency_key"), str)
    )
    unknown_pricing_models = _unknown_pricing_models(preflight_data) if preflight_ready else []
    requires_unknown_cost_ack = bool(has_real_provider and unknown_pricing_models)
    acknowledged_unknown_cost = False
    if preflight_ready:
        _render_server_estimates(preflight_data)
        _render_metric_coverage(preflight_data, case_count=case_count)
        if requires_unknown_cost_ack:
            st.warning(
                "The known-cost estimate is partial because these real-provider models do not "
                "have complete pricing metadata.",
                icon=":material/price_check:",
            )
            for model_name in unknown_pricing_models:
                st.text(f"Unpriced model: {model_name}")
            preflight_id = (
                str(preflight_state.get("idempotency_key"))
                if isinstance(preflight_state, dict)
                else ""
            )
            acknowledged_unknown_cost = st.checkbox(
                "I understand some selected models have unknown pricing and actual charges may "
                "be higher.",
                value=False,
                key=f"_evalforge_unknown_cost_ack_{preflight_id}",
            )
            if not acknowledged_unknown_cost:
                st.warning(
                    "Confirm the unknown-pricing disclosure before submission.",
                    icon=":material/warning:",
                )
        if not requires_unknown_cost_ack or acknowledged_unknown_cost:
            st.success("Setup checked. This evaluation is ready to start.")
        with st.expander("Technical details", icon=":material/data_object:"):
            safe_json_panel("Validated plan", preflight_data)
    elif not blockers:
        st.info("Check the setup to unlock the evaluation.", icon=":material/policy:")

    payload = {
        **preflight_payload,
        "acknowledge_unknown_cost": bool(requires_unknown_cost_ack and acknowledged_unknown_cost),
    }
    submitted = st.button(
        "Start evaluation",
        type="primary",
        icon=":material/play_arrow:",
        disabled=(
            bool(blockers)
            or not preflight_ready
            or (requires_unknown_cost_ack and not acknowledged_unknown_cost)
        ),
        width="stretch",
    )
    if submitted:
        idempotency_key = (
            preflight_state.get("idempotency_key") if isinstance(preflight_state, dict) else None
        )
        if not isinstance(idempotency_key, str) or not idempotency_key:
            st.session_state.pop("_evalforge_run_preflight", None)
            render_partial_state("Preflight expired. Validate the matrix again before submission.")
            return
        try:
            run = api.create_run(payload, idempotency_key=idempotency_key)
        except ApiError as error:
            render_api_error(error, title="The evaluation was not submitted")
        else:
            st.session_state.pop("_evalforge_run_preflight", None)
            run_id = resource_id(run)
            if not run_id:
                render_partial_state(
                    "The API accepted the run but did not return an identifier. Check run history."
                )
            else:
                select_run(run_id, active=True)
                st.success("Evaluation queued. Progress will appear here.")
                st.rerun()


@st.fragment(run_every="2s")
def _poll_active_run(run_id: str) -> None:
    with st.container(border=True):
        header, action = st.columns([3, 1], vertical_alignment="center")
        with header:
            st.subheader("Active evaluation")
            st.text(f"Run {run_id}")
        with action:
            if st.button("Stop polling", icon=":material/pause:", width="stretch"):
                clear_active_run()
                st.rerun()
        try:
            run = client().run(run_id)
        except ApiError as error:
            render_api_error(error, title="Live progress is temporarily unavailable")
            return
        status = str(first_value(run, "status", "state", default="unknown"))
        render_status_badge(status)
        completed = first_value(
            run,
            "completed_items",
            "completed_count",
            "progress_completed",
            default=0,
        )
        total = first_value(run, "total_items", "total_count", "progress_total", default=0)
        render_progress(completed, total, status=status)
        failed = first_value(run, "failed_items", "error_count", default=0)
        st.caption(f"Errors recorded: {format_count(failed)}")
        if is_terminal_status(status):
            clear_active_run()
            st.session_state[_LAST_FINISHED_RUN_KEY] = run_id
            if status.lower() == "completed":
                set_flash("Evaluation completed. Results are ready to inspect.")
            else:
                set_flash(
                    "Evaluation reached a terminal state. Inspect the result evidence.",
                    tone="warning",
                )
            select_run(run_id)
            st.rerun(scope="app")


def _render_finished_run_action() -> None:
    run_id = st.session_state.get(_LAST_FINISHED_RUN_KEY)
    if not isinstance(run_id, str) or not run_id:
        return
    with st.container(border=True):
        message, review, dismiss = st.columns([3, 1, 1], vertical_alignment="center")
        with message:
            st.subheader("Results are ready")
            st.caption("Review case evidence first, then compare candidates on shared cases.")
        with review:
            if st.button(
                "Review results",
                type="primary",
                icon=":material/arrow_forward:",
                width="stretch",
            ):
                select_run(run_id)
                st.session_state.pop(_LAST_FINISHED_RUN_KEY, None)
                navigate_to("run_detail")
        with dismiss:
            if st.button("Dismiss", width="stretch"):
                st.session_state.pop(_LAST_FINISHED_RUN_KEY, None)
                st.rerun()


def _render_candidate_preview(
    *,
    prompt_ids: list[str],
    model_ids: list[str],
    prompt_options: dict[str, str],
    model_options: dict[str, str],
    model_by_id: dict[str, JsonObject],
) -> None:
    rows: list[dict[str, str]] = []
    for prompt_id in prompt_ids:
        for model_id in model_ids:
            model = model_by_id.get(model_id, {})
            rows.append(
                {
                    "Role": "Baseline" if not rows else "Challenger",
                    "Prompt": prompt_options.get(prompt_id, prompt_id),
                    "Model": model_options.get(model_id, model_id),
                    "Execution": "Offline demo" if is_demo_record(model) else "External provider",
                }
            )
    if not rows:
        st.caption("Choose at least one prompt and model to build the candidate matrix.")
        return
    st.caption("Candidate matrix")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption(
        "The first prompt/model pair is the comparison baseline. Every other pair is compared "
        "with it on the same test cases."
    )


def _case_count(dataset: JsonObject) -> int:
    value = first_value(dataset, "case_count", "test_case_count", "item_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    cases = dataset.get("cases")
    return len(cases) if isinstance(cases, list) else 0


def _limit(capabilities: Any, *keys: str) -> int | None:
    if not isinstance(capabilities, dict):
        return None
    limits = capabilities.get("limits")
    source = limits if isinstance(limits, dict) else capabilities
    value = first_value(source, *keys)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _real_runs_enabled(capabilities: Any) -> bool:
    if not isinstance(capabilities, dict):
        return False
    direct = first_value(capabilities, "real_runs_enabled", "paid_runs_enabled")
    if isinstance(direct, bool):
        return direct
    providers = capabilities.get("providers")
    if isinstance(providers, dict):
        nested = providers.get("real_runs_enabled")
        if isinstance(nested, bool):
            return nested
    return False


def _unknown_pricing_models(preflight: Any) -> list[str]:
    if not isinstance(preflight, dict):
        return []
    value = preflight.get("unknown_pricing_models")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _render_metric_coverage(preflight: JsonObject, *, case_count: int) -> None:
    counts = preflight.get("inapplicable_counts")
    if not isinstance(counts, dict):
        return
    unavailable = [
        (str(name), count)
        for name, count in counts.items()
        if isinstance(count, int) and not isinstance(count, bool) and count > 0
    ]
    if not unavailable:
        st.caption("All reference- and context-dependent metrics have evidence for every case.")
        return
    st.caption("Metric coverage")
    for name, count in unavailable:
        readable = name.replace("_", " ").title()
        st.caption(
            f"{readable}: not scored for {count:,} of {case_count:,} cases because the required "
            "reference or context is missing."
        )


def _render_server_estimates(preflight: JsonObject) -> None:
    estimated_tokens = preflight.get("estimated_input_tokens")
    known_cost = preflight.get("estimated_known_cost_micro_usd")
    estimate_complete = preflight.get("cost_estimate_complete") is True
    token_limit = _limit(
        preflight,
        "max_estimated_input_tokens",
        "max_estimated_input_tokens_per_run",
    )
    cost_limit = _limit(
        preflight,
        "max_estimated_cost_micro_usd",
        "max_estimated_cost_micro_usd_per_run",
    )

    st.subheader("Server estimates")
    token_column, cost_column = st.columns(2)
    input_bound_help = (
        "Computed from rendered UTF-8 bytes plus a per-request framing margin. It is a "
        "server safety estimate, not provider-tokenizer output or billable token usage."
    )
    if token_limit is not None:
        input_bound_help = f"{input_bound_help} Server limit: {token_limit:,}."
    token_column.metric(
        "Padded UTF-8 input guard",
        format_count(estimated_tokens),
        help=input_bound_help,
    )

    cost_label = "Known-cost estimate" if estimate_complete else "Partial known-cost estimate"
    cost_help = (
        "Uses the padded input guard and configured maximum output tokens for models "
        "with recorded input and output pricing."
    )
    if not estimate_complete:
        cost_help = f"{cost_help} Models without complete pricing are excluded."
    if cost_limit is not None:
        cost_help = f"{cost_help} Server budget: {format_micro_usd(cost_limit)}."
    cost_column.metric(
        cost_label,
        format_micro_usd(known_cost),
        help=cost_help,
    )
    st.caption(
        "The input figure is a padded UTF-8 safety estimate, not measured provider tokens. "
        "The cost figure is an estimate, not a final invoice."
    )
