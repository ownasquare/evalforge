from __future__ import annotations

from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

from evalforge.dashboard.client import ApiClient
from evalforge.dashboard.pages.models import (
    _allowed_model_options,
    _api_mode_label,
    _option_label,
    _profile_payload,
)

MODELS_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.models import render
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge model profiles test", layout="wide")
initialize_state()
render()
"""

VIEWER_MODELS_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.auth import WorkspaceOption
from evalforge.dashboard.pages.models import render
from evalforge.dashboard.state import (
    configure_client,
    initialize_state,
    select_workspace,
    sync_identity,
)

st.set_page_config(page_title="EvalForge viewer model profiles test", layout="wide")
initialize_state()
sync_identity("viewer-fingerprint")
workspace = WorkspaceOption("workspace-1", "Quality", "viewer")
select_workspace(workspace)
configure_client(identity_fingerprint="viewer-fingerprint", workspace_id=workspace.id)
render()
"""


def _capabilities() -> dict[str, Any]:
    return {
        "providers": {
            "real_runs_enabled": True,
            "openai": {
                "configured": True,
                "models": ["gpt-4.1-mini"],
            },
            "openai_compatible": {
                "configured": False,
                "auth_mode": "bearer",
                "models": ["server-private-model"],
            },
        }
    }


def test_allowed_models_only_include_configured_server_published_options() -> None:
    assert _allowed_model_options(_capabilities()) == [
        {
            "key": "openai::gpt-4.1-mini",
            "provider": "openai",
            "provider_label": "OpenAI",
            "model_name": "gpt-4.1-mini",
            "api_mode": "responses",
        }
    ]


def test_profile_payload_contains_no_credentials_or_unpublished_configuration() -> None:
    option = _allowed_model_options(_capabilities())[0]

    payload = _profile_payload(
        name=" Release candidate ",
        description="  Used for release checks. ",
        option=option,
    )

    assert payload == {
        "name": "Release candidate",
        "description": "Used for release checks.",
        "provider": "openai",
        "model_name": "gpt-4.1-mini",
        "api_mode": "responses",
        "generation_parameters": {},
        "enabled": True,
    }
    assert not any("key" in field or "token" in field or "secret" in field for field in payload)


def test_model_options_explain_the_server_selected_api_mode() -> None:
    option = _allowed_model_options(_capabilities())[0]

    assert _option_label(option) == "OpenAI · gpt-4.1-mini · Responses API"
    assert _api_mode_label("chat_completions") == "Chat Completions API"


def test_models_page_creates_only_a_server_approved_profile(monkeypatch) -> None:
    submitted: list[dict[str, Any]] = []
    routes: dict[str, Any] = {
        "/api/v1/models": {"items": []},
        "/api/v1/capabilities": _capabilities(),
    }
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _fake_transport(routes, submitted=submitted, readback=True),
    )
    app = AppTest.from_string(MODELS_PAGE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.text_input}["Profile name"].set_value(
        "Release candidate"
    ).run()
    {button.label: button for button in app.button}["Add model profile"].click().run()

    assert not app.exception
    assert submitted == [
        {
            "name": "Release candidate",
            "description": None,
            "provider": "openai",
            "model_name": "gpt-4.1-mini",
            "api_mode": "responses",
            "generation_parameters": {},
            "enabled": True,
        }
    ]
    assert app.dataframe[0].value.to_dict(orient="records") == [
        {
            "Profile": "Release candidate",
            "Provider": "OpenAI",
            "Model": "gpt-4.1-mini",
            "Connection": "Responses API",
            "Available": True,
        }
    ]
    labels = [element.label.lower() for element in [*app.text_input, *app.text_area]]
    assert not any("secret" in label or "api key" in label or "token" in label for label in labels)


def test_models_page_updates_profile_availability(monkeypatch) -> None:
    updates: list[tuple[str, dict[str, Any]]] = []
    model = {
        "id": "model-1",
        "name": "Release candidate",
        "provider": "openai",
        "model_name": "gpt-4.1-mini",
        "api_mode": "responses",
        "enabled": True,
    }
    routes: dict[str, Any] = {
        "/api/v1/models": {"items": [model]},
        "/api/v1/capabilities": _capabilities(),
    }
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _fake_transport(routes, updates=updates, readback=True),
    )
    app = AppTest.from_string(MODELS_PAGE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.checkbox}["Available for evaluations"].uncheck().run()
    {button.label: button for button in app.button}["Save availability"].click().run()

    assert not app.exception
    assert updates == [("model-1", {"enabled": False})]
    assert app.dataframe[0].value.to_dict(orient="records")[0]["Available"] is False


def test_viewer_can_review_profiles_but_cannot_manage_them(monkeypatch) -> None:
    model = {
        "id": "model-1",
        "name": "Release candidate",
        "provider": "openai",
        "model_name": "gpt-4.1-mini",
        "api_mode": "responses",
        "enabled": True,
    }
    routes: dict[str, Any] = {
        "/api/v1/models": {"items": [model]},
        "/api/v1/capabilities": _capabilities(),
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes))
    app = AppTest.from_string(VIEWER_MODELS_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert "Model profiles" in [element.value for element in app.subheader]
    assert any("owners and admins" in str(message.value).lower() for message in app.info)
    assert not any(
        button.label in {"Save availability", "Add model profile"} for button in app.button
    )


def _fake_transport(
    routes: dict[str, Any],
    *,
    submitted: list[dict[str, Any]] | None = None,
    updates: list[tuple[str, dict[str, Any]]] | None = None,
    readback: bool = False,
):
    def request_response(
        _: ApiClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if method == "POST" and path == "/api/v1/models":
            payload = dict(kwargs["json_payload"])
            if submitted is not None:
                submitted.append(payload)
            created = {"id": "model-new", **payload}
            if readback:
                routes["/api/v1/models"] = {"items": [created]}
            return httpx.Response(201, json=created)
        if method == "PATCH" and path.startswith("/api/v1/models/"):
            payload = dict(kwargs["json_payload"])
            model_id = path.rsplit("/", 1)[-1]
            if updates is not None:
                updates.append((model_id, payload))
            if readback:
                listed = routes.get("/api/v1/models")
                if isinstance(listed, dict) and isinstance(listed.get("items"), list):
                    for item in listed["items"]:
                        if isinstance(item, dict) and item.get("id") == model_id:
                            item.update(payload)
            return httpx.Response(200, json={"id": model_id, **payload})
        payload = routes.get(path)
        if payload is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=payload)

    return request_response
