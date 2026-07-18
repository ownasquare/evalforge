"""Session-local state for Streamlit's top-to-bottom rerun model."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import streamlit as st

from evalforge.dashboard.client import ApiClient

DEFAULT_API_URL = "http://127.0.0.1:8000"

_CLIENT_KEY = "_evalforge_api_client"
_CLIENT_URL_KEY = "_evalforge_api_client_url"
_PAGES_KEY = "_evalforge_pages"
_SELECTED_RUN_KEY = "selected_run_id"
_ACTIVE_RUN_KEY = "active_run_id"
_FLASH_KEY = "_evalforge_flash"


def configured_api_url() -> str:
    """Read the non-secret API origin, falling back to the local service."""

    value = os.getenv("EVALFORGE_API_URL", DEFAULT_API_URL)
    try:
        return ApiClient.validate_base_url(value)
    except ValueError:
        return DEFAULT_API_URL


def initialize_state() -> None:
    defaults: dict[str, Any] = {
        _SELECTED_RUN_KEY: None,
        _ACTIVE_RUN_KEY: None,
        _FLASH_KEY: None,
        "run_filter": "all",
        "result_page": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def get_client() -> ApiClient:
    base_url = configured_api_url()
    existing = st.session_state.get(_CLIENT_KEY)
    existing_url = st.session_state.get(_CLIENT_URL_KEY)
    if isinstance(existing, ApiClient) and existing_url == base_url:
        return existing
    if isinstance(existing, ApiClient):
        existing.close()
    client = ApiClient(base_url)
    st.session_state[_CLIENT_KEY] = client
    st.session_state[_CLIENT_URL_KEY] = base_url
    return client


def reconnect_client() -> ApiClient:
    existing = st.session_state.pop(_CLIENT_KEY, None)
    st.session_state.pop(_CLIENT_URL_KEY, None)
    if isinstance(existing, ApiClient):
        existing.close()
    return get_client()


def register_pages(pages: Mapping[str, Any]) -> None:
    st.session_state[_PAGES_KEY] = dict(pages)


def navigate_to(page_key: str) -> None:
    pages = st.session_state.get(_PAGES_KEY, {})
    page = pages.get(page_key) if isinstance(pages, dict) else None
    if page is not None:
        st.switch_page(page)


def select_run(run_id: str, *, active: bool = False) -> None:
    st.session_state[_SELECTED_RUN_KEY] = run_id
    if active:
        st.session_state[_ACTIVE_RUN_KEY] = run_id


def selected_run_id() -> str | None:
    value = st.session_state.get(_SELECTED_RUN_KEY)
    return value if isinstance(value, str) and value else None


def active_run_id() -> str | None:
    value = st.session_state.get(_ACTIVE_RUN_KEY)
    return value if isinstance(value, str) and value else None


def clear_active_run() -> None:
    st.session_state[_ACTIVE_RUN_KEY] = None


def set_flash(message: str, *, tone: str = "success") -> None:
    st.session_state[_FLASH_KEY] = {"message": message, "tone": tone}


def pop_flash() -> dict[str, str] | None:
    value = st.session_state.get(_FLASH_KEY)
    st.session_state[_FLASH_KEY] = None
    if not isinstance(value, dict):
        return None
    message = value.get("message")
    tone = value.get("tone")
    if not isinstance(message, str) or not isinstance(tone, str):
        return None
    return {"message": message, "tone": tone}
