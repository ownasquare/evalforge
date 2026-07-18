"""Session-local state for Streamlit's top-to-bottom rerun model."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import streamlit as st

from evalforge.config import get_settings
from evalforge.dashboard.auth import DashboardAccount, WorkspaceOption
from evalforge.dashboard.client import ApiClient

_CLIENT_KEY = "_evalforge_api_client"
_CLIENT_SCOPE_KEY = "_evalforge_api_client_scope"
_PAGES_KEY = "_evalforge_pages"
_IDENTITY_KEY = "_evalforge_identity_fingerprint"
_ACCOUNT_KEY = "_evalforge_account"
_WORKSPACE_KEY = "_evalforge_workspace"
_WORKSPACES_KEY = "_evalforge_workspaces"
_REAUTHENTICATION_KEY = "_evalforge_reauthentication_required"
_SELECTED_RUN_KEY = "selected_run_id"
_ACTIVE_RUN_KEY = "active_run_id"
_FLASH_KEY = "_evalforge_flash"

_RESOURCE_KEYS_TO_DROP = frozenset(
    {
        "_evalforge_run_preflight",
        "_evalforge_last_finished_run_id",
    }
)
_RESOURCE_PREFIXES_TO_DROP = (
    "_evalforge_settings_",
    "_evalforge_run_export_",
    "_evalforge_unknown_cost_ack_",
    "export-data-",
    "export-format-",
    "prepare-export-",
    "download-export-",
)


def configured_api_url() -> str:
    """Return the API origin from the same validated settings used by the backend."""

    return ApiClient.validate_base_url(str(get_settings().api_url))


def initialize_state() -> None:
    defaults: dict[str, Any] = {
        _SELECTED_RUN_KEY: None,
        _ACTIVE_RUN_KEY: None,
        _FLASH_KEY: None,
        _IDENTITY_KEY: "local",
        _WORKSPACE_KEY: None,
        _WORKSPACES_KEY: (),
        _ACCOUNT_KEY: None,
        _REAUTHENTICATION_KEY: False,
        "run_filter": "all",
        "result_page": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def configure_client(
    *,
    identity_fingerprint: str,
    workspace_id: str | None = None,
    access_token_provider: Callable[[], str | None] | None = None,
) -> ApiClient:
    """Return the API client scoped to one identity and workspace."""

    base_url = configured_api_url()
    scope = (base_url, identity_fingerprint, workspace_id)
    existing = st.session_state.get(_CLIENT_KEY)
    existing_scope = st.session_state.get(_CLIENT_SCOPE_KEY)
    if isinstance(existing, ApiClient) and existing_scope == scope:
        return existing
    if isinstance(existing, ApiClient):
        existing.close()
    client = ApiClient(
        base_url,
        access_token_provider=access_token_provider,
        identity_fingerprint=identity_fingerprint,
        workspace_id=workspace_id,
        on_unauthorized=mark_reauthentication_required,
    )
    st.session_state[_CLIENT_KEY] = client
    st.session_state[_CLIENT_SCOPE_KEY] = scope
    return client


def get_client() -> ApiClient:
    existing = st.session_state.get(_CLIENT_KEY)
    if isinstance(existing, ApiClient):
        return existing
    fingerprint = st.session_state.get(_IDENTITY_KEY, "local")
    if not isinstance(fingerprint, str) or fingerprint != "local":
        raise RuntimeError("The authenticated API client has not been configured")
    return configure_client(identity_fingerprint="local")


def reconnect_client() -> ApiClient:
    existing = st.session_state.get(_CLIENT_KEY)
    if isinstance(existing, ApiClient):
        existing.clear_cache()
        return existing
    return get_client()


def sync_identity(identity_fingerprint: str) -> None:
    """Clear all scoped state when the signed-in principal changes."""

    current = st.session_state.get(_IDENTITY_KEY)
    if current == identity_fingerprint:
        return
    _discard_client()
    _clear_resource_state()
    st.session_state[_IDENTITY_KEY] = identity_fingerprint
    st.session_state[_ACCOUNT_KEY] = None
    st.session_state[_WORKSPACE_KEY] = None
    st.session_state[_WORKSPACES_KEY] = ()
    st.session_state[_REAUTHENTICATION_KEY] = False


def clear_identity() -> None:
    """Discard authenticated state before signing out."""

    _discard_client()
    _clear_resource_state()
    st.session_state[_IDENTITY_KEY] = "local"
    st.session_state[_ACCOUNT_KEY] = None
    st.session_state[_WORKSPACE_KEY] = None
    st.session_state[_WORKSPACES_KEY] = ()
    st.session_state[_REAUTHENTICATION_KEY] = False


def set_account_context(account: DashboardAccount | None) -> None:
    st.session_state[_ACCOUNT_KEY] = account


def account_context() -> DashboardAccount | None:
    value = st.session_state.get(_ACCOUNT_KEY)
    return value if isinstance(value, DashboardAccount) else None


def set_available_workspaces(workspaces: Sequence[WorkspaceOption]) -> None:
    """Refresh memberships and revoke a stale selected workspace immediately."""

    normalized = tuple(workspaces)
    st.session_state[_WORKSPACES_KEY] = normalized
    selected = workspace_context()
    if selected is None:
        return
    current = next((workspace for workspace in normalized if workspace.id == selected.id), None)
    if current != selected:
        select_workspace(current)


def available_workspaces() -> tuple[WorkspaceOption, ...]:
    value = st.session_state.get(_WORKSPACES_KEY, ())
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, WorkspaceOption) for item in value
    ):
        return tuple(value)
    return ()


def select_workspace(workspace: WorkspaceOption | None) -> None:
    current = workspace_context()
    if current == workspace:
        return
    _discard_client()
    _clear_resource_state()
    st.session_state[_WORKSPACE_KEY] = workspace


def selected_workspace_id() -> str | None:
    workspace = workspace_context()
    return workspace.id if workspace is not None else None


def workspace_context() -> WorkspaceOption | None:
    value = st.session_state.get(_WORKSPACE_KEY)
    return value if isinstance(value, WorkspaceOption) else None


def can_edit() -> bool:
    """Return whether the current workspace role can mutate evaluation resources."""

    if st.session_state.get(_IDENTITY_KEY, "local") == "local":
        return True
    workspace = workspace_context()
    return workspace is not None and workspace.role in {"owner", "admin", "editor"}


def mark_reauthentication_required() -> None:
    """Handle API 401 without retaining workspace-scoped evidence."""

    _discard_client()
    _clear_resource_state()
    st.session_state[_ACCOUNT_KEY] = None
    st.session_state[_WORKSPACE_KEY] = None
    st.session_state[_WORKSPACES_KEY] = ()
    st.session_state[_REAUTHENTICATION_KEY] = True


def reauthentication_required() -> bool:
    return st.session_state.get(_REAUTHENTICATION_KEY) is True


def clear_reauthentication_required() -> None:
    st.session_state[_REAUTHENTICATION_KEY] = False


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


def _discard_client() -> None:
    existing = st.session_state.pop(_CLIENT_KEY, None)
    st.session_state.pop(_CLIENT_SCOPE_KEY, None)
    if isinstance(existing, ApiClient):
        existing.close()


def _clear_resource_state() -> None:
    for key in list(st.session_state):
        if key in _RESOURCE_KEYS_TO_DROP or (
            isinstance(key, str) and key.startswith(_RESOURCE_PREFIXES_TO_DROP)
        ):
            st.session_state.pop(key, None)
    st.session_state[_SELECTED_RUN_KEY] = None
    st.session_state[_ACTIVE_RUN_KEY] = None
    st.session_state[_FLASH_KEY] = None
    st.session_state["run_filter"] = "all"
    st.session_state["result_page"] = 0
    st.session_state.pop(_PAGES_KEY, None)
