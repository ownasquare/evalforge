from __future__ import annotations

import json
from pathlib import Path

import pytest
from click import unstyle
from sqlalchemy import func, select
from typer.testing import CliRunner

from evalforge import cli
from evalforge.config import Settings
from evalforge.container import apply_migrations
from evalforge.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from evalforge.models import (
    AuditEvent,
    Dataset,
    ModelProfile,
    PromptTemplate,
    RecordStatus,
    User,
    Workspace,
    WorkspaceMembership,
)
from evalforge.repositories import Repositories
from evalforge.schemas import EvaluationRunCreate, MetricConfiguration
from evalforge.security.permissions import WorkspaceRole, local_workspace_context

ISSUER = "https://identity.example.test"
SUBJECT = "operator-fixture-subject"
EMAIL = "member@example.test"


def _plain_cli_output(output: str) -> str:
    """Remove Rich styling and make assertions independent of terminal wrapping."""

    return " ".join(unstyle(output).split())


@pytest.fixture
def shared_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'identity-cli.db'}",
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience="evalforge-api",
        oidc_jwks_url=f"{ISSUER}/jwks.json",
        public_base_url="https://evalforge.example.test",
        auto_migrate=False,
        seed_demo=False,
    )
    apply_migrations(settings)
    return settings


@pytest.fixture
def identity_runner(monkeypatch: pytest.MonkeyPatch, shared_settings: Settings) -> CliRunner:
    monkeypatch.setattr(cli, "get_settings", lambda: shared_settings)
    return CliRunner()


def _invoke_json(runner: CliRunner, arguments: list[str]) -> dict[str, object]:
    result = runner.invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _create_workspace(
    runner: CliRunner,
    *,
    slug: str = "quality-team",
    name: str = "Quality team",
) -> dict[str, object]:
    return _invoke_json(
        runner,
        ["workspace-create", "--slug", slug, "--name", name],
    )


def _provision(
    runner: CliRunner,
    *,
    role: str = "viewer",
    workspace_slug: str = "quality-team",
    subject: str = SUBJECT,
    email: str = EMAIL,
) -> dict[str, object]:
    return _invoke_json(
        runner,
        [
            "membership-provision",
            "--workspace",
            workspace_slug,
            "--issuer",
            ISSUER,
            "--subject",
            subject,
            "--role",
            role,
            "--display-name",
            "Quality member",
            "--email",
            email,
        ],
    )


def _seed_workspace(runner: CliRunner, workspace_slug: str = "quality-team") -> dict[str, object]:
    return _invoke_json(runner, ["seed", "--workspace", workspace_slug])


def _create_demo_run(settings: Settings, workspace_slug: str | None) -> str:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            context = (
                cli._operator_workspace_context(session, workspace_slug)
                if workspace_slug is not None
                else local_workspace_context()
            )
            dataset = session.scalar(
                select(Dataset)
                .where(Dataset.workspace_id == context.workspace_id)
                .order_by(Dataset.name)
            )
            prompt = session.scalar(
                select(PromptTemplate)
                .where(PromptTemplate.workspace_id == context.workspace_id)
                .order_by(PromptTemplate.name)
            )
            model = session.scalar(
                select(ModelProfile)
                .where(ModelProfile.workspace_id == context.workspace_id)
                .order_by(ModelProfile.name)
            )
            assert dataset is not None
            assert prompt is not None
            assert model is not None
            run = Repositories(session, context).runs.create(
                EvaluationRunCreate(
                    dataset_id=dataset.id,
                    prompt_ids=[prompt.id],
                    model_ids=[model.id],
                    name="CLI export fixture",
                    metrics=[
                        MetricConfiguration(
                            name="correctness",
                            version="lexical-correctness-v1",
                        )
                    ],
                ),
                application_version="0.1.0",
                executor_type="cli-test",
            )
            return run.id
    finally:
        engine.dispose()


def test_identity_commands_expose_claim_keys_but_no_credential_parameter() -> None:
    runner = CliRunner()
    for command in ("membership-provision", "membership-revoke"):
        result = runner.invoke(cli.app, [command, "--help"])
        assert result.exit_code == 0
        output = _plain_cli_output(result.output)
        assert "--issuer" in output
        assert "--subject" in output
        assert "--token" not in output
        assert "bearer" not in output.casefold()


def test_database_worker_rejects_sqlite_before_container_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
    )
    container_built = False

    def forbidden_build(*_args: object, **_kwargs: object) -> object:
        nonlocal container_built
        container_built = True
        raise AssertionError("container must not be built")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_container", forbidden_build)

    result = CliRunner().invoke(cli.app, ["worker"])

    assert result.exit_code != 0
    assert "requires PostgreSQL" in result.output
    assert container_built is False


def test_database_worker_honors_single_migration_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://evalforge:password@database.test/evalforge",
        auto_migrate=False,
        seed_demo=False,
    )
    observed_migrate: list[bool] = []

    class FakeExecutor:
        async def start(self) -> None:
            raise KeyboardInterrupt

    class FakeContainer:
        executor = FakeExecutor()

        async def close(self) -> None:
            return None

    def fake_build(_settings: Settings, *, migrate: bool) -> FakeContainer:
        observed_migrate.append(migrate)
        return FakeContainer()

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_container", fake_build)

    result = CliRunner().invoke(cli.app, ["worker"])

    assert result.exit_code == 0, result.output
    assert observed_migrate == [False]


def test_workspace_and_membership_provisioning_are_idempotent_and_content_minimized(
    identity_runner: CliRunner,
    shared_settings: Settings,
) -> None:
    created = _create_workspace(identity_runner)
    existing = _create_workspace(identity_runner)
    assert created["status"] == "created"
    assert existing == {**created, "status": "already_exists"}

    provisioned = _provision(identity_runner)
    updated = _provision(identity_runner, role="admin")
    unchanged = _provision(identity_runner, role="admin")
    assert provisioned["status"] == "created"
    assert provisioned["role"] == "viewer"
    assert updated["status"] == "updated"
    assert updated["role"] == "admin"
    assert unchanged["status"] == "already_active"

    all_output = json.dumps([created, existing, provisioned, updated, unchanged])
    assert ISSUER not in all_output
    assert SUBJECT not in all_output
    assert EMAIL not in all_output

    engine = create_database_engine(shared_settings)
    session = create_session_factory(engine)()
    try:
        workspace = session.scalar(select(Workspace).where(Workspace.slug == "quality-team"))
        user = session.scalar(
            select(User).where(
                User.issuer == str(shared_settings.oidc_issuer),
                User.subject == SUBJECT,
            )
        )
        assert workspace is not None
        assert user is not None
        membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        assert membership is not None
        assert membership.role == WorkspaceRole.ADMIN
        assert membership.status == RecordStatus.ACTIVE
        assert user.display_name == "Quality member"
        assert user.email == EMAIL

        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == workspace.id)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        assert [event.action for event in events] == [
            "operator.workspace.create",
            "operator.membership.provision",
            "operator.membership.provision",
            "operator.membership.provision",
        ]
        serialized_events = json.dumps(
            [
                {
                    "action": event.action,
                    "metadata": event.metadata_json,
                    "resource_id": event.resource_id,
                }
                for event in events
            ]
        )
        assert ISSUER not in serialized_events
        assert SUBJECT not in serialized_events
        assert EMAIL not in serialized_events
    finally:
        session.close()
        engine.dispose()


def test_membership_revoke_suspends_history_and_can_be_reprovisioned(
    identity_runner: CliRunner,
    shared_settings: Settings,
) -> None:
    _create_workspace(identity_runner)
    provisioned = _provision(identity_runner, role="editor")

    arguments = [
        "membership-revoke",
        "--workspace",
        "quality-team",
        "--issuer",
        ISSUER,
        "--subject",
        SUBJECT,
    ]
    revoked = _invoke_json(identity_runner, arguments)
    repeated = _invoke_json(identity_runner, arguments)
    assert revoked == {
        "membership_id": provisioned["membership_id"],
        "status": "revoked",
        "user_id": provisioned["user_id"],
        "workspace_id": provisioned["workspace_id"],
        "workspace_slug": "quality-team",
    }
    assert repeated == {**revoked, "status": "already_revoked"}

    restored = _provision(identity_runner, role="viewer")
    assert restored["membership_id"] == provisioned["membership_id"]
    assert restored["status"] == "updated"

    engine = create_database_engine(shared_settings)
    session = create_session_factory(engine)()
    try:
        membership = session.get(WorkspaceMembership, str(provisioned["membership_id"]))
        assert membership is not None
        assert membership.status == RecordStatus.ACTIVE
        assert membership.role == WorkspaceRole.VIEWER
        revoke_events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.action == "operator.membership.revoke")
            )
        )
        assert len(revoke_events) == 1
    finally:
        session.close()
        engine.dispose()


def test_identity_commands_fail_closed_for_local_mode_and_unconfigured_issuer(
    identity_runner: CliRunner,
    shared_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_workspace(identity_runner)
    wrong_issuer = identity_runner.invoke(
        cli.app,
        [
            "membership-provision",
            "--workspace",
            "quality-team",
            "--issuer",
            "https://wrong-issuer.example.test",
            "--subject",
            SUBJECT,
            "--role",
            "viewer",
        ],
    )
    assert wrong_issuer.exit_code != 0
    assert "does not match" in wrong_issuer.output
    assert SUBJECT not in wrong_issuer.output

    local_settings = shared_settings.model_copy(
        update={
            "auth_mode": "local",
            "oidc_issuer": None,
            "oidc_audience": None,
            "oidc_jwks_url": None,
            "public_base_url": None,
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: local_settings)
    local_result = identity_runner.invoke(
        cli.app,
        ["workspace-create", "--slug", "another-team", "--name", "Another team"],
    )
    assert local_result.exit_code != 0
    assert "shared OIDC" in local_result.output
    assert "mode" in local_result.output


def test_shared_seed_requires_workspace_before_migration_or_mutation(
    identity_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_called = False

    def forbidden_migration(_settings: Settings) -> None:
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(cli, "apply_migrations", forbidden_migration)
    result = identity_runner.invoke(cli.app, ["seed"])

    assert result.exit_code != 0
    assert "--workspace is required" in _plain_cli_output(result.output)
    assert migration_called is False


def test_shared_seed_is_idempotent_and_scoped_to_selected_workspace(
    identity_runner: CliRunner,
    shared_settings: Settings,
) -> None:
    quality = _create_workspace(identity_runner)
    _provision(identity_runner, role="editor")
    other = _create_workspace(identity_runner, slug="other-team", name="Other team")
    _provision(
        identity_runner,
        role="editor",
        workspace_slug="other-team",
        subject="other-operator-subject",
        email="other-member@example.test",
    )

    first = _seed_workspace(identity_runner)
    second = _seed_workspace(identity_runner)
    assert first["status"] == "ready"
    assert first["workspace_id"] == quality["workspace_id"]
    assert first["workspace_slug"] == "quality-team"
    assert first["counts"] == {"datasets": 2, "models": 3, "prompts": 2}
    assert second["counts"] == first["counts"]

    engine = create_database_engine(shared_settings)
    session = create_session_factory(engine)()
    try:
        quality_datasets = session.scalar(
            select(func.count())
            .select_from(Dataset)
            .where(Dataset.workspace_id == str(quality["workspace_id"]))
        )
        other_datasets = session.scalar(
            select(func.count())
            .select_from(Dataset)
            .where(Dataset.workspace_id == str(other["workspace_id"]))
        )
        assert quality_datasets == 2
        assert other_datasets == 0
        seed_events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == "operator.demo.seed"))
        )
        assert len(seed_events) == 2
        assert {event.workspace_id for event in seed_events} == {str(quality["workspace_id"])}
    finally:
        session.close()
        engine.dispose()


def test_shared_raw_export_is_workspace_scoped_and_cross_workspace_safe(
    identity_runner: CliRunner,
    shared_settings: Settings,
    tmp_path: Path,
) -> None:
    quality = _create_workspace(identity_runner)
    _provision(identity_runner, role="editor")
    _seed_workspace(identity_runner)
    run_id = _create_demo_run(shared_settings, "quality-team")

    destination = tmp_path / "raw" / "run.json"
    exported = identity_runner.invoke(
        cli.app,
        [
            "export-run",
            run_id,
            "--workspace",
            "quality-team",
            "--output",
            str(destination),
        ],
    )
    assert exported.exit_code == 0, exported.output
    assert destination.exists()
    assert (
        json.loads(destination.read_text(encoding="utf-8"))["workspace_id"]
        == quality["workspace_id"]
    )

    _create_workspace(identity_runner, slug="other-team", name="Other team")
    _provision(
        identity_runner,
        role="viewer",
        workspace_slug="other-team",
        subject="other-operator-subject",
        email="other-member@example.test",
    )
    denied_destination = tmp_path / "denied.json"
    denied = identity_runner.invoke(
        cli.app,
        [
            "export-run",
            run_id,
            "--workspace",
            "other-team",
            "--output",
            str(denied_destination),
        ],
    )
    assert denied.exit_code != 0
    assert "not found in the selected workspace" in denied.output
    assert denied_destination.exists() is False

    engine = create_database_engine(shared_settings)
    session = create_session_factory(engine)()
    try:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == "operator.run.export"))
        )
        assert len(events) == 1
        assert events[0].workspace_id == quality["workspace_id"]
        assert events[0].resource_id == run_id
    finally:
        session.close()
        engine.dispose()


def test_versioned_export_package_requires_disclosure_and_returns_auditable_receipt(
    identity_runner: CliRunner,
    shared_settings: Settings,
    tmp_path: Path,
) -> None:
    quality = _create_workspace(identity_runner)
    _provision(identity_runner, role="editor")
    _seed_workspace(identity_runner)
    run_id = _create_demo_run(shared_settings, "quality-team")
    export_root = tmp_path / "packages"

    missing_profile = identity_runner.invoke(
        cli.app,
        [
            "export-package",
            run_id,
            "--workspace",
            "quality-team",
            "--output-dir",
            str(export_root),
        ],
    )
    assert missing_profile.exit_code != 0
    assert "--disclosure-profile" in _plain_cli_output(missing_profile.output)

    arguments = [
        "export-package",
        run_id,
        "--workspace",
        "quality-team",
        "--output-dir",
        str(export_root),
        "--disclosure-profile",
        "content_redacted",
    ]
    first = _invoke_json(identity_runner, arguments)
    second = _invoke_json(identity_runner, arguments)
    assert first["status"] == "created"
    assert second == {**first, "status": "already_exists"}
    assert first["workspace_id"] == quality["workspace_id"]
    assert first["schema_version"] == "evalforge.run-export.v1"
    assert first["disclosure_profile"] == "content_redacted"
    assert first["idempotency_key"] == f"local_file:{first['package_sha256']}"
    assert ISSUER not in json.dumps(first)
    assert SUBJECT not in json.dumps(first)
    assert EMAIL not in json.dumps(first)

    package_path = Path(str(first["location"]))
    envelope = json.loads(package_path.read_text(encoding="utf-8"))
    assert envelope["payload_sha256"] == first["package_sha256"]
    assert envelope["payload"]["schema_version"] == "evalforge.run-export.v1"
    assert envelope["payload"]["disclosure_profile"] == "content_redacted"
    assert envelope["payload"]["metric_versions"] == {"correctness": "lexical-correctness-v1"}
    run_payload = envelope["payload"]["run"]
    assert run_payload["workspace_id"] == quality["workspace_id"]
    assert run_payload["dataset_snapshot"]["cases"][0]["input"] == "[redacted]"

    _create_workspace(identity_runner, slug="other-team", name="Other team")
    _provision(
        identity_runner,
        role="viewer",
        workspace_slug="other-team",
        subject="other-operator-subject",
        email="other-member@example.test",
    )
    denied = identity_runner.invoke(
        cli.app,
        [
            "export-package",
            run_id,
            "--workspace",
            "other-team",
            "--output-dir",
            str(tmp_path / "denied-packages"),
            "--disclosure-profile",
            "content_redacted",
        ],
    )
    assert denied.exit_code != 0
    assert "not found in the selected workspace" in denied.output
    assert (tmp_path / "denied-packages").exists() is False

    engine = create_database_engine(shared_settings)
    session = create_session_factory(engine)()
    try:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "operator.run.export_package")
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        assert len(events) == 2
        assert [event.metadata_json["created"] for event in events] == [True, False]
        assert all(event.workspace_id == quality["workspace_id"] for event in events)
        assert all(
            event.metadata_json["package_sha256"] == first["package_sha256"] for event in events
        )
        assert ISSUER not in json.dumps([event.metadata_json for event in events])
        assert SUBJECT not in json.dumps([event.metadata_json for event in events])
    finally:
        session.close()
        engine.dispose()


def test_versioned_export_package_supports_the_local_workspace(
    identity_runner: CliRunner,
    shared_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_settings = Settings(
        _env_file=None,
        environment="test",
        database_url=shared_settings.database_url,
        auth_mode="local",
        auto_migrate=False,
        seed_demo=False,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: local_settings)
    seeded = _invoke_json(identity_runner, ["seed", "--workspace", "local"])
    assert seeded["counts"] == {"datasets": 2, "models": 3, "prompts": 2}
    run_id = _create_demo_run(local_settings, None)

    receipt = _invoke_json(
        identity_runner,
        [
            "export-package",
            run_id,
            "--workspace",
            "local",
            "--output-dir",
            str(tmp_path / "local-packages"),
            "--disclosure-profile",
            "full_evidence",
        ],
    )
    assert receipt["status"] == "created"
    assert receipt["workspace_id"] == local_workspace_context().workspace_id
    assert Path(str(receipt["location"])).exists()
