"""Backend readiness, public capabilities, and evaluation contract settings."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from evalforge.dashboard import state
from evalforge.dashboard.auth import configured_auth, safe_markdown_text
from evalforge.dashboard.client import ApiClient, ApiError, collection_items, public_payload
from evalforge.dashboard.components import (
    first_value,
    page_header,
    render_api_error,
    render_partial_state,
    render_status_badge,
    safe_json_panel,
)
from evalforge.dashboard.pages.common import client, load_resource
from evalforge.dashboard.state import configured_api_url, reconnect_client


def render() -> None:
    page_header(
        "Settings",
        "Review your workspace and the model providers available for evaluations.",
        eyebrow="System",
    )
    _render_account_and_workspace()
    api = client()
    live, live_error = load_resource("liveness", api.health_live)
    ready, ready_error = load_resource("readiness", api.health_ready)
    capabilities, capability_error = load_resource("capabilities", api.capabilities)

    if live_error:
        render_api_error(live_error, title="The API process is not reachable")
    elif ready_error:
        render_api_error(ready_error, title="The API is live but not ready")

    if capability_error:
        render_partial_state("Public provider, metric, and limit metadata is unavailable.")
        safe_capabilities: dict[str, Any] = {}
    elif not isinstance(capabilities, dict):
        render_partial_state("The capabilities endpoint returned an unexpected payload.")
        safe_capabilities = {}
    else:
        safe_capabilities = public_payload(capabilities)

    if safe_capabilities:
        _render_commercial_offer(api, safe_capabilities)
        _render_provider_capabilities(safe_capabilities)
        _render_provider_safety(safe_capabilities)

    with st.expander("Advanced system details", icon=":material/settings:"):
        _render_backend_connection(
            live=live,
            live_error=live_error,
            ready=ready,
            ready_error=ready_error,
        )
        st.caption(
            "Only API-published metadata appears here. Provider keys, database URLs, and "
            "credentials are never requested or rendered."
        )
        if safe_capabilities:
            _render_metric_versions(safe_capabilities)
            _render_limits(safe_capabilities)
            _render_executor_notes(safe_capabilities)
            with st.expander("Published capability payload", icon=":material/data_object:"):
                safe_json_panel("API-published metadata", safe_capabilities)


def _render_commercial_offer(api: ApiClient, capabilities: dict[str, Any]) -> None:
    commercial = capabilities.get("commercial")
    if not isinstance(commercial, dict):
        return

    st.subheader("Plans and hosted pilot")
    st.caption(
        "Community self-hosted remains the complete open-source workflow. The hosted pilot "
        "adds a managed shared workspace, persistence, team access, and pilot support."
    )
    plans, plans_error = load_resource("commercial plans", api.commercial_plans)
    entitlement, entitlement_error = load_resource(
        "workspace access",
        api.commercial_entitlement,
    )
    if plans_error or entitlement_error:
        render_partial_state(
            "The hosted offer is visible, but current workspace access could not be read back."
        )
        return
    plan_rows = collection_items(plans)
    if not isinstance(entitlement, dict) or not plan_rows:
        render_partial_state("The commercial service returned an unexpected payload.")
        return

    _render_plan_cards(plan_rows)
    _render_entitlement_readback(entitlement, plan_rows)

    pilot_enabled = commercial.get("pilot_enabled") is True
    hosted = commercial.get("hosted") is True
    if not hosted or not pilot_enabled:
        st.info(
            "This workspace is using the free self-hosted edition. Hosted pilot actions are "
            "available only on an enabled shared deployment.",
            icon=":material/code:",
        )
        return

    _record_upgrade_view(api)
    if state.commercial_tracking_unavailable():
        render_partial_state(
            "Pilot analytics are temporarily unavailable. Workspace access is unaffected."
        )

    if not state.can_admin():
        st.caption("A workspace owner or administrator can manage hosted access.")
        return

    _render_trial_actions(api, entitlement, commercial)
    _render_team_request(api)
    _render_commercial_history(api)


def _render_plan_cards(plans: list[dict[str, Any]]) -> None:
    columns = st.columns(len(plans))
    for column, plan in zip(columns, plans, strict=False):
        with column.container(border=True):
            st.markdown(f"**{safe_markdown_text(str(plan.get('name', 'Plan')))}**")
            st.caption(safe_markdown_text(str(plan.get("price_label", ""))))
            st.write(safe_markdown_text(str(plan.get("audience", ""))))
            features = plan.get("features")
            if isinstance(features, list):
                for feature in features:
                    if isinstance(feature, str):
                        st.markdown(f"- {safe_markdown_text(feature)}")
            if plan.get("available") is False and plan.get("self_hosted") is not True:
                st.caption("Available only when the hosted pilot is enabled.")


def _render_entitlement_readback(
    entitlement: dict[str, Any],
    plans: list[dict[str, Any]],
) -> None:
    plan_code = str(entitlement.get("plan_code", "open_source"))
    plan_name = next(
        (str(plan.get("name")) for plan in plans if plan.get("code") == plan_code),
        plan_code.replace("_", " ").title(),
    )
    with st.container(border=True):
        st.caption("Current workspace access · server readback")
        columns = st.columns(4)
        columns[0].metric("Plan", plan_name)
        columns[1].metric("Status", str(entitlement.get("status", "unknown")).title())
        columns[2].metric("Seat limit", _nonnegative_number(entitlement.get("seat_limit")))
        columns[3].metric(
            "Active members",
            _nonnegative_number(entitlement.get("active_memberships")),
        )
        period_end = entitlement.get("current_period_end")
        if isinstance(period_end, str) and period_end:
            st.caption(f"Current access period ends {safe_markdown_text(period_end[:16])} UTC.")
        if entitlement.get("can_start_runs") is not True:
            st.warning(
                "New hosted evaluations are paused until workspace access is activated.",
                icon=":material/lock_clock:",
            )


def _render_trial_actions(
    api: ApiClient,
    entitlement: dict[str, Any],
    commercial: dict[str, Any],
) -> None:
    plan_code = str(entitlement.get("plan_code", "open_source"))
    status = str(entitlement.get("status", "active"))
    trial_days = _nonnegative_number(commercial.get("trial_days"))
    if plan_code == "open_source":
        if st.button(
            f"Start {trial_days}-day hosted trial",
            type="primary",
            icon=":material/rocket_launch:",
        ):
            try:
                api.start_hosted_trial(idempotency_key=state.commercial_event_key("trial-start"))
            except ApiError as error:
                render_api_error(error, title="The hosted trial could not be started")
            else:
                state.set_flash("Hosted trial activated from server readback.")
                st.rerun()
    elif plan_code == "hosted_trial" and status == "trialing":
        st.caption("Canceling stops new evaluations; existing results and exports remain readable.")
        if st.button("Cancel hosted trial", icon=":material/cancel:"):
            try:
                api.cancel_hosted_trial(idempotency_key=state.commercial_event_key("trial-cancel"))
            except ApiError as error:
                render_api_error(error, title="The hosted trial could not be canceled")
            else:
                state.set_flash("Hosted trial canceled. Existing evidence remains available.")
                st.rerun()


def _render_team_request(api: ApiClient) -> None:
    funnel, funnel_error = load_resource("commercial funnel", api.commercial_funnel)
    requests, request_error = load_resource("team pilot requests", api.team_pilot_requests)
    activated_runs = (
        funnel.get("activated_runs", 0)
        if isinstance(funnel, dict) and not isinstance(funnel.get("activated_runs"), bool)
        else 0
    )
    total_team_requests = (
        funnel.get("total_team_requests", 0)
        if isinstance(funnel, dict) and not isinstance(funnel.get("total_team_requests"), bool)
        else 0
    )
    if funnel_error or request_error:
        render_partial_state("Team-request readback is temporarily unavailable.")
        return

    request_rows = collection_items(requests)
    pending = [request for request in request_rows if request.get("status") == "pending"]
    if pending:
        st.markdown("**Team pilot requests**")
        for request in pending:
            request_id = str(request.get("id", ""))
            with st.container(border=True):
                columns = st.columns([3, 1])
                columns[0].write(
                    f"{_nonnegative_number(request.get('requested_seats'))} seats · "
                    f"{str(request.get('evaluation_frequency', '')).replace('_', ' ')}"
                )
                if request.get("security_review_required") is True:
                    columns[0].caption("Security review requested")
                if request_id and columns[1].button(
                    "Cancel request",
                    key=f"_evalforge_cancel_team_request_{request_id}",
                ):
                    try:
                        api.cancel_team_pilot_request(
                            request_id,
                            idempotency_key=state.commercial_event_key(
                                "request-cancel",
                                source_scope=request_id,
                            ),
                        )
                    except ApiError as error:
                        render_api_error(error, title="The team request could not be canceled")
                    else:
                        state.set_flash("Pending team request canceled.")
                        st.rerun()

    st.markdown("**Request a hosted team workspace**")
    st.caption("This pilot uses a qualified team request, not live checkout. No card is charged.")
    if pending:
        st.caption("Cancel the pending request before submitting a replacement.")
        return
    if not isinstance(activated_runs, int) or activated_runs < 1:
        st.info(
            "Complete one hosted evaluation before requesting a paid team workspace.",
            icon=":material/check_circle:",
        )
        return
    with st.form("_evalforge_team_pilot_request_form", border=True):
        requested_seats = st.number_input(
            "Team seats",
            min_value=2,
            max_value=250,
            value=5,
            step=1,
        )
        frequency_labels = {
            "weekly": "Weekly",
            "several_times_week": "Several times a week",
            "daily": "Daily",
            "release_driven": "Around releases",
        }
        frequency_options = tuple(frequency_labels)
        evaluation_frequency = st.selectbox(
            "Evaluation frequency",
            options=frequency_options,
            index=0,
            format_func=lambda value: frequency_labels[value],
        )
        security_review_required = st.checkbox("We require a security review")
        submitted = st.form_submit_button(
            "Request team pilot",
            type="primary",
            icon=":material/groups:",
        )
    if submitted:
        try:
            api.create_team_pilot_request(
                {
                    "requested_seats": int(requested_seats),
                    "evaluation_frequency": evaluation_frequency,
                    "security_review_required": security_review_required,
                },
                idempotency_key=state.commercial_event_key(
                    f"team-request-{_nonnegative_number(total_team_requests)}"
                ),
            )
        except ApiError as error:
            render_api_error(error, title="The team request could not be submitted")
        else:
            state.set_flash("Team request submitted as pending. No payment was taken.")
            st.rerun()


def _render_commercial_history(api: ApiClient) -> None:
    events, error = load_resource("hosted access history", api.commercial_billing_events)
    if error:
        render_partial_state("Hosted access history is temporarily unavailable.")
        return
    rows = collection_items(events)
    with st.expander("Hosted access history", icon=":material/receipt_long:"):
        if not rows:
            st.caption("No hosted access changes have been recorded.")
            return
        safe_rows = [
            {
                "Change": str(row.get("event_type", "unknown")).replace("_", " ").title(),
                "Source": str(row.get("provider", "unknown")).replace("_", " ").title(),
                "Recorded": str(row.get("created_at", ""))[:16],
            }
            for row in rows[:20]
        ]
        st.dataframe(pd.DataFrame(safe_rows), hide_index=True, width="stretch")


def _record_upgrade_view(api: ApiClient) -> None:
    idempotency_key = state.commercial_event_key("upgrade-view")
    if state.commercial_event_recorded(idempotency_key):
        return
    try:
        api.record_activation_event(
            "upgrade_view",
            source=state.commercial_acquisition_source(),
            surface="settings",
            idempotency_key=idempotency_key,
        )
    except ApiError:
        state.mark_commercial_tracking_unavailable()
    else:
        state.mark_commercial_event_recorded(idempotency_key)


def _nonnegative_number(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _render_backend_connection(
    *,
    live: Any,
    live_error: Any,
    ready: Any,
    ready_error: Any,
) -> None:
    st.subheader("Backend connection")
    st.caption("Configured API origin")
    st.code(configured_api_url(), language=None)
    columns = st.columns(3)
    with columns[0]:
        render_status_badge("offline" if live_error else _health_status(live), prefix="Liveness")
    with columns[1]:
        render_status_badge("offline" if ready_error else _health_status(ready), prefix="Readiness")
    with columns[2]:
        if st.button("Reconnect", icon=":material/refresh:", width="stretch"):
            reconnect_client().clear_cache()
            st.rerun()


def _render_provider_capabilities(capabilities: dict[str, Any]) -> None:
    st.subheader("Provider capability")
    provider_payload = first_value(
        capabilities,
        "providers",
        "model_providers",
        "provider_capabilities",
        default=[],
    )
    if isinstance(provider_payload, dict) and not collection_items(provider_payload):
        provider_rows = _provider_mapping_rows(provider_payload)
        proof = capabilities.get("proof")
        proof_mapping = proof if isinstance(proof, dict) else {}
        provider_rows.insert(
            0,
            {
                "Provider": "Deterministic offline",
                "Available": bool(proof_mapping.get("demo_mode")),
                "Configured": True,
                "Models": "Built in",
                "Confirmation": False,
            },
        )
        if provider_rows:
            st.dataframe(pd.DataFrame(provider_rows), hide_index=True, width="stretch")
            return
    providers = (
        collection_items(provider_payload)
        if isinstance(provider_payload, dict)
        else provider_payload
    )
    if isinstance(providers, list) and providers:
        rows: list[dict[str, Any]] = []
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            rows.append(
                {
                    "Provider": first_value(provider, "name", "provider", default="Provider"),
                    "Available": first_value(provider, "available", "enabled", default=False),
                    "Mode": first_value(provider, "api_mode", "mode", default="—"),
                    "Models": _count_or_list(first_value(provider, "models", "model_count")),
                    "Confirmation": first_value(
                        provider, "requires_confirmation", "paid", default=False
                    ),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        proof = capabilities.get("proof")
        proof_mapping = proof if isinstance(proof, dict) else {}
        deterministic = bool(
            first_value(
                capabilities,
                "demo_available",
                "deterministic_available",
                default=proof_mapping.get("demo_mode"),
            )
        )
        provider_mapping = provider_payload if isinstance(provider_payload, dict) else {}
        real_enabled = bool(
            first_value(
                capabilities,
                "real_runs_enabled",
                default=provider_mapping.get("real_runs_enabled", False),
            )
        )
        rows = [
            {"Capability": "Deterministic offline", "Available": deterministic},
            {"Capability": "Real-provider execution", "Available": real_enabled},
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_metric_versions(capabilities: dict[str, Any]) -> None:
    st.subheader("Metric registry")
    payload = first_value(capabilities, "metrics", "metric_versions", "metric_registry", default=[])
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for name, value in payload.items():
            if isinstance(value, dict):
                rows.append({"Metric": name, **value})
            else:
                rows.append({"Metric": name, "Version": value})
    elif isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
    if rows:
        frame = pd.DataFrame(rows)
        st.dataframe(frame, hide_index=True, width="stretch")
    else:
        st.caption("Metric versions will appear after the API publishes its registry.")
    st.caption(
        "Correctness and groundedness remain not applicable when their required evidence is absent."
    )


def _render_provider_safety(capabilities: dict[str, Any]) -> None:
    safety = capabilities.get("provider_safety")
    if not isinstance(safety, dict):
        return
    st.caption(
        "Real-provider runs require separate approval for external data transfer and provider "
        "cost, plus a user-selected estimated-spend ceiling. The ceiling is checked against "
        "the known-price preflight estimate; it is not a provider billing limit or final invoice."
    )


def _render_limits(capabilities: dict[str, Any]) -> None:
    st.subheader("Execution limits")
    limits = capabilities.get("limits", {})
    if not isinstance(limits, dict) or not limits:
        st.caption("No public execution limits were returned.")
        return
    rows = [
        {"Limit": str(key).replace("_", " ").title(), "Value": value}
        for key, value in limits.items()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_executor_notes(capabilities: dict[str, Any]) -> None:
    executor = capabilities.get("executor", {})
    executor_type = (
        first_value(executor, "type", "name", default="local in-process")
        if isinstance(executor, dict)
        else executor
    )
    st.subheader("Executor boundary")
    st.info(
        "Evaluation work is claimed with database leases, so multiple worker processes can "
        "coordinate safely. Provider calls cannot be made exactly once: if a lease expires "
        "after an external request begins, the item is marked billing ambiguous and is not "
        "replayed automatically.",
        icon=":material/info:",
    )
    st.caption("Published executor type")
    st.text(str(executor_type))


def _health_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    return str(first_value(payload, "status", "state", default="healthy"))


def _count_or_list(value: Any) -> str:
    if isinstance(value, list):
        return str(len(value))
    return str(value) if value is not None else "—"


def _provider_mapping_rows(providers: dict[str, Any]) -> list[dict[str, Any]]:
    real_enabled = bool(providers.get("real_runs_enabled", False))
    rows: list[dict[str, Any]] = []
    for name, value in providers.items():
        if name == "real_runs_enabled" or not isinstance(value, dict):
            continue
        rows.append(
            {
                "Provider": name.replace("_", " ").title(),
                "Available": bool(value.get("configured", False)) and real_enabled,
                "Configured": bool(value.get("configured", False)),
                "Models": _count_or_list(value.get("models")),
                "Confirmation": True,
            }
        )
    return rows


def _render_account_and_workspace() -> None:
    st.subheader("Account and workspace")
    auth_config = configured_auth()
    if auth_config.mode == "local":
        columns = st.columns(2)
        with columns[0]:
            st.caption("Workspace")
            st.markdown("**Local workspace**")
        with columns[1]:
            st.caption("Access")
            st.markdown("**Owner**")
        st.caption("This private workspace is available only from the local EvalForge service.")
        return

    account = state.account_context()
    workspace = state.workspace_context()
    workspaces = state.available_workspaces()
    columns = st.columns(2)
    with columns[0]:
        st.caption("Account")
        account_name = account.display_name if account else "Signed-in user"
        st.markdown(f"**{safe_markdown_text(account_name)}**")
        if account is not None and account.email:
            st.caption(safe_markdown_text(account.email))
    with columns[1]:
        st.caption("Current workspace")
        workspace_name = workspace.name if workspace else "Not selected"
        st.markdown(f"**{safe_markdown_text(workspace_name)}**")
        if workspace is not None:
            st.caption(workspace.role.title())

    if workspaces and workspace is not None:
        current_index = next(
            (index for index, option in enumerate(workspaces) if option.id == workspace.id),
            0,
        )
        choice = st.selectbox(
            "Switch workspace",
            options=workspaces,
            index=current_index,
            format_func=lambda option: option.name,
            key="_evalforge_settings_workspace_choice",
        )
        if st.button(
            "Switch workspace",
            disabled=choice.id == workspace.id,
            icon=":material/swap_horiz:",
        ):
            state.select_workspace(choice)
            st.rerun()

    if st.button("Sign out", icon=":material/logout:"):
        state.clear_identity()
        st.logout()
    st.divider()
