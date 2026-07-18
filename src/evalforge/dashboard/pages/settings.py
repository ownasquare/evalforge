"""Backend readiness, public capabilities, and evaluation contract settings."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from evalforge.dashboard import state
from evalforge.dashboard.auth import configured_auth, safe_markdown_text
from evalforge.dashboard.client import collection_items, public_payload
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
        "Review connection health, execution limits, and the published scoring contract.",
        eyebrow="System",
    )
    _render_account_and_workspace()
    api = client()
    live, live_error = load_resource("liveness", api.health_live)
    ready, ready_error = load_resource("readiness", api.health_ready)
    capabilities, capability_error = load_resource("capabilities", api.capabilities)

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

    if live_error:
        render_api_error(live_error, title="The API process is not reachable")
    elif ready_error:
        render_api_error(ready_error, title="The API is live but not ready")

    st.info(
        "Security boundary · This page shows only API-published capability metadata. "
        "Provider keys, database URLs, and credentials are neither requested nor rendered.",
        icon=":material/lock:",
    )

    if capability_error:
        render_partial_state("Public provider, metric, and limit metadata is unavailable.")
        return
    if not isinstance(capabilities, dict):
        render_partial_state("The capabilities endpoint returned an unexpected payload.")
        return

    safe_capabilities = public_payload(capabilities)
    _render_provider_capabilities(safe_capabilities)
    _render_provider_safety(safe_capabilities)
    _render_metric_versions(safe_capabilities)
    _render_limits(safe_capabilities)
    _render_executor_notes(safe_capabilities)

    with st.expander("Safe capability payload", icon=":material/data_object:"):
        safe_json_panel("API-published metadata", safe_capabilities)


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
