from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from evalforge.api.app import create_app
from evalforge.commercial import ActivationRecorder
from evalforge.config import Settings
from evalforge.container import AppContainer, build_container
from evalforge.models import (
    ActivationEvent,
    ActivationEventName,
    BillingEvent,
    ImmutableProvenanceError,
    RecordStatus,
    User,
    Workspace,
    WorkspaceEntitlement,
    WorkspaceMembership,
    utc_now,
)
from evalforge.security.auth import AuthenticatedPrincipal, AuthenticationError
from evalforge.security.permissions import WorkspaceRole

ISSUER = "https://identity.commercial.test"
ALPHA_WORKSPACE_ID = "30000000-0000-4000-8000-000000000001"
BETA_WORKSPACE_ID = "30000000-0000-4000-8000-000000000002"
OWNER_USER_ID = "30000000-0000-4000-8000-000000000011"
VIEWER_USER_ID = "30000000-0000-4000-8000-000000000012"
FOREIGN_OWNER_USER_ID = "30000000-0000-4000-8000-000000000013"

FIXTURE_CREDENTIALS = {
    "owner": "fixture-commercial-owner",
    "viewer": "fixture-commercial-viewer",
    "foreign_owner": "fixture-commercial-foreign-owner",
}


class ResponseLike(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self) -> object: ...


class StubAuthBackend:
    """Resolve synthetic test labels without retaining request credentials."""

    def __init__(self, principals: Mapping[str, AuthenticatedPrincipal]) -> None:
        self._principals = dict(principals)

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        if authorization is None:
            raise AuthenticationError
        scheme, separator, credential = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not separator:
            raise AuthenticationError(invalid=True)
        principal = self._principals.get(credential)
        if principal is None:
            raise AuthenticationError(invalid=True)
        return principal


@dataclass(frozen=True, slots=True)
class HostedApi:
    client: TestClient
    container: AppContainer

    def headers(self, actor: str, workspace_id: str = ALPHA_WORKSPACE_ID) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {FIXTURE_CREDENTIALS[actor]}",
            "X-EvalForge-Workspace-ID": workspace_id,
        }


@pytest.fixture
def local_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'local-commercial.db'}",
        auto_migrate=False,
        seed_demo=False,
        real_runs_enabled=False,
    )
    container = build_container(settings, migrate=True)
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            yield client
    finally:
        container.engine.dispose()


@pytest.fixture
def hosted_api(tmp_path: Path) -> Iterator[HostedApi]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'hosted-commercial.db'}",
        auto_migrate=False,
        seed_demo=False,
        real_runs_enabled=False,
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience="evalforge-api",
        oidc_jwks_url=f"{ISSUER}/jwks.json",
        public_base_url="https://api.commercial.test",
        commercial_pilot_enabled=True,
    )
    container = build_container(settings, migrate=True)
    _provision_identities(container)
    container.authenticator = StubAuthBackend(
        {
            FIXTURE_CREDENTIALS[actor]: AuthenticatedPrincipal(
                user_id=None,
                issuer=ISSUER,
                subject=f"commercial-{actor}",
                display_name=f"Untrusted {actor}",
                is_local=False,
            )
            for actor in FIXTURE_CREDENTIALS
        }
    )
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            yield HostedApi(client=client, container=container)
    finally:
        container.engine.dispose()


@pytest.fixture
def hosted_api_only(tmp_path: Path) -> Iterator[HostedApi]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'hosted-commercial-api-only.db'}",
        auto_migrate=False,
        seed_demo=False,
        real_runs_enabled=False,
        executor_mode="api_only",
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience="evalforge-api",
        oidc_jwks_url=f"{ISSUER}/jwks.json",
        public_base_url="https://api.commercial.test",
        commercial_pilot_enabled=True,
    )
    container = build_container(settings, migrate=True)
    _provision_identities(container)
    container.authenticator = StubAuthBackend(
        {
            FIXTURE_CREDENTIALS[actor]: AuthenticatedPrincipal(
                user_id=None,
                issuer=ISSUER,
                subject=f"commercial-{actor}",
                display_name=f"Untrusted {actor}",
                is_local=False,
            )
            for actor in FIXTURE_CREDENTIALS
        }
    )
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            yield HostedApi(client=client, container=container)
    finally:
        container.engine.dispose()


def _provision_identities(container: AppContainer) -> None:
    with container.session_factory() as session:
        alpha = Workspace(id=ALPHA_WORKSPACE_ID, slug="commercial-alpha", name="Alpha team")
        beta = Workspace(id=BETA_WORKSPACE_ID, slug="commercial-beta", name="Beta team")
        owner = User(
            id=OWNER_USER_ID,
            issuer=ISSUER,
            subject="commercial-owner",
            display_name="Owner",
        )
        viewer = User(
            id=VIEWER_USER_ID,
            issuer=ISSUER,
            subject="commercial-viewer",
            display_name="Viewer",
        )
        foreign_owner = User(
            id=FOREIGN_OWNER_USER_ID,
            issuer=ISSUER,
            subject="commercial-foreign_owner",
            display_name="Foreign owner",
        )
        session.add_all(
            [
                alpha,
                beta,
                owner,
                viewer,
                foreign_owner,
                WorkspaceMembership(
                    workspace=alpha,
                    user=owner,
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceMembership(
                    workspace=alpha,
                    user=viewer,
                    role=WorkspaceRole.VIEWER,
                ),
                WorkspaceMembership(
                    workspace=beta,
                    user=foreign_owner,
                    role=WorkspaceRole.OWNER,
                ),
            ]
        )
        session.commit()


def _assert_error(response: ResponseLike, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    payload = cast(dict[str, object], response.json())
    error = cast(dict[str, object], payload["error"])
    assert error["code"] == code


def _create_matrix(
    api: HostedApi,
    prefix: str,
    *,
    actor: str = "owner",
    workspace_id: str = ALPHA_WORKSPACE_ID,
) -> dict[str, object]:
    headers = api.headers(actor, workspace_id)
    dataset = api.client.post(
        "/api/v1/datasets",
        headers=headers,
        json={
            "name": f"{prefix} dataset",
            "cases": [
                {
                    "external_id": f"{prefix}-case",
                    "input_text": "Return the expected word.",
                    "expected_output": "verified",
                }
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    prompt = api.client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "name": f"{prefix} prompt",
            "system_template": "Return the expected word.",
            "user_template": "{input}",
        },
    )
    assert prompt.status_code == 201, prompt.text
    second_prompt = api.client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "name": f"{prefix} alternative prompt",
            "system_template": "Answer only with the expected word.",
            "user_template": "{input}",
        },
    )
    assert second_prompt.status_code == 201, second_prompt.text
    model = api.client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "name": f"{prefix} model",
            "provider": "demo",
            "model_name": "demo-reliable",
            "api_mode": "deterministic",
            "generation_parameters": {
                "temperature": 0.0,
                "max_output_tokens": 32,
                "seed": 7,
            },
        },
    )
    assert model.status_code == 201, model.text
    return {
        "name": f"{prefix} comparison",
        "dataset_id": dataset.json()["id"],
        "prompt_ids": [prompt.json()["id"], second_prompt.json()["id"]],
        "model_ids": [model.json()["id"]],
    }


def _wait_for_terminal(
    api: HostedApi,
    run_id: str,
    *,
    actor: str = "owner",
    workspace_id: str = ALPHA_WORKSPACE_ID,
) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = api.client.get(
            f"/api/v1/runs/{run_id}",
            headers=api.headers(actor, workspace_id),
        )
        assert response.status_code == 200, response.text
        payload = cast(dict[str, object], response.json())
        if payload["status"] in {"completed", "completed_with_errors", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("evaluation run did not reach a terminal state")


@pytest.mark.integration
def test_local_oss_offer_stays_runnable_and_hosted_mutations_are_disabled(
    local_client: TestClient,
) -> None:
    plans = local_client.get("/api/v1/commercial/plans")
    assert plans.status_code == 200, plans.text
    assert plans.headers["cache-control"] == "private, no-store"
    assert [plan["available"] for plan in plans.json()] == [True, False, False]

    entitlement = local_client.get("/api/v1/commercial/entitlement")
    assert entitlement.status_code == 200, entitlement.text
    assert entitlement.json() == {
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "plan_code": "open_source",
        "status": "active",
        "seat_limit": 1,
        "active_memberships": 1,
        "source": "oss_self_hosted",
        "current_period_start": None,
        "current_period_end": None,
        "can_start_runs": True,
        "hosted": False,
        "commercial_pilot_enabled": False,
    }

    unavailable = local_client.post(
        "/api/v1/commercial/trial",
        headers={"Idempotency-Key": "local-trial"},
    )
    _assert_error(unavailable, 403, "capability_unavailable")


@pytest.mark.integration
def test_hosted_trial_gates_only_new_runs_and_records_server_activation(
    hosted_api: HostedApi,
) -> None:
    run_payload = _create_matrix(hosted_api, "pilot")
    blocked_preflight = hosted_api.client.post(
        "/api/v1/runs/preflight",
        headers=hosted_api.headers("owner"),
        json=run_payload,
    )
    _assert_error(blocked_preflight, 402, "entitlement_required")
    blocked_run = hosted_api.client.post(
        "/api/v1/runs",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "blocked-run"},
        json=run_payload,
    )
    _assert_error(blocked_run, 402, "entitlement_required")

    viewer_denied = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "viewer-trial"},
    )
    _assert_error(viewer_denied, 403, "forbidden")

    started = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "alpha-trial"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["plan_code"] == "hosted_trial"
    assert started.json()["status"] == "trialing"
    assert started.json()["can_start_runs"] is True
    replayed = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "alpha-trial"},
    )
    assert replayed.status_code == 200
    assert replayed.json() == started.json()

    signup = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "signup-alpha-owner"},
        json={"name": "signup", "source": "developer_launch", "surface": "dashboard"},
    )
    assert signup.status_code == 201, signup.text

    first = hosted_api.client.post(
        "/api/v1/runs",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "first-run"},
        json=run_payload,
    )
    assert first.status_code == 202, first.text
    first_run_id = first.json()["id"]
    assert _wait_for_terminal(hosted_api, first_run_id)["status"] == "completed"

    second_payload = _create_matrix(hosted_api, "pilot-second")
    second = hosted_api.client.post(
        "/api/v1/runs",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "second-run"},
        json=second_payload,
    )
    assert second.status_code == 202, second.text
    assert _wait_for_terminal(hosted_api, second.json()["id"])["status"] == "completed"

    third_payload = _create_matrix(hosted_api, "pilot-third")
    third = hosted_api.client.post(
        "/api/v1/runs",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "third-run"},
        json=third_payload,
    )
    assert third.status_code == 202, third.text
    assert _wait_for_terminal(hosted_api, third.json()["id"])["status"] == "completed"

    viewer_export = hosted_api.client.get(
        f"/api/v1/runs/{first_run_id}/export",
        headers=hosted_api.headers("viewer"),
    )
    assert viewer_export.status_code == 200, viewer_export.text
    before_requester_engagement = hosted_api.client.get(
        "/api/v1/commercial/funnel",
        headers=hosted_api.headers("owner"),
    )
    assert before_requester_engagement.status_code == 200
    assert before_requester_engagement.json()["activated_runs"] == 0
    assert before_requester_engagement.json()["activation_duration_sample_size"] == 0

    canceled = hosted_api.client.post(
        "/api/v1/commercial/trial/cancel",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "cancel-alpha-trial"},
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["can_start_runs"] is False

    blocked_after_cancel = hosted_api.client.post(
        "/api/v1/runs/preflight",
        headers=hosted_api.headers("owner"),
        json=run_payload,
    )
    _assert_error(blocked_after_cancel, 402, "entitlement_required")
    historical = hosted_api.client.get(
        f"/api/v1/runs/{first_run_id}",
        headers=hosted_api.headers("owner"),
    )
    assert historical.status_code == 200, historical.text
    exported = hosted_api.client.get(
        f"/api/v1/runs/{first_run_id}/export",
        headers=hosted_api.headers("owner"),
    )
    assert exported.status_code == 200, exported.text

    funnel = hosted_api.client.get(
        "/api/v1/commercial/funnel",
        headers=hosted_api.headers("owner"),
    )
    assert funnel.status_code == 200, funnel.text
    assert (
        funnel.json()["event_counts"]
        | {
            "core_job_start": 3,
            "evaluation_complete": 3,
            "result_engagement": 1,
            "second_use": 2,
            "entitlement_activation": 1,
        }
        == funnel.json()["event_counts"]
    )
    assert funnel.json()["activated_runs"] == 1
    assert funnel.json()["acquisition_sources"] == {"developer_launch": 1}
    assert funnel.json()["activation_duration_sample_size"] == 1
    assert funnel.json()["activation_duration_excluded_actors"] == 0
    assert funnel.json()["activation_duration_p50_seconds"] is not None
    assert funnel.json()["activation_duration_p90_seconds"] is not None
    assert funnel.json()["activation_duration_p90_seconds"] < 600


@pytest.mark.integration
def test_early_export_does_not_poison_later_qualifying_activation(
    hosted_api_only: HostedApi,
) -> None:
    started = hosted_api_only.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api_only.headers("owner"), "Idempotency-Key": "early-export-trial"},
    )
    assert started.status_code == 200, started.text
    signup = hosted_api_only.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api_only.headers("owner"), "Idempotency-Key": "early-signup"},
        json={"name": "signup", "source": "direct", "surface": "dashboard"},
    )
    assert signup.status_code == 201, signup.text
    matrix = _create_matrix(hosted_api_only, "early-export")
    created = hosted_api_only.client.post(
        "/api/v1/runs",
        headers={**hosted_api_only.headers("owner"), "Idempotency-Key": "early-export-run"},
        json=matrix,
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    early = hosted_api_only.client.get(
        f"/api/v1/runs/{run_id}/export",
        headers=hosted_api_only.headers("owner"),
    )
    assert early.status_code == 200, early.text
    before_completion = hosted_api_only.client.get(
        "/api/v1/commercial/funnel",
        headers=hosted_api_only.headers("owner"),
    )
    assert before_completion.status_code == 200
    assert before_completion.json()["event_counts"]["result_engagement"] == 0

    asyncio.run(hosted_api_only.container.evaluation_service.execute_run(run_id))
    assert _wait_for_terminal(hosted_api_only, run_id)["status"] == "completed"
    later = hosted_api_only.client.get(
        f"/api/v1/runs/{run_id}/export",
        headers=hosted_api_only.headers("owner"),
    )
    assert later.status_code == 200, later.text
    after_completion = hosted_api_only.client.get(
        "/api/v1/commercial/funnel",
        headers=hosted_api_only.headers("owner"),
    )
    assert after_completion.status_code == 200
    assert after_completion.json()["activated_runs"] == 1
    assert after_completion.json()["activation_duration_sample_size"] == 1

    team_request = hosted_api_only.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api_only.headers("owner"), "Idempotency-Key": "early-export-team"},
        json={
            "requested_seats": 5,
            "evaluation_frequency": "weekly",
            "security_review_required": False,
        },
    )
    assert team_request.status_code == 201, team_request.text


@pytest.mark.integration
def test_team_request_activation_and_billing_readback_are_role_and_tenant_scoped(
    hosted_api: HostedApi,
) -> None:
    landing = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "landing-alpha-viewer"},
        json={"name": "landing", "source": "direct", "surface": "overview"},
    )
    assert landing.status_code == 201, landing.text
    landing_replay = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "landing-alpha-viewer"},
        json={"name": "landing", "source": "direct", "surface": "overview"},
    )
    assert landing_replay.status_code == 200
    assert landing_replay.json()["id"] == landing.json()["id"]

    server_only = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "spoof-result"},
        json={"name": "result_engagement", "source": "direct", "surface": "run_detail"},
    )
    _assert_error(server_only, 422, "validation_error")
    browser_run_link = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "spoof-run-link"},
        json={
            "name": "upgrade_view",
            "source": "direct",
            "surface": "settings",
            "run_id": "00000000-0000-4000-8000-000000000001",
        },
    )
    _assert_error(browser_run_link, 422, "validation_error")
    checkout_without_provider = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "spoof-checkout"},
        json={"name": "checkout_start", "source": "direct", "surface": "settings"},
    )
    _assert_error(checkout_without_provider, 422, "validation_error")

    request_payload = {
        "requested_seats": 8,
        "evaluation_frequency": "several_times_week",
        "security_review_required": True,
    }
    before_success = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "too-early"},
        json=request_payload,
    )
    _assert_error(before_success, 409, "conflict")

    trial = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "team-alpha-trial"},
    )
    assert trial.status_code == 200, trial.text
    matrix = _create_matrix(hosted_api, "team-alpha")
    single_candidate_payload = {**matrix, "prompt_ids": [matrix["prompt_ids"][0]]}
    single_candidate = hosted_api.client.post(
        "/api/v1/runs",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "single-candidate-run"},
        json=single_candidate_payload,
    )
    assert single_candidate.status_code == 202, single_candidate.text
    single_run_id = single_candidate.json()["id"]
    assert _wait_for_terminal(hosted_api, single_run_id)["status"] == "completed"
    single_export = hosted_api.client.get(
        f"/api/v1/runs/{single_run_id}/export",
        headers=hosted_api.headers("owner"),
    )
    assert single_export.status_code == 200, single_export.text
    after_non_comparison = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "still-too-early"},
        json=request_payload,
    )
    _assert_error(after_non_comparison, 409, "conflict")

    comparison = hosted_api.client.post(
        "/api/v1/runs",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "qualifying-comparison"},
        json=matrix,
    )
    assert comparison.status_code == 202, comparison.text
    comparison_run_id = comparison.json()["id"]
    assert _wait_for_terminal(hosted_api, comparison_run_id)["status"] == "completed"
    comparison_export = hosted_api.client.get(
        f"/api/v1/runs/{comparison_run_id}/export",
        headers=hosted_api.headers("owner"),
    )
    assert comparison_export.status_code == 200, comparison_export.text

    created = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "team-interest"},
        json=request_payload,
    )
    assert created.status_code == 201, created.text
    replayed = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "team-interest"},
        json=request_payload,
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["id"] == created.json()["id"]
    duplicate_pending = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "another-team-interest"},
        json=request_payload,
    )
    _assert_error(duplicate_pending, 409, "conflict")

    foreign_trial = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={
            **hosted_api.headers("foreign_owner", BETA_WORKSPACE_ID),
            "Idempotency-Key": "team-beta-trial",
        },
    )
    assert foreign_trial.status_code == 200, foreign_trial.text
    foreign_matrix = _create_matrix(
        hosted_api,
        "team-beta",
        actor="foreign_owner",
        workspace_id=BETA_WORKSPACE_ID,
    )
    foreign_run = hosted_api.client.post(
        "/api/v1/runs",
        headers={
            **hosted_api.headers("foreign_owner", BETA_WORKSPACE_ID),
            "Idempotency-Key": "beta-comparison",
        },
        json=foreign_matrix,
    )
    assert foreign_run.status_code == 202, foreign_run.text
    foreign_run_id = foreign_run.json()["id"]
    assert (
        _wait_for_terminal(
            hosted_api,
            foreign_run_id,
            actor="foreign_owner",
            workspace_id=BETA_WORKSPACE_ID,
        )["status"]
        == "completed"
    )
    foreign_export = hosted_api.client.get(
        f"/api/v1/runs/{foreign_run_id}/export",
        headers=hosted_api.headers("foreign_owner", BETA_WORKSPACE_ID),
    )
    assert foreign_export.status_code == 200, foreign_export.text

    foreign_created = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={
            **hosted_api.headers("foreign_owner", BETA_WORKSPACE_ID),
            "Idempotency-Key": "team-interest",
        },
        json=request_payload,
    )
    assert foreign_created.status_code == 201, foreign_created.text
    assert foreign_created.json()["workspace_id"] == BETA_WORKSPACE_ID

    viewer_list = hosted_api.client.get(
        "/api/v1/commercial/team-requests",
        headers=hosted_api.headers("viewer"),
    )
    _assert_error(viewer_list, 403, "forbidden")
    listed = hosted_api.client.get(
        "/api/v1/commercial/team-requests",
        headers=hosted_api.headers("owner"),
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]

    canceled = hosted_api.client.post(
        f"/api/v1/commercial/team-requests/{created.json()['id']}/cancel",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "cancel-team-interest"},
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"
    cancel_replay = hosted_api.client.post(
        f"/api/v1/commercial/team-requests/{created.json()['id']}/cancel",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "cancel-team-interest"},
    )
    assert cancel_replay.status_code == 200, cancel_replay.text

    replacement = hosted_api.client.post(
        "/api/v1/commercial/team-requests",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "replacement-interest"},
        json=request_payload,
    )
    assert replacement.status_code == 201, replacement.text
    replacement_canceled = hosted_api.client.post(
        f"/api/v1/commercial/team-requests/{replacement.json()['id']}/cancel",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "cancel-replacement"},
    )
    assert replacement_canceled.status_code == 200, replacement_canceled.text

    billing = hosted_api.client.get(
        "/api/v1/commercial/billing-events",
        headers=hosted_api.headers("owner"),
    )
    assert billing.status_code == 200, billing.text
    assert {event["event_type"] for event in billing.json()} == {
        "entitlement.trial_activated",
        "team_request.created",
        "team_request.canceled",
    }
    assert all(event["workspace_id"] == ALPHA_WORKSPACE_ID for event in billing.json())
    assert all("provider_event_id" not in event for event in billing.json())

    activation_events = hosted_api.client.get(
        "/api/v1/commercial/events",
        headers=hosted_api.headers("owner"),
    )
    assert activation_events.status_code == 200, activation_events.text
    assert {
        "landing",
        "entitlement_activation",
        "core_job_start",
        "second_use",
        "evaluation_complete",
        "result_engagement",
        "team_request_submitted",
    }.issubset({event["name"] for event in activation_events.json()})
    assert all(event["workspace_id"] == ALPHA_WORKSPACE_ID for event in activation_events.json())

    funnel_denied = hosted_api.client.get(
        "/api/v1/commercial/funnel",
        headers=hosted_api.headers("viewer"),
    )
    _assert_error(funnel_denied, 403, "forbidden")
    funnel = hosted_api.client.get(
        "/api/v1/commercial/funnel",
        headers=hosted_api.headers("owner"),
    )
    assert funnel.status_code == 200, funnel.text
    assert funnel.json()["event_counts"]["landing"] == 1
    assert funnel.json()["event_counts"]["evaluation_complete"] == 1
    assert funnel.json()["event_counts"]["team_request_submitted"] == 2
    assert funnel.json()["activated_runs"] == 1
    assert funnel.json()["pending_team_requests"] == 0
    assert funnel.json()["total_team_requests"] == 2


@pytest.mark.integration
def test_client_activation_events_are_namespaced_first_touch_and_bounded(
    hosted_api: HostedApi,
) -> None:
    signup = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "first-signup"},
        json={"name": "signup", "source": "github_launch", "surface": "dashboard"},
    )
    assert signup.status_code == 201, signup.text
    later_source = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "later-signup"},
        json={"name": "signup", "source": "community_reply", "surface": "dashboard"},
    )
    assert later_source.status_code == 200, later_source.text
    assert later_source.json()["id"] == signup.json()["id"]
    assert later_source.json()["source"] == "github_launch"

    hostile_key = "evaluation-complete:known-run"
    hostile = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": hostile_key},
        json={"name": "landing", "source": "direct", "surface": "dashboard"},
    )
    assert hostile.status_code == 201, hostile.text
    with hosted_api.container.session_factory() as session:
        client_event = session.get(ActivationEvent, hostile.json()["id"])
        assert client_event is not None
        assert client_event.event_key == f"client:{VIEWER_USER_ID}:{hostile_key}"
        server_event, created = ActivationRecorder(session).record(
            workspace_id=ALPHA_WORKSPACE_ID,
            actor_user_id=OWNER_USER_ID,
            name=ActivationEventName.EVALUATION_COMPLETE,
            event_key=hostile_key,
            source="worker",
            metadata={"surface": "worker"},
        )
        assert created is True
        assert server_event.id != client_event.id
        session.commit()

    for index in range(98):
        accepted = hosted_api.client.post(
            "/api/v1/commercial/events",
            headers={
                **hosted_api.headers("viewer"),
                "Idempotency-Key": f"bounded-landing-{index}",
            },
            json={"name": "landing", "source": "direct", "surface": "dashboard"},
        )
        assert accepted.status_code == 201, accepted.text
    limited = hosted_api.client.post(
        "/api/v1/commercial/events",
        headers={**hosted_api.headers("viewer"), "Idempotency-Key": "over-daily-limit"},
        json={"name": "landing", "source": "direct", "surface": "dashboard"},
    )
    _assert_error(limited, 429, "limit_exceeded")

    history = hosted_api.client.get(
        "/api/v1/commercial/events",
        headers=hosted_api.headers("owner"),
    )
    assert history.status_code == 200, history.text
    assert len(history.json()) == 100


@pytest.mark.integration
def test_entitlement_readback_enforces_seat_limit_and_trial_expiration(
    hosted_api: HostedApi,
) -> None:
    started = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "bounded-trial"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["can_start_runs"] is True

    extra_membership_ids: list[str] = []
    with hosted_api.container.session_factory() as session:
        workspace = session.get(Workspace, ALPHA_WORKSPACE_ID)
        assert workspace is not None
        for ordinal in range(4):
            user = User(
                id=f"30000000-0000-4000-8000-{100 + ordinal:012d}",
                issuer=ISSUER,
                subject=f"commercial-extra-{ordinal}",
                display_name=f"Extra member {ordinal}",
            )
            membership = WorkspaceMembership(
                workspace=workspace,
                user=user,
                role=WorkspaceRole.VIEWER,
            )
            session.add_all([user, membership])
            session.flush()
            extra_membership_ids.append(membership.id)
        session.commit()

    over_seat_limit = hosted_api.client.get(
        "/api/v1/commercial/entitlement",
        headers=hosted_api.headers("viewer"),
    )
    assert over_seat_limit.status_code == 200, over_seat_limit.text
    assert over_seat_limit.json()["status"] == "trialing"
    assert over_seat_limit.json()["active_memberships"] == 6
    assert over_seat_limit.json()["seat_limit"] == 5
    assert over_seat_limit.json()["can_start_runs"] is False

    with hosted_api.container.session_factory() as session:
        for membership_id in extra_membership_ids:
            stored_membership = session.get(WorkspaceMembership, membership_id)
            assert stored_membership is not None
            stored_membership.status = RecordStatus.SUSPENDED
        entitlement = session.scalar(
            select(WorkspaceEntitlement).where(
                WorkspaceEntitlement.workspace_id == ALPHA_WORKSPACE_ID
            )
        )
        assert entitlement is not None
        entitlement.current_period_end = utc_now() - timedelta(seconds=1)
        session.commit()

    expired = hosted_api.client.get(
        "/api/v1/commercial/entitlement",
        headers=hosted_api.headers("viewer"),
    )
    assert expired.status_code == 200, expired.text
    assert expired.json()["status"] == "expired"
    assert expired.json()["active_memberships"] == 2
    assert expired.json()["can_start_runs"] is False


@pytest.mark.integration
def test_commercial_evidence_events_are_append_only(hosted_api: HostedApi) -> None:
    started = hosted_api.client.post(
        "/api/v1/commercial/trial",
        headers={**hosted_api.headers("owner"), "Idempotency-Key": "immutable-trial"},
    )
    assert started.status_code == 200, started.text

    with hosted_api.container.session_factory() as session:
        billing_event = session.scalar(select(BillingEvent))
        assert billing_event is not None
        billing_event.event_type = "tampered"
        with pytest.raises(ImmutableProvenanceError, match="append-only"):
            session.flush()
        session.rollback()

        activation_event = session.scalar(select(ActivationEvent))
        assert activation_event is not None
        session.delete(activation_event)
        with pytest.raises(ImmutableProvenanceError, match="append-only"):
            session.flush()
        session.rollback()


@pytest.mark.integration
def test_metrics_token_is_server_enforced_without_secret_disclosure(
    hosted_api: HostedApi,
) -> None:
    unavailable = hosted_api.client.get("/metrics")
    hosted_api.container.settings.metrics_bearer_token = SecretStr("metrics-secret-value")

    missing = hosted_api.client.get("/metrics")
    wrong = hosted_api.client.get(
        "/metrics",
        headers={"Authorization": "Bearer wrong-value"},
    )
    authorized = hosted_api.client.get(
        "/metrics",
        headers={"Authorization": "Bearer metrics-secret-value"},
    )

    assert unavailable.status_code == 503
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "metrics-secret-value" not in missing.text
    assert "metrics-secret-value" not in wrong.text
    assert authorized.status_code == 200
    assert "text/plain" in authorized.headers["content-type"]
