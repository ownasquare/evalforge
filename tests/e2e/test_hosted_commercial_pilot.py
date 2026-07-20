"""Opt-in live acceptance for the hosted EvalForge commercialization pilot.

Collection or a skip is not hosted proof. The suite runs only with explicit
HTTPS targets and caller-owned OIDC session fixtures.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

DASHBOARD_URL_ENV = "EVALFORGE_HOSTED_DASHBOARD_URL"
API_URL_ENV = "EVALFORGE_HOSTED_API_URL"
FIXTURE_ENV = "EVALFORGE_HOSTED_ACCEPTANCE_FIXTURE"

pytestmark = pytest.mark.e2e


@dataclass(frozen=True, slots=True)
class HostedFixture:
    dashboard_url: str
    api_url: str
    owner_storage_state: Path
    owner_login_storage_state: Path
    owner_token_file: Path
    foreign_token_file: Path
    owner_display_name: str
    primary_workspace_id: str
    primary_workspace_name: str
    foreign_workspace_id: str
    foreign_workspace_name: str
    post_logout_text: str
    minimum_candidate_count: int
    run_timeout_seconds: int
    allow_mutation: bool


@pytest.fixture(scope="session")
def hosted_fixture() -> HostedFixture:
    """Skip until a caller provides every live-hosted coordinate."""

    dashboard_url = os.environ.get(DASHBOARD_URL_ENV)
    api_url = os.environ.get(API_URL_ENV)
    fixture_value = os.environ.get(FIXTURE_ENV)
    missing = [
        key
        for key, value in (
            (DASHBOARD_URL_ENV, dashboard_url),
            (API_URL_ENV, api_url),
            (FIXTURE_ENV, fixture_value),
        )
        if not value
    ]
    if missing:
        pytest.skip("hosted acceptance is not configured; missing " + ", ".join(missing))

    fixture_path = Path(str(fixture_value)).expanduser()
    if not fixture_path.is_file():
        pytest.skip(f"the fixture path supplied by {FIXTURE_ENV} does not exist")
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        pytest.fail(f"hosted acceptance fixture is not readable JSON: {error}")
    if not isinstance(payload, dict):
        pytest.fail("hosted acceptance fixture must be a JSON object")

    def text(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            pytest.fail(f"{key} must be a non-empty trimmed string")
        return value

    def file(key: str) -> Path:
        path = Path(text(key)).expanduser()
        resolved = path if path.is_absolute() else fixture_path.parent / path
        if not resolved.is_file():
            pytest.skip(f"the caller-owned fixture file for {key} is absent")
        return resolved

    def storage_state(key: str) -> Path:
        path = file(key)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            pytest.fail(f"{key} is not readable Playwright JSON: {error}")
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            pytest.fail(f"{key} must be Playwright storage-state JSON")
        return path

    minimum_candidates = payload.get("minimum_candidate_count")
    if (
        not isinstance(minimum_candidates, int)
        or isinstance(minimum_candidates, bool)
        or minimum_candidates < 2
    ):
        pytest.fail("minimum_candidate_count must be an integer of at least 2")
    run_timeout = payload.get("run_timeout_seconds", 240)
    if (
        not isinstance(run_timeout, int)
        or isinstance(run_timeout, bool)
        or not 30 <= run_timeout <= 900
    ):
        pytest.fail("run_timeout_seconds must be an integer from 30 through 900")

    primary_id = _uuid(text("primary_workspace_id"), "primary_workspace_id")
    foreign_id = _uuid(text("foreign_workspace_id"), "foreign_workspace_id")
    if primary_id == foreign_id:
        pytest.fail("hosted acceptance requires two distinct workspace IDs")
    return HostedFixture(
        dashboard_url=_https_url(str(dashboard_url), "dashboard URL"),
        api_url=_https_url(str(api_url), "API URL", origin_only=True),
        owner_storage_state=storage_state("owner_storage_state"),
        owner_login_storage_state=storage_state("owner_login_storage_state"),
        owner_token_file=file("owner_access_token_file"),
        foreign_token_file=file("foreign_access_token_file"),
        owner_display_name=text("owner_display_name"),
        primary_workspace_id=primary_id,
        primary_workspace_name=text("primary_workspace_name"),
        foreign_workspace_id=foreign_id,
        foreign_workspace_name=text("foreign_workspace_name"),
        post_logout_text=text("post_logout_visible_text"),
        minimum_candidate_count=minimum_candidates,
        run_timeout_seconds=run_timeout,
        allow_mutation=payload.get("allow_commercial_mutation") is True,
    )


def test_hosted_two_workspace_tenant_denial(hosted_fixture: HostedFixture) -> None:
    """Both OIDC identities read their tenant; the owner cannot select the other tenant."""

    with _api(hosted_fixture.api_url, hosted_fixture.owner_token_file) as owner:
        owner_session = _object(owner, "/api/v1/session")
        _assert_workspace(
            owner_session,
            hosted_fixture.primary_workspace_id,
            hosted_fixture.primary_workspace_name,
        )
        assert hosted_fixture.foreign_workspace_id not in _workspace_ids(owner_session)
        _status(
            owner.get(
                "/api/v1/runs",
                headers=_workspace(hosted_fixture.primary_workspace_id),
            ),
            200,
            "primary tenant",
        )
        _status(
            owner.get(
                "/api/v1/runs",
                headers=_workspace(hosted_fixture.foreign_workspace_id),
            ),
            403,
            "cross-tenant denial",
        )

    with _api(hosted_fixture.api_url, hosted_fixture.foreign_token_file) as foreign:
        foreign_session = _object(foreign, "/api/v1/session")
        _assert_workspace(
            foreign_session,
            hosted_fixture.foreign_workspace_id,
            hosted_fixture.foreign_workspace_name,
        )
        _status(
            foreign.get(
                "/api/v1/runs",
                headers=_workspace(hosted_fixture.foreign_workspace_id),
            ),
            200,
            "foreign tenant",
        )


def test_hosted_commercial_journey_and_mobile_settings(
    browser: Any,
    hosted_fixture: HostedFixture,
) -> None:
    """Activate, request, read back, and cancel in a disposable hosted workspace."""

    from playwright.sync_api import expect

    if not hosted_fixture.allow_mutation:
        pytest.skip(
            "commercial acceptance needs allow_commercial_mutation=true in a disposable fixture"
        )

    headers = _workspace(hosted_fixture.primary_workspace_id)
    context: Any | None = None
    trial_started = False
    cleanup_scope = uuid4().hex
    with _api(hosted_fixture.api_url, hosted_fixture.owner_token_file) as api:
        entitlement = _object(api, "/api/v1/commercial/entitlement", headers=headers)
        assert entitlement.get("hosted") is True
        assert entitlement.get("commercial_pilot_enabled") is True
        assert entitlement.get("plan_code") == "open_source", (
            "reset the disposable fixture: expected open_source"
        )
        assert entitlement.get("can_start_runs") is False
        initial_funnel = _object(api, "/api/v1/commercial/funnel", headers=headers)
        initial_activations = _count(initial_funnel.get("activated_runs"))
        assert not any(
            row.get("status") == "pending"
            for row in _list(api, "/api/v1/commercial/team-requests", headers=headers)
        ), "reset the disposable fixture: pending request found"

        try:
            context, page = _open_workspace(browser, hosted_fixture)
            page.get_by_role("link", name="Settings", exact=True).click()
            expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
            start_trial = page.get_by_role(
                "button",
                name=re.compile(r"Start \d+-day hosted trial"),
            )
            expect(start_trial).to_be_visible()
            start_trial.click()
            trial_started = True
            _poll(
                lambda: _object(api, "/api/v1/commercial/entitlement", headers=headers),
                lambda value: (
                    value.get("status") == "trialing" and value.get("can_start_runs") is True
                ),
                "trial entitlement",
            )

            page.get_by_role("link", name="New evaluation", exact=True).click()
            expect(page.get_by_role("heading", name="New evaluation", exact=True)).to_be_visible()
            run_name = f"Hosted pilot acceptance {uuid4().hex[:10]}"
            page.get_by_role("textbox", name="Run name", exact=True).fill(run_name)
            candidate_summary = page.get_by_text(
                re.compile(r"Review [0-9,]+ candidates"),
                exact=False,
            ).first
            expect(candidate_summary).to_be_visible()
            assert _candidate_count(candidate_summary.inner_text()) >= (
                hosted_fixture.minimum_candidate_count
            )
            page.get_by_role("button", name="Start evaluation", exact=True).click()
            expect(
                page.get_by_text(
                    "Evaluation completed. Results are ready to inspect.",
                    exact=True,
                )
            ).to_be_visible(timeout=hosted_fixture.run_timeout_seconds * 1_000)
            page.get_by_role("button", name="Review results", exact=True).click()
            expect(page.get_by_role("heading", name="Results", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name=run_name, exact=True)).to_be_visible()

            page.get_by_text("Export evidence", exact=True).click()
            page.get_by_role("button", name="Prepare evidence package", exact=True).click()
            expect(
                page.get_by_role("button", name="Download evidence package", exact=True)
            ).to_be_visible()
            _poll(
                lambda: _object(api, "/api/v1/commercial/funnel", headers=headers),
                lambda value: _count(value.get("activated_runs")) >= initial_activations + 1,
                "export activation",
            )

            page.get_by_role("link", name="Settings", exact=True).click()
            expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_text("Plans and hosted pilot", exact=True)).to_be_visible()
            expect(
                page.get_by_text("Current workspace access · server readback", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="Request team pilot", exact=True)
            ).to_be_visible()
            dimensions = page.evaluate(
                "() => ({ scrollWidth: document.documentElement.scrollWidth, "
                "clientWidth: document.documentElement.clientWidth })"
            )
            assert dimensions["scrollWidth"] <= dimensions["clientWidth"]

            page.get_by_role("button", name="Request team pilot", exact=True).click()
            pending = _poll(
                lambda: _list(api, "/api/v1/commercial/team-requests", headers=headers),
                lambda rows: len([row for row in rows if row.get("status") == "pending"]) == 1,
                "pending team request",
            )
            request_id = str(next(row["id"] for row in pending if row.get("status") == "pending"))
            expect(page.get_by_text("Team pilot requests", exact=True)).to_be_visible()
            page.get_by_role("button", name="Cancel request", exact=True).click()
            canceled_requests = _poll(
                lambda: _list(api, "/api/v1/commercial/team-requests", headers=headers),
                lambda rows: any(
                    row.get("id") == request_id and row.get("status") == "canceled" for row in rows
                ),
                "canceled team request",
            )
            assert any(
                row.get("workspace_id") == hosted_fixture.primary_workspace_id
                for row in canceled_requests
            )

            page.get_by_role("button", name="Cancel hosted trial", exact=True).click()
            canceled_entitlement = _poll(
                lambda: _object(api, "/api/v1/commercial/entitlement", headers=headers),
                lambda value: (
                    value.get("status") == "canceled" and value.get("can_start_runs") is False
                ),
                "canceled entitlement",
            )
            assert canceled_entitlement.get("workspace_id") == hosted_fixture.primary_workspace_id
            event_types = {
                str(row.get("event_type"))
                for row in _list(api, "/api/v1/commercial/billing-events", headers=headers)
            }
            assert {"entitlement.trial_activated", "entitlement.trial_canceled"} <= event_types
            capabilities = _object(api, "/api/v1/capabilities", headers=headers)
            commercial = capabilities.get("commercial")
            assert isinstance(commercial, dict)
            assert commercial.get("live_money") is False
            assert commercial.get("payment_path") == "qualified_team_request"
        finally:
            _cleanup_pending_requests(
                api,
                hosted_fixture.primary_workspace_id,
                cleanup_scope,
            )
            if trial_started:
                _cleanup_post(
                    api,
                    "/api/v1/commercial/trial/cancel",
                    hosted_fixture.primary_workspace_id,
                    f"acceptance-cleanup-trial-{cleanup_scope}",
                )
            if context is not None:
                context.close()


def test_hosted_oidc_session_and_logout(browser: Any, hosted_fixture: HostedFixture) -> None:
    """Use an IdP-only SSO session for the live callback, then log out."""

    from playwright.sync_api import expect

    context = browser.new_context(
        storage_state=str(hosted_fixture.owner_login_storage_state),
        viewport={"width": 1440, "height": 1000},
    )
    try:
        page = context.new_page()
        page.goto(hosted_fixture.dashboard_url, wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Welcome to EvalForge", exact=True)).to_be_visible(
            timeout=30_000
        )
        sign_in = page.get_by_role("button", name="Sign in", exact=True)
        assert sign_in.count() == 1
        sign_in.click()
        expect(page).to_have_url(
            re.compile(rf"^{re.escape(hosted_fixture.dashboard_url)}(?:/.*)?$"),
            timeout=60_000,
        )
        _finish_workspace_open(page, hosted_fixture)
        expect(
            page.get_by_text(hosted_fixture.owner_display_name, exact=True).first
        ).to_be_visible()
        page.get_by_role("link", name="Settings", exact=True).click()
        expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
        page.get_by_role("button", name="Sign out", exact=True).click()
        expect(page.get_by_text(hosted_fixture.post_logout_text, exact=False).first).to_be_visible(
            timeout=30_000
        )
    finally:
        context.close()


def _open_workspace(browser: Any, fixture: HostedFixture) -> tuple[Any, Any]:
    context = browser.new_context(
        storage_state=str(fixture.owner_storage_state),
        viewport={"width": 1440, "height": 1000},
    )
    try:
        page = context.new_page()
        page.goto(fixture.dashboard_url, wait_until="domcontentloaded")
        _finish_workspace_open(page, fixture)
        return context, page
    except BaseException:
        context.close()
        raise


def _finish_workspace_open(page: Any, fixture: HostedFixture) -> None:
    from playwright.sync_api import expect

    heading = page.locator("h1").first
    expect(heading).to_be_visible(timeout=30_000)
    value = heading.inner_text().strip()
    if value == "Welcome to EvalForge":
        pytest.fail("the supplied authenticated owner state did not open EvalForge")
    if value == "Choose a workspace":
        page.get_by_role("combobox", name="Workspace", exact=True).click()
        page.get_by_text(fixture.primary_workspace_name, exact=True).click()
        page.get_by_role("button", name="Open workspace", exact=True).click()
    expect(page.get_by_role("heading", name="Evaluation workspace", exact=True)).to_be_visible(
        timeout=30_000
    )
    expect(page.get_by_text(fixture.primary_workspace_name, exact=True).first).to_be_visible()


def _api(base_url: str, token_file: Path) -> httpx.Client:
    token = token_file.read_text(encoding="utf-8").rstrip("\r\n")
    if not token or token != token.strip() or "\n" in token or "\r" in token:
        pytest.fail("each token file must contain exactly one current token")
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        follow_redirects=False,
        timeout=20,
    )


def _object(
    client: httpx.Client,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.get(path, headers=headers)
    _status(response, 200, path)
    payload = response.json()
    assert isinstance(payload, dict), f"{path} must return a JSON object"
    return payload


def _list(
    client: httpx.Client,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    response = client.get(path, headers=headers)
    _status(response, 200, path)
    payload = response.json()
    assert isinstance(payload, list) and all(isinstance(row, dict) for row in payload)
    return payload


def _poll(
    read: Callable[[], Any],
    accepted: Callable[[Any], bool],
    label: str,
) -> Any:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = read()
        if accepted(value):
            return value
        time.sleep(0.25)
    pytest.fail(f"hosted readback did not reach {label}")


def _cleanup_pending_requests(client: httpx.Client, workspace_id: str, scope: str) -> None:
    try:
        rows = _list(
            client,
            "/api/v1/commercial/team-requests",
            headers=_workspace(workspace_id),
        )
    except (AssertionError, httpx.HTTPError, ValueError):
        return
    for index, row in enumerate(rows):
        request_id = row.get("id")
        if row.get("status") == "pending" and isinstance(request_id, str):
            _cleanup_post(
                client,
                f"/api/v1/commercial/team-requests/{request_id}/cancel",
                workspace_id,
                f"acceptance-cleanup-request-{scope}-{index}",
            )


def _cleanup_post(client: httpx.Client, path: str, workspace_id: str, key: str) -> None:
    with suppress(httpx.HTTPError):
        client.post(
            path,
            headers={**_workspace(workspace_id), "Idempotency-Key": key},
        )


def _assert_workspace(session: dict[str, Any], workspace_id: str, name: str) -> None:
    assert session.get("auth_mode") == "oidc"
    workspaces = session.get("workspaces")
    assert isinstance(workspaces, list)
    assert any(
        isinstance(row, dict) and row.get("id") == workspace_id and row.get("name") == name
        for row in workspaces
    )


def _workspace_ids(session: dict[str, Any]) -> set[str]:
    rows = session.get("workspaces")
    return {
        str(row["id"])
        for row in rows
        if isinstance(rows, list) and isinstance(row, dict) and "id" in row
    }


def _workspace(workspace_id: str) -> dict[str, str]:
    return {"X-EvalForge-Workspace-ID": workspace_id}


def _status(response: httpx.Response, expected: int, label: str) -> None:
    assert response.status_code == expected, (
        f"{label} returned HTTP {response.status_code}; expected HTTP {expected}"
    )


def _candidate_count(value: str) -> int:
    match = re.search(r"Review ([0-9,]+) candidates", value)
    assert match is not None
    return int(match.group(1).replace(",", ""))


def _count(value: Any) -> int:
    assert isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value


def _uuid(value: str, label: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError:
        pytest.fail(f"{label} must be a UUID")
    if str(parsed) != value.lower():
        pytest.fail(f"{label} must use canonical UUID text")
    return str(parsed)


def _https_url(value: str, label: str, *, origin_only: bool = False) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        pytest.fail(f"hosted {label} must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        pytest.fail(f"hosted {label} must not contain credentials, query data, or a fragment")
    if origin_only and parsed.path not in {"", "/"}:
        pytest.fail(f"hosted {label} must be an origin without a path")
    return candidate
