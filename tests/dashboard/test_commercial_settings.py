from __future__ import annotations

from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

from evalforge.dashboard.auth import DashboardAuthConfig
from evalforge.dashboard.client import ApiClient
from evalforge.dashboard.pages import settings as settings_page

SETTINGS_SOURCE = """
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

st.set_page_config(page_title="EvalForge commercial settings test", layout="wide")
initialize_state()
sync_identity("known-fingerprint")
account = DashboardAccount("user-1", "Morgan Lee", "morgan@example.com")
workspace = WorkspaceOption("workspace-1", "Quality team", "owner")
set_account_context(account)
set_available_workspaces([workspace])
select_workspace(workspace)
configure_client(identity_fingerprint="known-fingerprint", workspace_id=workspace.id)
render()
"""


def _plans() -> list[dict[str, Any]]:
    return [
        {
            "code": "open_source",
            "name": "Community self-hosted",
            "audience": "Teams operating EvalForge themselves.",
            "price_label": "Free and open source",
            "features": ["Complete evaluation workflow", "Local persistence and exports"],
            "self_hosted": True,
            "available": True,
        },
        {
            "code": "hosted_trial",
            "name": "Hosted team trial · 14 days",
            "audience": "Small AI teams testing a shared workflow.",
            "price_label": "Invitation pilot",
            "features": ["No-install managed workspace", "Managed persistence"],
            "self_hosted": False,
            "available": True,
        },
        {
            "code": "team",
            "name": "Hosted team",
            "audience": "Teams with recurring evaluation needs.",
            "price_label": "Qualified team request",
            "features": ["Shared workspace", "Pilot support"],
            "self_hosted": False,
            "available": True,
        },
    ]


def test_hosted_settings_show_readback_request_path_and_cancel_trial(monkeypatch) -> None:
    entitlement = {
        "workspace_id": "workspace-1",
        "plan_code": "hosted_trial",
        "status": "trialing",
        "seat_limit": 5,
        "active_memberships": 2,
        "source": "self_service_trial",
        "current_period_start": "2026-07-20T00:00:00Z",
        "current_period_end": "2026-08-03T00:00:00Z",
        "can_start_runs": True,
        "hosted": True,
        "commercial_pilot_enabled": True,
    }
    mutations: list[tuple[str, str | None]] = []
    routes: dict[str, Any] = {
        "/health/live": {"status": "live"},
        "/health/ready": {"status": "ready"},
        "/api/v1/capabilities": {
            "commercial": {
                "pilot_enabled": True,
                "hosted": True,
                "trial_days": 14,
                "trial_seat_limit": 5,
                "payment_path": "qualified_team_request",
                "live_money": False,
            }
        },
        "/api/v1/commercial/plans": _plans(),
        "/api/v1/commercial/funnel": {
            "activated_runs": 1,
            "pending_team_requests": 0,
            "total_team_requests": 0,
        },
        "/api/v1/commercial/team-requests": [],
        "/api/v1/commercial/billing-events": [
            {
                "event_type": "trial_started",
                "provider": "evalforge",
                "created_at": "2026-07-20T00:00:00Z",
            }
        ],
    }

    def request_response(
        _client: ApiClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if path == "/api/v1/commercial/entitlement":
            return httpx.Response(200, json=entitlement)
        if path == "/api/v1/commercial/events":
            return httpx.Response(200, json={"id": "event-1"})
        if path == "/api/v1/commercial/trial/cancel":
            mutations.append((path, kwargs["headers"]["Idempotency-Key"]))
            entitlement.update({"status": "canceled", "can_start_runs": False})
            return httpx.Response(200, json=entitlement)
        payload = routes.get(path)
        if payload is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        settings_page,
        "configured_auth",
        lambda: DashboardAuthConfig(mode="oidc", provider="company"),
    )
    monkeypatch.setattr(ApiClient, "_request_response", request_response)
    app = AppTest.from_string(SETTINGS_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    visible = [str(element.value) for element in [*app.caption, *app.markdown, *app.info]]
    assert any("Community self-hosted" in value for value in visible)
    assert any("Qualified team request" in value for value in visible)
    assert any("No card is charged" in value for value in visible)
    assert any(button.label == "Request team pilot" for button in app.button)
    {button.label: button for button in app.button}["Cancel hosted trial"].click().run()

    assert not app.exception
    assert mutations and mutations[0][0] == "/api/v1/commercial/trial/cancel"
    assert mutations[0][1]
    metric_values = {metric.label: metric.value for metric in app.metric}
    assert metric_values["Status"] == "Canceled"
    assert not any(button.label == "Cancel hosted trial" for button in app.button)


def test_self_hosted_settings_keep_community_useful_without_paid_actions(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/health/live": {"status": "live"},
        "/health/ready": {"status": "ready"},
        "/api/v1/capabilities": {
            "commercial": {
                "pilot_enabled": False,
                "hosted": False,
                "trial_days": 14,
                "trial_seat_limit": 5,
                "payment_path": "qualified_team_request",
                "live_money": False,
            }
        },
        "/api/v1/commercial/plans": _plans(),
        "/api/v1/commercial/entitlement": {
            "workspace_id": "workspace-1",
            "plan_code": "open_source",
            "status": "active",
            "seat_limit": 1,
            "active_memberships": 1,
            "source": "self_hosted",
            "can_start_runs": True,
            "hosted": False,
            "commercial_pilot_enabled": False,
        },
    }

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

    monkeypatch.setattr(
        settings_page,
        "configured_auth",
        lambda: DashboardAuthConfig(mode="local", provider="evalforge"),
    )
    monkeypatch.setattr(ApiClient, "_request_response", request_response)
    app = AppTest.from_string(SETTINGS_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    visible = [str(element.value) for element in [*app.caption, *app.markdown, *app.info]]
    assert any("complete open-source workflow" in value for value in visible)
    assert any("free self-hosted edition" in value for value in visible)
    assert not any("hosted trial" in button.label.lower() for button in app.button)
    assert not any(button.label == "Request team pilot" for button in app.button)
