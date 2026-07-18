from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select

from evalforge.api.app import create_app
from evalforge.config import Settings
from evalforge.container import AppContainer, build_container
from evalforge.models import AuditEvent, RecordStatus, User, Workspace, WorkspaceMembership
from evalforge.security.auth import AuthenticatedPrincipal, AuthenticationError
from evalforge.security.permissions import WorkspaceRole

ISSUER = "https://identity.example.test"
WORKSPACE_ALPHA_ID = "20000000-0000-4000-8000-000000000001"
WORKSPACE_BETA_ID = "20000000-0000-4000-8000-000000000002"

USER_IDS = {
    "viewer": "20000000-0000-4000-8000-000000000011",
    "editor": "20000000-0000-4000-8000-000000000012",
    "admin": "20000000-0000-4000-8000-000000000013",
    "owner": "20000000-0000-4000-8000-000000000014",
    "foreign_owner": "20000000-0000-4000-8000-000000000015",
}
SUBJECTS = {actor: f"subject-{actor}" for actor in USER_IDS}

# These short labels are intentionally synthetic fixture credentials, not JWTs or
# copied secrets. The stub consumes them request-locally and never persists them.
FIXTURE_CREDENTIALS = {actor: f"fixture-{actor}" for actor in USER_IDS}


class StubAuthBackend:
    """Resolve synthetic bearer labels without retaining request credentials."""

    def __init__(self, principals: Mapping[str, AuthenticatedPrincipal]) -> None:
        self._principals = dict(principals)

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        if authorization is None:
            raise AuthenticationError
        scheme, separator, credential = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not separator or not credential:
            raise AuthenticationError(invalid=True)
        principal = self._principals.get(credential)
        if principal is None:
            raise AuthenticationError(invalid=True)
        return principal


@dataclass(frozen=True, slots=True)
class SharedApi:
    client: TestClient
    container: AppContainer

    def headers(self, actor: str, workspace_id: str = WORKSPACE_ALPHA_ID) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {FIXTURE_CREDENTIALS[actor]}",
            "X-EvalForge-Workspace-ID": workspace_id,
        }


@pytest.fixture
def local_api_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'local-authorization.db'}",
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
def shared_api(tmp_path: Path) -> Iterator[SharedApi]:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'shared-authorization.db'}",
        auto_migrate=False,
        seed_demo=False,
        real_runs_enabled=False,
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience="evalforge-api",
        oidc_jwks_url=f"{ISSUER}/jwks.json",
        public_base_url="https://evalforge.example.test",
    )
    container = build_container(settings, migrate=True)
    _provision_shared_identities(container)
    container.authenticator = StubAuthBackend(
        {
            FIXTURE_CREDENTIALS[actor]: AuthenticatedPrincipal(
                user_id=None,
                issuer=ISSUER,
                subject=SUBJECTS[actor],
                display_name=f"Untrusted {actor} claim",
                is_local=False,
            )
            for actor in USER_IDS
        }
    )
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            yield SharedApi(client=client, container=container)
    finally:
        container.engine.dispose()


def _provision_shared_identities(container: AppContainer) -> None:
    with container.session_factory() as session:
        alpha = Workspace(id=WORKSPACE_ALPHA_ID, slug="alpha", name="Alpha quality")
        beta = Workspace(id=WORKSPACE_BETA_ID, slug="beta", name="Beta quality")
        users = {
            actor: User(
                id=user_id,
                issuer=ISSUER,
                subject=SUBJECTS[actor],
                display_name=actor.replace("_", " ").title(),
                email=f"{actor}@example.test",
            )
            for actor, user_id in USER_IDS.items()
        }
        memberships = [
            WorkspaceMembership(
                workspace=alpha,
                user=users["viewer"],
                role=WorkspaceRole.VIEWER,
            ),
            WorkspaceMembership(
                workspace=alpha,
                user=users["editor"],
                role=WorkspaceRole.EDITOR,
            ),
            WorkspaceMembership(
                workspace=alpha,
                user=users["admin"],
                role=WorkspaceRole.ADMIN,
            ),
            WorkspaceMembership(
                workspace=alpha,
                user=users["owner"],
                role=WorkspaceRole.OWNER,
            ),
            WorkspaceMembership(
                workspace=beta,
                user=users["owner"],
                role=WorkspaceRole.OWNER,
            ),
            WorkspaceMembership(
                workspace=beta,
                user=users["foreign_owner"],
                role=WorkspaceRole.OWNER,
            ),
        ]
        session.add_all([alpha, beta, *users.values(), *memberships])
        session.commit()


def _assert_error(response: Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"]


def _model_payload(name: str) -> dict[str, object]:
    return {
        "name": name,
        "provider": "demo",
        "model_name": "demo-reliable",
        "api_mode": "deterministic",
        "generation_parameters": {
            "temperature": 0.0,
            "max_output_tokens": 32,
            "seed": 7,
        },
        "metadata_json": {"synthetic": True},
    }


def _create_matrix(
    api: SharedApi,
    *,
    actor: str,
    workspace_id: str,
    prefix: str,
    idempotency_key: str,
) -> dict[str, str]:
    headers = api.headers(actor, workspace_id)
    dataset_response = api.client.post(
        "/api/v1/datasets",
        headers=headers,
        json={
            "name": f"{prefix} dataset",
            "cases": [
                {
                    "external_id": f"{prefix}-case",
                    "position": 0,
                    "input_text": "Return the supplied reference.",
                    "expected_output": "verified",
                }
            ],
        },
    )
    assert dataset_response.status_code == 201, dataset_response.text
    prompt_response = api.client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "name": f"{prefix} prompt",
            "system_template": "Use the reference.",
            "user_template": "{input}",
        },
    )
    assert prompt_response.status_code == 201, prompt_response.text
    model_response = api.client.post(
        "/api/v1/models",
        headers=headers,
        json=_model_payload(f"{prefix} model"),
    )
    assert model_response.status_code == 201, model_response.text
    run_response = api.client.post(
        "/api/v1/runs",
        headers={**headers, "Idempotency-Key": idempotency_key},
        json={
            "name": f"{prefix} run",
            "dataset_id": dataset_response.json()["id"],
            "prompt_ids": [prompt_response.json()["id"]],
            "model_ids": [model_response.json()["id"]],
        },
    )
    assert run_response.status_code == 202, run_response.text
    return {
        "dataset_id": dataset_response.json()["id"],
        "prompt_id": prompt_response.json()["id"],
        "model_id": model_response.json()["id"],
        "run_id": run_response.json()["id"],
    }


@pytest.mark.integration
def test_local_mode_keeps_headerless_api_compatibility(local_api_client: TestClient) -> None:
    created = local_api_client.post(
        "/api/v1/datasets",
        json={"name": "Headerless local dataset"},
    )

    assert created.status_code == 201, created.text
    listed = local_api_client.get("/api/v1/datasets")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]


@pytest.mark.integration
def test_shared_mode_requires_bearer_and_explicit_workspace_selection(
    shared_api: SharedApi,
) -> None:
    missing = shared_api.client.get("/api/v1/session")
    _assert_error(missing, 401, "authentication_required")
    assert missing.headers["www-authenticate"] == "Bearer"

    invalid_credential = "fixture-unknown"
    invalid = shared_api.client.get(
        "/api/v1/session",
        headers={"Authorization": f"Bearer {invalid_credential}"},
    )
    _assert_error(invalid, 401, "authentication_required")
    assert invalid.headers["www-authenticate"] == "Bearer"
    assert invalid_credential not in invalid.text

    session_response = shared_api.client.get(
        "/api/v1/session",
        headers={"Authorization": f"Bearer {FIXTURE_CREDENTIALS['owner']}"},
    )
    assert session_response.status_code == 200, session_response.text
    assert session_response.json()["user_id"] == USER_IDS["owner"]
    assert session_response.json()["display_name"] == "Owner"
    assert session_response.json()["auth_mode"] == "oidc"
    assert session_response.json()["workspaces"] == [
        {"id": WORKSPACE_ALPHA_ID, "name": "Alpha quality", "role": "owner"},
        {"id": WORKSPACE_BETA_ID, "name": "Beta quality", "role": "owner"},
    ]

    workspaces = shared_api.client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {FIXTURE_CREDENTIALS['owner']}"},
    )
    assert workspaces.status_code == 200
    assert workspaces.json() == session_response.json()["workspaces"]

    no_workspace = shared_api.client.get(
        "/api/v1/datasets",
        headers={"Authorization": f"Bearer {FIXTURE_CREDENTIALS['owner']}"},
    )
    _assert_error(no_workspace, 403, "forbidden")

    unassigned_workspace = shared_api.client.get(
        "/api/v1/datasets",
        headers={
            "Authorization": f"Bearer {FIXTURE_CREDENTIALS['viewer']}",
            "X-EvalForge-Workspace-ID": WORKSPACE_BETA_ID,
        },
    )
    _assert_error(unassigned_workspace, 403, "forbidden")

    for workspace_id, label in (
        (WORKSPACE_ALPHA_ID, "Same name"),
        (WORKSPACE_BETA_ID, "Same name"),
    ):
        created = shared_api.client.post(
            "/api/v1/datasets",
            headers=shared_api.headers("owner", workspace_id),
            json={"name": label},
        )
        assert created.status_code == 201, created.text
        assert created.json()["workspace_id"] == workspace_id

    for workspace_id in (WORKSPACE_ALPHA_ID, WORKSPACE_BETA_ID):
        listed = shared_api.client.get(
            "/api/v1/datasets",
            headers=shared_api.headers("owner", workspace_id),
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert {item["workspace_id"] for item in listed.json()["items"]} == {workspace_id}


@pytest.mark.integration
def test_workspace_roles_gate_reads_and_mutations(shared_api: SharedApi) -> None:
    viewer_headers = shared_api.headers("viewer")
    editor_headers = shared_api.headers("editor")
    admin_headers = shared_api.headers("admin")
    owner_headers = shared_api.headers("owner")

    assert shared_api.client.get("/api/v1/datasets", headers=viewer_headers).status_code == 200
    viewer_mutation = shared_api.client.post(
        "/api/v1/datasets",
        headers=viewer_headers,
        json={"name": "Viewer must not create"},
    )
    _assert_error(viewer_mutation, 403, "forbidden")

    dataset = shared_api.client.post(
        "/api/v1/datasets",
        headers=editor_headers,
        json={
            "name": "Editor dataset",
            "cases": [
                {
                    "external_id": "editor-case",
                    "position": 0,
                    "input_text": "Give the expected word.",
                    "expected_output": "allowed",
                }
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    prompt = shared_api.client.post(
        "/api/v1/prompts",
        headers=editor_headers,
        json={"name": "Editor prompt", "user_template": "{input}"},
    )
    assert prompt.status_code == 201, prompt.text

    editor_model = shared_api.client.post(
        "/api/v1/models",
        headers=editor_headers,
        json=_model_payload("Editor model must be denied"),
    )
    _assert_error(editor_model, 403, "forbidden")

    model = shared_api.client.post(
        "/api/v1/models",
        headers=admin_headers,
        json=_model_payload("Admin model"),
    )
    assert model.status_code == 201, model.text

    run_request = {
        "name": "Editor authorized run",
        "requested_by": "Spoofed owner name",
        "dataset_id": dataset.json()["id"],
        "prompt_ids": [prompt.json()["id"]],
        "model_ids": [model.json()["id"]],
    }
    run = shared_api.client.post(
        "/api/v1/runs",
        headers={**editor_headers, "Idempotency-Key": "editor-authorized-run"},
        json=run_request,
    )
    assert run.status_code == 202, run.text

    viewer_read = shared_api.client.get(
        f"/api/v1/runs/{run.json()['id']}",
        headers=viewer_headers,
    )
    assert viewer_read.status_code == 200, viewer_read.text
    viewer_export = shared_api.client.get(
        f"/api/v1/runs/{run.json()['id']}/export",
        headers=viewer_headers,
        params={"format": "json"},
    )
    assert viewer_export.status_code == 200, viewer_export.text
    assert "requested_by" not in viewer_export.json()
    viewer_full_export = shared_api.client.get(
        f"/api/v1/runs/{run.json()['id']}/export",
        headers=viewer_headers,
        params={"format": "json", "disclosure_profile": "full_evidence"},
    )
    assert viewer_full_export.status_code == 200, viewer_full_export.text
    assert viewer_full_export.json()["requested_by"] == "Editor"
    viewer_package = shared_api.client.get(
        f"/api/v1/runs/{run.json()['id']}/export",
        headers=viewer_headers,
        params={"format": "package"},
    )
    assert viewer_package.status_code == 200, viewer_package.text
    assert viewer_package.json()["payload"]["disclosure_profile"] == "content_redacted"
    viewer_cancel = shared_api.client.post(
        f"/api/v1/runs/{run.json()['id']}/cancel",
        headers=viewer_headers,
    )
    _assert_error(viewer_cancel, 403, "forbidden")

    owner_model_mutation = shared_api.client.patch(
        f"/api/v1/models/{model.json()['id']}",
        headers=owner_headers,
        json={"enabled": False},
    )
    assert owner_model_mutation.status_code == 200, owner_model_mutation.text
    assert owner_model_mutation.json()["enabled"] is False


@pytest.mark.integration
def test_suspended_membership_is_denied_without_object_disclosure(shared_api: SharedApi) -> None:
    assert (
        shared_api.client.get(
            "/api/v1/datasets",
            headers=shared_api.headers("viewer"),
        ).status_code
        == 200
    )
    with shared_api.container.session_factory() as session:
        membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == WORKSPACE_ALPHA_ID,
                WorkspaceMembership.user_id == USER_IDS["viewer"],
            )
        )
        assert membership is not None
        membership.status = RecordStatus.SUSPENDED
        session.commit()

    denied = shared_api.client.get(
        "/api/v1/datasets",
        headers=shared_api.headers("viewer"),
    )
    _assert_error(denied, 403, "forbidden")
    assert WORKSPACE_ALPHA_ID not in denied.text


@pytest.mark.integration
def test_cors_preflight_allows_identity_workspace_and_idempotency_headers(
    shared_api: SharedApi,
) -> None:
    response = shared_api.client.options(
        "/api/v1/runs",
        headers={
            "Origin": "http://127.0.0.1:8501",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,idempotency-key,x-evalforge-workspace-id,x-request-id"
            ),
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8501"
    allowed = {
        value.strip().casefold()
        for value in response.headers["access-control-allow-headers"].split(",")
    }
    assert {
        "authorization",
        "content-type",
        "idempotency-key",
        "x-evalforge-workspace-id",
        "x-request-id",
    }.issubset(allowed)


@pytest.mark.integration
def test_cross_tenant_ids_and_idempotency_do_not_cross_boundaries(shared_api: SharedApi) -> None:
    alpha = _create_matrix(
        shared_api,
        actor="owner",
        workspace_id=WORKSPACE_ALPHA_ID,
        prefix="Alpha isolation",
        idempotency_key="tenant-local-key",
    )
    beta = _create_matrix(
        shared_api,
        actor="foreign_owner",
        workspace_id=WORKSPACE_BETA_ID,
        prefix="Beta isolation",
        idempotency_key="tenant-local-key",
    )

    assert alpha["run_id"] != beta["run_id"]
    replay = shared_api.client.post(
        "/api/v1/runs",
        headers={
            **shared_api.headers("owner", WORKSPACE_ALPHA_ID),
            "Idempotency-Key": "tenant-local-key",
        },
        json={
            "name": "Alpha isolation run",
            "dataset_id": alpha["dataset_id"],
            "prompt_ids": [alpha["prompt_id"]],
            "model_ids": [alpha["model_id"]],
        },
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == alpha["run_id"]

    beta_headers = shared_api.headers("foreign_owner", WORKSPACE_BETA_ID)
    for path in (
        f"/api/v1/datasets/{alpha['dataset_id']}",
        f"/api/v1/runs/{alpha['run_id']}",
    ):
        response = shared_api.client.get(path, headers=beta_headers)
        _assert_error(response, 404, "not_found")
        assert alpha["dataset_id"] not in response.text
        assert alpha["run_id"] not in response.text

    cross_tenant_update = shared_api.client.patch(
        f"/api/v1/datasets/{alpha['dataset_id']}",
        headers=beta_headers,
        json={"name": "Must remain invisible"},
    )
    _assert_error(cross_tenant_update, 404, "not_found")
    assert alpha["dataset_id"] not in cross_tenant_update.text

    cross_tenant_cancel = shared_api.client.post(
        f"/api/v1/runs/{alpha['run_id']}/cancel",
        headers=beta_headers,
    )
    _assert_error(cross_tenant_cancel, 404, "not_found")
    assert alpha["run_id"] not in cross_tenant_cancel.text


@pytest.mark.integration
def test_audit_events_are_actor_attributed_and_content_minimized(shared_api: SharedApi) -> None:
    editor_headers = shared_api.headers("editor")
    admin_headers = shared_api.headers("admin")
    canary = "private-input-prompt-output-reference-canary"

    dataset = shared_api.client.post(
        "/api/v1/datasets",
        headers=editor_headers,
        json={
            "name": "Audit dataset",
            "description": canary,
            "cases": [
                {
                    "external_id": "audit-case",
                    "position": 0,
                    "input_text": canary,
                    "expected_output": "safe result",
                }
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    prompt = shared_api.client.post(
        "/api/v1/prompts",
        headers=editor_headers,
        json={"name": "Audit prompt", "user_template": f"{{input}} {canary}"},
    )
    assert prompt.status_code == 201, prompt.text
    model = shared_api.client.post(
        "/api/v1/models",
        headers=admin_headers,
        json=_model_payload("Audit model"),
    )
    assert model.status_code == 201, model.text
    run = shared_api.client.post(
        "/api/v1/runs",
        headers={
            **editor_headers,
            "Idempotency-Key": "audit-run-key",
            "X-Request-ID": "audit-run-create-proof",
        },
        json={
            "name": "Audit run",
            "dataset_id": dataset.json()["id"],
            "prompt_ids": [prompt.json()["id"]],
            "model_ids": [model.json()["id"]],
        },
    )
    assert run.status_code == 202, run.text
    export_requests = (
        ("json", "content_redacted", "audit-export-json-proof"),
        ("csv", "full_evidence", "audit-export-csv-proof"),
        ("package", "content_redacted", "audit-export-package-proof"),
    )
    for export_format, disclosure_profile, request_id in export_requests:
        exported = shared_api.client.get(
            f"/api/v1/runs/{run.json()['id']}/export",
            headers={**editor_headers, "X-Request-ID": request_id},
            params={
                "format": export_format,
                "disclosure_profile": disclosure_profile,
            },
        )
        assert exported.status_code == 200, exported.text

    with shared_api.container.session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == WORKSPACE_ALPHA_ID)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )

    assert {event.action for event in events} >= {
        "dataset.create",
        "prompt.create",
        "model.create",
        "run.create",
        "run.export",
    }
    expected_actors = {
        "dataset.create": USER_IDS["editor"],
        "prompt.create": USER_IDS["editor"],
        "model.create": USER_IDS["admin"],
        "run.create": USER_IDS["editor"],
        "run.export": USER_IDS["editor"],
    }
    for event in events:
        if event.action not in expected_actors:
            continue
        assert event.actor_user_id == expected_actors[event.action]
        assert event.outcome == "success"
        assert event.resource_id
        normalized_keys = {
            "".join(character for character in key.casefold() if character.isalnum())
            for key in event.metadata_json
        }
        assert not normalized_keys & {
            "token",
            "secret",
            "authorization",
            "prompt",
            "context",
            "input",
            "output",
            "reference",
        }

    serialized_metadata = json.dumps(
        [event.metadata_json for event in events],
        sort_keys=True,
    )
    assert canary not in serialized_metadata
    for credential in FIXTURE_CREDENTIALS.values():
        assert credential not in serialized_metadata
    request_ids = {event.action: event.request_id for event in events if event.request_id}
    assert request_ids["run.create"] == "audit-run-create-proof"
    export_events = [event for event in events if event.action == "run.export"]
    assert {event.request_id for event in export_events} == {
        "audit-export-json-proof",
        "audit-export-csv-proof",
        "audit-export-package-proof",
    }
    assert {
        (
            event.metadata_json["format"],
            event.metadata_json["disclosure_profile"],
        )
        for event in export_events
    } == {
        ("json", "content_redacted"),
        ("csv", "full_evidence"),
        ("package", "content_redacted"),
    }
    package_event = next(
        event for event in export_events if event.metadata_json["format"] == "package"
    )
    assert len(package_event.metadata_json["package_sha256"]) == 64
