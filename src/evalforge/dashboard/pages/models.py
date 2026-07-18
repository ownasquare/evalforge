"""Compact, server-governed model profile setup."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from evalforge.dashboard.client import ApiError, public_payload
from evalforge.dashboard.components import (
    first_value,
    page_header,
    render_api_error,
    render_empty_state,
    render_partial_state,
    resource_id,
    resource_label,
    safe_json_panel,
)
from evalforge.dashboard.pages.common import client, list_payload, load_resource
from evalforge.dashboard.state import can_admin


def render() -> None:
    page_header(
        "Models",
        "Choose which server-approved models are available in evaluations.",
        eyebrow="Library",
    )
    st.caption(
        "Provider access stays on the API server. This dashboard never asks for or displays keys."
    )

    api = client()
    models_payload, models_error = load_resource("model profiles", api.models)
    if models_error:
        render_api_error(models_error, title="Model profiles are unavailable")
        return
    profiles = list_payload(models_payload)
    _render_profile_list(profiles)

    capabilities, capabilities_error = load_resource("model capabilities", api.capabilities)
    safe_capabilities = public_payload(capabilities) if isinstance(capabilities, dict) else {}
    if capabilities_error:
        render_partial_state(
            "Existing profiles are available, but the server-approved model list could not "
            "be loaded."
        )

    if not can_admin():
        st.info(
            "Workspace owners and admins can add or pause model profiles.",
            icon=":material/lock:",
        )
        return

    if profiles:
        _render_availability_editor(api, profiles)
    if not capabilities_error:
        _render_create_form(api, safe_capabilities, profiles)


def _render_profile_list(profiles: list[dict[str, Any]]) -> None:
    st.subheader("Model profiles")
    if not profiles:
        render_empty_state(
            "No model profiles yet",
            "Add a model from the server-approved list below.",
            icon=":material/model_training:",
        )
        return

    rows = [
        {
            "Profile": resource_label(profile, fallback="Model profile"),
            "Provider": _provider_label(first_value(profile, "provider")),
            "Model": first_value(profile, "model_name", default="—"),
            "Connection": _api_mode_label(first_value(profile, "api_mode")),
            "Available": bool(first_value(profile, "enabled", default=False)),
        }
        for profile in profiles
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with st.expander("Profile details", icon=":material/info:"):
        st.caption(
            "Connection mode is selected by the server-approved provider setup. Generation "
            "settings are retained for troubleshooting and extensions."
        )
        safe_json_panel("Stored model profiles", public_payload(profiles))


def _render_availability_editor(api: Any, profiles: list[dict[str, Any]]) -> None:
    with st.expander("Manage availability", icon=":material/tune:"):
        profile_by_id = {
            resource_id(profile): profile for profile in profiles if resource_id(profile)
        }
        if not profile_by_id:
            return
        model_id = st.selectbox(
            "Profile to manage",
            options=list(profile_by_id),
            format_func=lambda value: resource_label(profile_by_id[value]),
            help="Paused profiles stay in past results but cannot be selected for new evaluations.",
        )
        profile = profile_by_id[model_id]
        current_enabled = bool(first_value(profile, "enabled", default=False))
        enabled = st.checkbox(
            "Available for evaluations",
            value=current_enabled,
            key=f"model-enabled-{model_id}",
        )
        if st.button(
            "Save availability",
            disabled=enabled == current_enabled,
            key=f"save-model-{model_id}",
        ):
            try:
                api.update_model(model_id, {"enabled": enabled})
            except ApiError as error:
                render_api_error(error, title="The model profile could not be updated")
            else:
                st.toast("Model availability updated.", icon=":material/check_circle:")
                st.rerun()


def _render_create_form(
    api: Any,
    capabilities: dict[str, Any],
    profiles: list[dict[str, Any]],
) -> None:
    configured = _allowed_model_options(capabilities)
    existing_pairs = {
        (str(first_value(profile, "provider", default="")), str(profile.get("model_name", "")))
        for profile in profiles
    }
    options = [
        option
        for option in configured
        if (option["provider"], option["model_name"]) not in existing_pairs
    ]

    st.subheader("Add a model")
    if not options:
        st.info(
            "The offline profiles are ready. To add a real model, an administrator must first "
            "connect its provider and approve which models this workspace may use.",
            icon=":material/info:",
        )
        return

    option_by_key = {option["key"]: option for option in options}
    with st.form("create-model-profile", border=True):
        name = st.text_input(
            "Profile name",
            placeholder="Release candidate",
            help="Use a short name teammates will recognize when starting an evaluation.",
        )
        option_key = st.selectbox(
            "Server-approved model",
            options=list(option_by_key),
            format_func=lambda value: _option_label(option_by_key[value]),
            help=(
                "Only configured providers and allowlisted models published by the API appear "
                "here. The API connection mode is chosen by that server-approved provider setup."
            ),
        )
        description = st.text_area(
            "Description (optional)",
            placeholder="What this profile is used to evaluate",
        )
        submitted = st.form_submit_button(
            "Add model profile",
            type="primary",
            disabled=not name.strip(),
        )
    if not submitted:
        return
    try:
        api.create_model(
            _profile_payload(
                name=name,
                description=description,
                option=option_by_key[option_key],
            )
        )
    except ApiError as error:
        render_api_error(error, title="The model profile could not be added")
    else:
        st.toast(
            "Model profile added. It is now available in new evaluations.",
            icon=":material/check_circle:",
        )
        st.rerun()


def _allowed_model_options(capabilities: dict[str, Any]) -> list[dict[str, str]]:
    providers = capabilities.get("providers")
    if not isinstance(providers, dict) or not providers.get("real_runs_enabled"):
        return []

    definitions = (
        ("openai", "openai", "OpenAI", "responses"),
        ("openai_compatible", "openai-compatible", "OpenAI-compatible", "chat_completions"),
    )
    options: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for capability_key, provider, provider_label, api_mode in definitions:
        payload = providers.get(capability_key)
        if not isinstance(payload, dict) or payload.get("configured") is not True:
            continue
        models = payload.get("models")
        if not isinstance(models, list):
            continue
        for value in models:
            if not isinstance(value, str) or not value.strip():
                continue
            model_name = value.strip()
            pair = (provider, model_name)
            if pair in seen:
                continue
            seen.add(pair)
            options.append(
                {
                    "key": f"{provider}::{model_name}",
                    "provider": provider,
                    "provider_label": provider_label,
                    "model_name": model_name,
                    "api_mode": api_mode,
                }
            )
    return options


def _profile_payload(
    *,
    name: str,
    description: str,
    option: dict[str, str],
) -> dict[str, Any]:
    clean_description = description.strip()
    return {
        "name": name.strip(),
        "description": clean_description or None,
        "provider": option["provider"],
        "model_name": option["model_name"],
        "api_mode": option["api_mode"],
        "generation_parameters": {},
        "enabled": True,
    }


def _option_label(option: dict[str, str]) -> str:
    return (
        f"{option['provider_label']} · {option['model_name']} · "
        f"{_api_mode_label(option['api_mode'])}"
    )


def _api_mode_label(value: Any) -> str:
    labels = {
        "responses": "Responses API",
        "chat_completions": "Chat Completions API",
        "chat-completions": "Chat Completions API",
        "demo": "Offline demo",
        "deterministic": "Offline demo",
    }
    text = str(value or "")
    if not text:
        return "Server default"
    return labels.get(text, text.replace("_", " ").replace("-", " ").title())


def _provider_label(value: Any) -> str:
    labels = {
        "demo": "Offline demo",
        "deterministic": "Offline demo",
        "openai": "OpenAI",
        "openai-compatible": "OpenAI-compatible",
        "openai_compatible": "OpenAI-compatible",
    }
    text = str(value or "Unknown")
    return labels.get(text, text.replace("_", " ").replace("-", " ").title())
