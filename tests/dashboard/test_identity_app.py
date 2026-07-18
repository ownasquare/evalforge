from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

from evalforge.dashboard import app as dashboard_app
from evalforge.dashboard.auth import (
    DashboardAuthConfig,
    current_auth_context,
)
from evalforge.dashboard.client import ApiClient
from evalforge.dashboard.pages import settings as settings_page

APP_PATH = Path(__file__).parents[2] / "src" / "evalforge" / "streamlit_app.py"

SETTINGS_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.auth import DashboardAccount, WorkspaceOption
from evalforge.dashboard.pages.settings import render
from evalforge.dashboard.state import (
    configure_client,
    initialize_state,
    select_workspace,
    set_account_context,
    set_available_workspaces,
    sync_identity,
)

st.set_page_config(page_title="EvalForge account test", layout="wide")
initialize_state()
sync_identity("known-fingerprint")
account = DashboardAccount("user-1", "Morgan Lee", "morgan@example.com")
workspace = WorkspaceOption("workspace-1", "Quality team", "editor")
set_account_context(account)
set_available_workspaces([workspace])
select_workspace(workspace)
configure_client(identity_fingerprint="known-fingerprint", workspace_id=workspace.id)
render()
"""


def test_oidc_shell_requires_explicit_workspace_choice_without_storing_token(
    monkeypatch,
) -> None:
    token = "private-access-token"
    context = current_auth_context(
        DashboardAuthConfig(mode="oidc", provider="company"),
        user=SimpleNamespace(is_logged_in=True, tokens={"access": token}),
    )
    assert context is not None
    routes: dict[str, Any] = {
        "/api/v1/session": {
            "user": {
                "id": "user-1",
                "display_name": "Morgan Lee",
                "email": "morgan@example.com",
            }
        },
        "/api/v1/workspaces": {
            "items": [
                {
                    "id": "workspace-1",
                    "name": "Quality team",
                    "role": "editor",
                    "active": True,
                }
            ]
        },
        "/health/live": {"status": "healthy"},
        "/api/v1/overview": {"totals": {}, "recent_runs": []},
        "/api/v1/capabilities": {"demo_available": True, "real_runs_enabled": False},
    }
    monkeypatch.setattr(
        dashboard_app,
        "configured_auth",
        lambda: DashboardAuthConfig(mode="oidc", provider="company"),
    )
    monkeypatch.setattr(dashboard_app, "current_auth_context", lambda _: context)
    monkeypatch.setattr(ApiClient, "_request_response", _successful_routes(routes))
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    assert not app.exception
    assert any(title.value == "Choose a workspace" for title in app.title)
    assert token not in repr(app.session_state["_evalforge_api_client"])
    workspace_select = {select.label: select for select in app.selectbox}["Workspace"]
    workspace_select.select("Quality team").run()
    {button.label: button for button in app.button}["Open workspace"].click().run()

    assert not app.exception
    assert not any(title.value == "Choose a workspace" for title in app.title)
    visible = [str(element.value) for element in [*app.caption, *app.markdown]]
    assert any("Morgan Lee" in value for value in visible)
    assert any("Quality team" in value for value in visible)
    assert app.session_state["_evalforge_workspace"].role == "editor"
    assert token not in repr(app.session_state["_evalforge_api_client"])


def test_workspace_forbidden_does_not_turn_into_a_logout(monkeypatch) -> None:
    context = current_auth_context(
        DashboardAuthConfig(mode="oidc", provider="company"),
        user=SimpleNamespace(is_logged_in=True, tokens={"access": "private-access-token"}),
    )
    assert context is not None
    monkeypatch.setattr(
        dashboard_app,
        "configured_auth",
        lambda: DashboardAuthConfig(mode="oidc", provider="company"),
    )
    monkeypatch.setattr(dashboard_app, "current_auth_context", lambda _: context)

    def forbidden(
        _client: ApiClient,
        _method: str,
        _path: str,
        **_kwargs: Any,
    ) -> httpx.Response:
        raise dashboard_app.ApiError("Access denied", status_code=403)

    monkeypatch.setattr(ApiClient, "_request_response", forbidden)
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    assert not app.exception
    assert any(title.value == "Workspace access changed" for title in app.title)
    assert app.session_state["_evalforge_reauthentication_required"] is False


def test_settings_presents_account_and_workspace_as_work_app_context(monkeypatch) -> None:
    monkeypatch.setattr(
        settings_page,
        "configured_auth",
        lambda: DashboardAuthConfig(mode="oidc", provider="company"),
    )
    routes: dict[str, Any] = {
        "/health/live": {"status": "healthy"},
        "/health/ready": {"status": "ready"},
        "/api/v1/capabilities": {},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _successful_routes(routes))
    app = AppTest.from_string(SETTINGS_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert any(subheader.value == "Account and workspace" for subheader in app.subheader)
    visible = [str(element.value) for element in [*app.caption, *app.markdown]]
    assert any("Morgan Lee" in value for value in visible)
    assert any("Quality team" in value for value in visible)
    assert any(select.label == "Switch workspace" for select in app.selectbox)
    assert any(button.label == "Sign out" for button in app.button)


def _successful_routes(routes: dict[str, Any]):
    def request_response(
        _client: ApiClient,
        _method: str,
        path: str,
        **_kwargs: Any,
    ) -> httpx.Response:
        payload = routes.get(path)
        if payload is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=payload)

    return request_response
