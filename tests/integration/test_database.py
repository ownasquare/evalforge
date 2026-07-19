from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, MetaData, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from evalforge.config import Settings, get_settings
from evalforge.container import apply_migrations
from evalforge.database import (
    EXPECTED_SCHEMA_REVISION,
    Base,
    SessionFactory,
    check_database_connectivity,
    check_database_readiness,
    create_session_factory,
    session_scope,
)
from evalforge.models import (
    CalibrationReport,
    EvaluationResult,
    EvaluationRun,
    ImmutableProvenanceError,
    InvalidStateTransition,
    ResultStatus,
    RunStatus,
    canonical_json_hash,
    validate_calibration_report_integrity,
)
from evalforge.models import TestCase as DomainTestCase
from evalforge.repositories import (
    CalibrationReportRepository,
    ConflictError,
    DatasetRepository,
    EvaluationRunRepository,
    ModelProfileRepository,
    PromptTemplateRepository,
    Repositories,
)
from evalforge.repositories import (
    ValidationError as RepositoryValidationError,
)
from evalforge.schemas import (
    DatasetCreate,
    EvaluationRunCreate,
    ModelProfileCreate,
    ModelProfileUpdate,
    PromptTemplateCreate,
    PromptTemplateUpdate,
)
from evalforge.schemas import TestCaseCreate as CaseCreateSchema
from evalforge.security.permissions import WorkspaceContext, local_workspace_context
from evalforge.seed import seed_demo

ROOT = Path(__file__).parents[2]


def test_schema_revision_fits_alembic_default_version_column() -> None:
    assert len(EXPECTED_SCHEMA_REVISION) <= 32


def test_calibration_integrity_accepts_stable_six_decimal_rates() -> None:
    payload = {
        "calibration_set_sha256": "a" * 64,
        "confusion_matrix": {
            "false_negative": 0,
            "false_positive": 1,
            "true_negative": 0,
            "true_positive": 2,
        },
        "dataset": {"id": "dataset", "version": "1", "sha256": "b" * 64},
        "evidence_kind": "offline_statistical_evidence",
        "f1": 0.8,
        "human_fail_count": 1,
        "human_pass_count": 2,
        "label_manifest_sha256": "c" * 64,
        "metric": {
            "name": "correctness",
            "version": "1",
            "direction": "higher_is_better",
        },
        "precision": 0.666667,
        "production_validated": False,
        "recall": 1.0,
        "reviewer_count": 1,
        "sample_size": 3,
        "schema_version": "evalforge.calibration-report.v1",
        "selected_threshold": 0.5,
    }
    report = CalibrationReport(
        workspace_id="00000000-0000-4000-8000-000000000001",
        run_id="00000000-0000-4000-8000-000000000010",
        run_candidate_id="00000000-0000-4000-8000-000000000011",
        metric_name="correctness",
        metric_version="1",
        metric_direction="higher_is_better",
        selected_threshold=0.5,
        manifest_sha256="c" * 64,
        report_sha256=canonical_json_hash(payload),
        schema_version="evalforge.calibration-report.v1",
        evidence_kind="offline_statistical_evidence",
        production_validated=False,
        sample_size=3,
        human_pass_count=2,
        human_fail_count=1,
        reviewer_count=1,
        precision=0.666667,
        recall=1.0,
        f1=0.8,
        report_payload=payload,
        created_by_user_id="00000000-0000-4000-8000-000000000002",
    )

    validate_calibration_report_integrity(report)


@pytest.mark.integration
def test_file_sqlite_enables_safety_pragmas(engine: Engine) -> None:
    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert busy_timeout == 3_500
    assert check_database_connectivity(engine) is True
    assert check_database_readiness(engine) is False


@pytest.mark.integration
def test_metadata_contains_exact_domain_tables(engine: Engine) -> None:
    table_names = set(inspect(engine).get_table_names())
    assert table_names == set(Base.metadata.tables)


@pytest.mark.integration
def test_session_factory_returns_independent_sessions(session_factory: SessionFactory) -> None:
    first = session_factory()
    second = session_factory()
    try:
        assert first is not second
        assert first.get_bind() is second.get_bind()
    finally:
        first.close()
        second.close()


@pytest.mark.integration
def test_foreign_keys_are_enforced(session: Session) -> None:
    session.add(
        DomainTestCase(
            dataset_id="00000000-0000-0000-0000-000000000000",
            external_id="orphan",
            position=0,
            input_text="orphan",
            required_phrases=[],
            constraints_json={},
            tags=[],
            metadata_json={},
            case_hash="a" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_run_result_keeps_immutable_case_snapshot(
    session: Session, sample_result: EvaluationResult
) -> None:
    session.add(sample_result)
    session.commit()

    assert sample_result.input_snapshot
    assert sample_result.case_hash
    assert sample_result.metric_versions == {"correctness": "1.0.0"}
    assert sample_result.metric_directions == {"correctness": "higher_is_better"}
    assert sample_result.metric_applicability == {"correctness": "applicable"}
    assert sample_result.estimated_cost_micro_usd == 0
    assert sample_result.cost_source == "deterministic"

    sample_result.input_snapshot = {"input": "tampered"}
    with pytest.raises(ImmutableProvenanceError, match="input_snapshot"):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_result_metrics_finalize_once_then_become_immutable(
    session: Session, sample_result: EvaluationResult
) -> None:
    sample_result.status = ResultStatus.RUNNING
    sample_result.metric_versions = {}
    sample_result.metric_directions = {}
    sample_result.metric_applicability = {}
    sample_result.metric_results = {}
    sample_result.estimated_cost_micro_usd = None
    sample_result.cost_source = "unavailable"
    session.add(sample_result)
    session.commit()

    sample_result.metric_versions = {"correctness": "1.0.0"}
    sample_result.metric_directions = {"correctness": "higher_is_better"}
    sample_result.metric_applicability = {"correctness": "applicable"}
    sample_result.metric_results = {"correctness": {"score": 1.0}}
    sample_result.estimated_cost_micro_usd = 0
    sample_result.cost_source = "synthetic"
    sample_result.transition_to(ResultStatus.COMPLETED)
    session.commit()

    sample_result.metric_results = {"correctness": {"score": 0.0}}
    with pytest.raises(ImmutableProvenanceError, match="metric_results"):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_calibration_reports_are_scoped_append_only_idempotent_and_paginated(
    session: Session,
    sample_result: EvaluationResult,
    workspace_context: WorkspaceContext,
) -> None:
    session.add(sample_result)
    session.commit()
    repository = CalibrationReportRepository(session, workspace_context)

    def report(*, threshold: float, digest: str) -> CalibrationReport:
        payload = {
            "calibration_set_sha256": digest * 64,
            "confusion_matrix": {
                "false_negative": 0,
                "false_positive": 0,
                "true_negative": 0,
                "true_positive": 1,
            },
            "dataset": {"id": "dataset", "version": "1", "sha256": "f" * 64},
            "evidence_kind": "offline_statistical_evidence",
            "f1": 1.0,
            "human_fail_count": 0,
            "human_pass_count": 1,
            "label_manifest_sha256": "a" * 64,
            "metric": {
                "name": "correctness",
                "version": "1.0.0",
                "direction": "higher_is_better",
            },
            "precision": 1.0,
            "production_validated": False,
            "recall": 1.0,
            "reviewer_count": 1,
            "sample_size": 1,
            "schema_version": "evalforge.calibration-report.v1",
            "selected_threshold": threshold,
        }
        return CalibrationReport(
            workspace_id=workspace_context.workspace_id,
            run_id=sample_result.run_id,
            run_candidate_id=sample_result.run_candidate_id,
            metric_name="correctness",
            metric_version="1.0.0",
            metric_direction="higher_is_better",
            selected_threshold=threshold,
            manifest_sha256="a" * 64,
            report_sha256=canonical_json_hash(payload),
            schema_version="evalforge.calibration-report.v1",
            evidence_kind="offline_statistical_evidence",
            production_validated=False,
            sample_size=1,
            human_pass_count=1,
            human_fail_count=0,
            reviewer_count=1,
            precision=1.0,
            recall=1.0,
            f1=1.0,
            report_payload=payload,
            created_by_user_id=workspace_context.user_id,
        )

    first, first_created = repository.create_idempotent(report(threshold=0.5, digest="b"))
    replay, replay_created = repository.create_idempotent(report(threshold=0.5, digest="b"))
    _second, second_created = repository.create_idempotent(report(threshold=0.6, digest="c"))
    session.commit()

    assert first_created is True
    assert replay_created is False
    assert second_created is True
    assert replay.id == first.id
    rows, total = repository.list(sample_result.run_id, page=1, limit=1)
    assert total == 2
    assert len(rows) == 1
    assert repository.list(sample_result.run_id, page=2, limit=1)[1] == 2
    assert repository.get(sample_result.run_id, first.id).id == first.id

    invalid = report(threshold=0.7, digest="d")
    invalid.report_sha256 = "e" * 64
    with pytest.raises(RepositoryValidationError, match="integrity"):
        repository.create_idempotent(invalid)

    foreign_run = EvaluationRun(
        workspace_id=workspace_context.workspace_id,
        dataset_id=sample_result.run.dataset_id,
        dataset_snapshot=dict(sample_result.run.dataset_snapshot),
        dataset_hash=sample_result.run.dataset_hash,
        metric_configuration_snapshot=dict(sample_result.run.metric_configuration_snapshot),
        application_version="test",
        executor_type="in_process",
        requested_by_user_id=workspace_context.user_id,
        requested_by=workspace_context.display_name,
        status=RunStatus.COMPLETED,
        total_items=0,
    )
    session.add(foreign_run)
    session.commit()
    cross_run = report(threshold=0.8, digest="e")
    cross_run.run_id = foreign_run.id
    session.add(cross_run)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    first.precision = 0.5
    with pytest.raises(ImmutableProvenanceError, match="append-only"):
        session.commit()
    session.rollback()
    persisted = repository.get(sample_result.run_id, first.id)
    session.delete(persisted)
    with pytest.raises(ImmutableProvenanceError, match="append-only"):
        session.commit()
    session.rollback()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("evidence_kind", "production_validation"),
        ("production_validated", True),
        ("metric_direction", "sideways"),
        ("reviewer_count", 2),
    ],
)
def test_calibration_database_rejects_misleading_evidence_claims(
    session: Session,
    sample_result: EvaluationResult,
    workspace_context: WorkspaceContext,
    field: str,
    invalid_value: object,
) -> None:
    session.add(sample_result)
    session.commit()
    values = {
        "workspace_id": workspace_context.workspace_id,
        "run_id": sample_result.run_id,
        "run_candidate_id": sample_result.run_candidate_id,
        "metric_name": "correctness",
        "metric_version": "1.0.0",
        "metric_direction": "higher_is_better",
        "selected_threshold": 0.5,
        "manifest_sha256": "a" * 64,
        "report_sha256": "b" * 64,
        "schema_version": "evalforge.calibration-report.v1",
        "evidence_kind": "offline_statistical_evidence",
        "production_validated": False,
        "sample_size": 1,
        "human_pass_count": 1,
        "human_fail_count": 0,
        "reviewer_count": 1,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "report_payload": {},
        "created_by_user_id": workspace_context.user_id,
    }
    values[field] = invalid_value
    session.add(CalibrationReport(**values))

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_terminal_run_cannot_restart(session: Session, sample_result: EvaluationResult) -> None:
    run = sample_result.run
    run.transition_to(RunStatus.RUNNING)
    run.transition_to(RunStatus.COMPLETED)
    assert run.state_version == 2
    assert run.started_at is not None
    assert run.finished_at is not None
    with pytest.raises(InvalidStateTransition, match="cannot transition run"):
        run.transition_to(RunStatus.RUNNING)


@pytest.mark.integration
def test_state_version_prevents_stale_run_updates(
    session: Session,
    session_factory: SessionFactory,
    sample_result: EvaluationResult,
    workspace_context: WorkspaceContext,
) -> None:
    session.add(sample_result)
    session.commit()
    first_session = session_factory()
    stale_session = session_factory()
    try:
        first = EvaluationRunRepository(first_session, workspace_context).get(sample_result.run_id)
        stale = EvaluationRunRepository(stale_session, workspace_context).get(sample_result.run_id)
        first.transition_to(RunStatus.RUNNING)
        first_session.commit()
        stale.transition_to(RunStatus.CANCELLED)
        with pytest.raises(StaleDataError):
            stale_session.commit()
    finally:
        first_session.close()
        stale_session.close()


@pytest.mark.integration
def test_recovery_preserves_queued_work_and_interrupts_only_active_work(
    session: Session,
    sample_result: EvaluationResult,
    workspace_context: WorkspaceContext,
) -> None:
    sample_result.status = ResultStatus.QUEUED
    session.add(sample_result)
    session.commit()
    repository = EvaluationRunRepository(session, workspace_context)

    assert repository.recover_abandoned() == 0
    assert sample_result.run.status is RunStatus.QUEUED
    assert sample_result.candidate.status is RunStatus.QUEUED
    assert sample_result.status is ResultStatus.QUEUED

    sample_result.run.transition_to(RunStatus.RUNNING)
    sample_result.candidate.transition_to(RunStatus.RUNNING)
    sample_result.transition_to(ResultStatus.RUNNING)
    session.flush()

    assert repository.recover_abandoned() == 1
    assert sample_result.run.status is RunStatus.INTERRUPTED
    assert sample_result.candidate.status is RunStatus.INTERRUPTED
    assert sample_result.status is ResultStatus.INTERRUPTED


@pytest.mark.integration
def test_recovery_interrupts_queued_results_inside_an_active_run(
    session: Session,
    sample_result: EvaluationResult,
    workspace_context: WorkspaceContext,
) -> None:
    sample_result.status = ResultStatus.QUEUED
    session.add(sample_result)
    session.commit()
    sample_result.run.transition_to(RunStatus.RUNNING)
    sample_result.candidate.transition_to(RunStatus.RUNNING)
    session.flush()

    changed = EvaluationRunRepository(session, workspace_context).recover_abandoned()

    assert changed == 1
    assert sample_result.run.status is RunStatus.INTERRUPTED
    assert sample_result.candidate.status is RunStatus.INTERRUPTED
    assert sample_result.status is ResultStatus.INTERRUPTED


@pytest.mark.integration
def test_recovery_preserves_persisted_provider_evidence_and_known_cost(
    session: Session,
    sample_result: EvaluationResult,
    workspace_context: WorkspaceContext,
) -> None:
    sample_result.status = ResultStatus.RUNNING
    sample_result.cost_source = "reported_usage"
    sample_result.estimated_cost_micro_usd = 17
    sample_result.provider = "openai"
    sample_result.request_id = "req_recorded_before_crash"
    sample_result.run.status = RunStatus.RUNNING
    sample_result.candidate.status = RunStatus.RUNNING
    session.add(sample_result)
    session.commit()

    changed = EvaluationRunRepository(session, workspace_context).recover_abandoned()

    assert changed == 1
    assert sample_result.status is ResultStatus.INTERRUPTED
    assert sample_result.request_id == "req_recorded_before_crash"
    assert sample_result.output_text == "Paris"
    assert sample_result.estimated_cost_micro_usd == 17
    assert sample_result.cost_source == "reported_usage"
    assert sample_result.error_retryable is False


@pytest.mark.integration
def test_run_creation_is_idempotent_and_requires_real_provider_approvals(
    session: Session,
    workspace_context: WorkspaceContext,
) -> None:
    dataset = DatasetRepository(session, workspace_context).create(
        DatasetCreate(
            name="Idempotency benchmark",
            cases=[
                CaseCreateSchema(
                    external_id="case-1",
                    position=0,
                    input_text="Say hello",
                    expected_output="hello",
                )
            ],
        )
    )
    prompt = PromptTemplateRepository(session, workspace_context).create(
        PromptTemplateCreate(
            name="Simple prompt",
            user_template="{input}",
        )
    )
    offline_model = ModelProfileRepository(session, workspace_context).create(
        ModelProfileCreate(
            name="Offline model",
            provider="deterministic",
            model_name="balanced",
            api_mode="deterministic",
            input_price_micro_usd_per_million_tokens=0,
            output_price_micro_usd_per_million_tokens=0,
            pricing_source="deterministic",
        )
    )
    real_model = ModelProfileRepository(session, workspace_context).create(
        ModelProfileCreate(
            name="Provider model",
            provider="openai",
            model_name="allowed-model",
            api_mode="responses",
        )
    )
    repository = EvaluationRunRepository(session, workspace_context)
    request = EvaluationRunCreate(
        dataset_id=UUID(dataset.id),
        prompt_ids=[UUID(prompt.id)],
        model_ids=[UUID(offline_model.id)],
        idempotency_key="request-1",
    )

    first = repository.create(request, application_version="test")
    with pytest.raises(ConflictError, match="referenced by an evaluation run"):
        DatasetRepository(session, workspace_context).delete_case(dataset.cases[0].id)
    with pytest.raises(ConflictError, match="create a new version"):
        DatasetRepository(session, workspace_context).add_case(
            dataset.id,
            CaseCreateSchema(external_id="case-2", position=1, input_text="New case"),
        )
    with pytest.raises(ConflictError, match="create a new version"):
        PromptTemplateRepository(session, workspace_context).update(
            prompt.id, PromptTemplateUpdate(user_template="Changed {input}")
        )
    with pytest.raises(ConflictError, match="create a new version"):
        ModelProfileRepository(session, workspace_context).update(
            offline_model.id,
            ModelProfileUpdate(generation_parameters={"temperature": 0.5}),
        )
    updated_model = ModelProfileRepository(session, workspace_context).update(
        offline_model.id, ModelProfileUpdate(enabled=False)
    )
    assert updated_model.enabled is False
    replay = repository.create(request, application_version="test")
    assert replay.id == first.id
    assert replay.request_hash == first.request_hash

    changed_request = request.model_copy(update={"name": "Different request"})
    with pytest.raises(ConflictError, match="different request"):
        repository.create(changed_request, application_version="test")

    real_request = EvaluationRunCreate(
        dataset_id=UUID(dataset.id),
        prompt_ids=[UUID(prompt.id)],
        model_ids=[UUID(real_model.id)],
    )
    with pytest.raises(RepositoryValidationError, match="acknowledge_real_cost"):
        repository.create(real_request, application_version="test")
    cost_acknowledged = real_request.model_copy(update={"acknowledge_real_cost": True})
    with pytest.raises(RepositoryValidationError, match="acknowledge_external_data_transfer"):
        repository.create(cost_acknowledged, application_version="test")
    transfer_acknowledged = cost_acknowledged.model_copy(
        update={"acknowledge_external_data_transfer": True}
    )
    with pytest.raises(RepositoryValidationError, match="spend_limit_micro_usd"):
        repository.create(transfer_acknowledged, application_version="test")
    spend_limited = transfer_acknowledged.model_copy(update={"spend_limit_micro_usd": 1_000_000})
    with pytest.raises(RepositoryValidationError, match="acknowledge_unknown_cost"):
        repository.create(spend_limited, application_version="test")


@pytest.mark.integration
def test_initial_migration_upgrades_a_new_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_database = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{migration_database}"
    monkeypatch.setenv("EVALFORGE_DATABASE_URL", database_url)
    monkeypatch.setenv("EVALFORGE_ENVIRONMENT", "test")
    get_settings.cache_clear()
    try:
        alembic_config = Config(str(ROOT / "alembic.ini"))
        command.upgrade(alembic_config, "head")
        from evalforge.database import create_database_engine

        migration_engine = create_database_engine(database_url)
        try:
            table_names = set(inspect(migration_engine).get_table_names())
            migration_ready = check_database_readiness(migration_engine)
            with migration_engine.connect() as connection:
                migration_context = MigrationContext.configure(
                    connection, opts={"compare_type": True}
                )
                schema_differences = compare_metadata(migration_context, Base.metadata)
        finally:
            migration_engine.dispose()
    finally:
        get_settings.cache_clear()

    assert table_names == {*Base.metadata.tables, "alembic_version"}
    assert schema_differences == []
    assert migration_ready is True


@pytest.mark.integration
def test_application_migration_uses_the_explicit_runtime_database(
    tmp_path: Path, settings: Settings
) -> None:
    from evalforge.database import create_database_engine

    migration_database = tmp_path / "explicit-runtime.db"
    runtime_settings = settings.model_copy(
        update={"database_url": f"sqlite+pysqlite:///{migration_database}"}
    )

    apply_migrations(runtime_settings)

    migration_engine = create_database_engine(runtime_settings)
    try:
        assert set(inspect(migration_engine).get_table_names()) == {
            *Base.metadata.tables,
            "alembic_version",
        }
        assert check_database_readiness(migration_engine) is True
    finally:
        migration_engine.dispose()


@pytest.mark.integration
def test_second_migration_upgrades_an_existing_first_revision(
    tmp_path: Path, settings: Settings
) -> None:
    from evalforge.database import create_database_engine

    database_url = f"sqlite+pysqlite:///{tmp_path / 'upgrade-from-0001.db'}"
    configuration = Config()
    configuration.set_main_option("script_location", str(ROOT / "src" / "evalforge" / "migrations"))
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "0001_initial_schema")
    engine = create_database_engine(database_url)
    try:
        assert check_database_readiness(engine) is False
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    engine = create_database_engine(database_url)
    try:
        run_columns = {column["name"] for column in inspect(engine).get_columns("evaluation_runs")}
        case_columns = {column["name"] for column in inspect(engine).get_columns("test_cases")}
        assert {"preflight_snapshot", "acknowledge_unknown_cost"} <= run_columns
        assert "context_chunks" in case_columns
        assert check_database_readiness(engine) is True
    finally:
        engine.dispose()


@pytest.mark.integration
def test_calibration_migration_upgrades_from_0004_and_refuses_evidence_loss(
    tmp_path: Path,
) -> None:
    from evalforge.database import create_database_engine

    database_url = f"sqlite+pysqlite:///{tmp_path / 'upgrade-from-0004.db'}"
    configuration = Config()
    configuration.set_main_option("script_location", str(ROOT / "src" / "evalforge" / "migrations"))
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "0004_durable_execution_leases")
    engine = create_database_engine(database_url)
    try:
        assert "calibration_reports" not in inspect(engine).get_table_names()
        assert check_database_readiness(engine) is False
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    engine = create_database_engine(database_url)
    try:
        context = local_workspace_context()
        with session_scope(create_session_factory(engine)) as migration_session:
            seed_demo(migration_session, context)
            repositories = Repositories(migration_session, context)
            datasets, _ = repositories.datasets.list(limit=1)
            prompts, _ = repositories.prompts.list(limit=1)
            models, _ = repositories.models.list(limit=1)
            run = repositories.runs.create(
                EvaluationRunCreate(
                    dataset_id=UUID(datasets[0].id),
                    prompt_ids=[UUID(prompts[0].id)],
                    model_ids=[UUID(models[0].id)],
                ),
                application_version="migration-test",
            )
            migration_session.add(
                CalibrationReport(
                    workspace_id=context.workspace_id,
                    run_id=run.id,
                    run_candidate_id=run.candidates[0].id,
                    metric_name="correctness",
                    metric_version="1.0.0",
                    metric_direction="higher_is_better",
                    selected_threshold=0.5,
                    manifest_sha256="a" * 64,
                    report_sha256="b" * 64,
                    schema_version="evalforge.calibration-report.v1",
                    evidence_kind="offline_statistical_evidence",
                    production_validated=False,
                    sample_size=1,
                    human_pass_count=1,
                    human_fail_count=0,
                    reviewer_count=1,
                    precision=1.0,
                    recall=1.0,
                    f1=1.0,
                    report_payload={},
                    created_by_user_id=context.user_id,
                )
            )
        assert check_database_readiness(engine) is True
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(configuration, "0004_durable_execution_leases")
    engine = create_database_engine(database_url)
    try:
        assert "calibration_reports" in inspect(engine).get_table_names()
        assert check_database_readiness(engine) is True
    finally:
        engine.dispose()


@pytest.mark.integration
def test_second_migration_preserves_populated_foreign_key_parents(
    tmp_path: Path,
) -> None:
    from evalforge.database import create_database_engine

    database_url = f"sqlite+pysqlite:///{tmp_path / 'populated-0001.db'}"
    configuration = Config()
    configuration.set_main_option("script_location", str(ROOT / "src" / "evalforge" / "migrations"))
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "0001_initial_schema")
    engine = create_database_engine(database_url)
    now = datetime.now(UTC)
    dataset_id = "10000000-0000-0000-0000-000000000001"
    case_id = "10000000-0000-0000-0000-000000000002"
    run_id = "10000000-0000-0000-0000-000000000003"
    metadata = MetaData()
    metadata.reflect(bind=engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                metadata.tables["datasets"].insert(),
                {
                    "id": dataset_id,
                    "name": "Existing dataset",
                    "version": 1,
                    "content_hash": "d" * 64,
                    "metadata": {},
                    "created_at": now,
                    "updated_at": now,
                },
            )
            connection.execute(
                metadata.tables["test_cases"].insert(),
                {
                    "id": case_id,
                    "dataset_id": dataset_id,
                    "external_id": "existing-case",
                    "position": 0,
                    "input_text": "Existing input",
                    "required_phrases": [],
                    "constraints": {},
                    "tags": [],
                    "metadata": {},
                    "case_hash": "c" * 64,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            connection.execute(
                metadata.tables["evaluation_runs"].insert(),
                {
                    "id": run_id,
                    "dataset_id": dataset_id,
                    "dataset_snapshot": {},
                    "dataset_hash": "d" * 64,
                    "metric_configuration_snapshot": {},
                    "application_version": "0.1.0",
                    "executor_type": "local",
                    "acknowledge_real_cost": False,
                    "status": "completed",
                    "state_version": 0,
                    "total_items": 0,
                    "completed_items": 0,
                    "succeeded_items": 0,
                    "failed_items": 0,
                    "queued_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            connection.exec_driver_sql(
                "CREATE TABLE migration_guard ("
                "test_case_id TEXT NOT NULL REFERENCES test_cases(id), "
                "run_id TEXT NOT NULL REFERENCES evaluation_runs(id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO migration_guard (test_case_id, run_id) VALUES (?, ?)",
                (case_id, run_id),
            )
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT COUNT(*) FROM migration_guard")).scalar_one() == 1
            )
            assert (
                connection.execute(
                    text("SELECT context_chunks FROM test_cases WHERE id = :id"),
                    {"id": case_id},
                ).scalar_one()
                == "[]"
            )
            run_defaults = connection.execute(
                text(
                    "SELECT preflight_snapshot, acknowledge_unknown_cost "
                    "FROM evaluation_runs WHERE id = :id"
                ),
                {"id": run_id},
            ).one()
            assert run_defaults == ("{}", 0)
        assert check_database_readiness(engine) is True
    finally:
        engine.dispose()


@pytest.mark.integration
def test_second_migration_recovers_a_stale_sqlite_batch_table(
    tmp_path: Path,
) -> None:
    from evalforge.database import create_database_engine

    database_url = f"sqlite+pysqlite:///{tmp_path / 'stale-batch-table.db'}"
    configuration = Config()
    configuration.set_main_option("script_location", str(ROOT / "src" / "evalforge" / "migrations"))
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "0001_initial_schema")
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE _alembic_tmp_test_cases AS SELECT * FROM test_cases WHERE 0"
            )
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    engine = create_database_engine(database_url)
    try:
        assert not {
            name for name in inspect(engine).get_table_names() if name.startswith("_alembic_tmp_")
        }
        assert check_database_readiness(engine) is True
    finally:
        engine.dispose()


@pytest.mark.integration
def test_second_migration_resumes_partially_applied_sqlite_columns(
    tmp_path: Path,
) -> None:
    from evalforge.database import create_database_engine

    database_url = f"sqlite+pysqlite:///{tmp_path / 'partial-migration.db'}"
    configuration = Config()
    configuration.set_main_option("script_location", str(ROOT / "src" / "evalforge" / "migrations"))
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "0001_initial_schema")
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE test_cases ADD COLUMN context_chunks JSON NOT NULL DEFAULT '[]'"
            )
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_runs ADD COLUMN preflight_snapshot "
                "JSON NOT NULL DEFAULT '{}'"
            )
            connection.exec_driver_sql(
                "CREATE TABLE _alembic_tmp_evaluation_runs AS SELECT * FROM evaluation_runs WHERE 0"
            )
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    engine = create_database_engine(database_url)
    try:
        run_columns = {column["name"] for column in inspect(engine).get_columns("evaluation_runs")}
        case_columns = {column["name"] for column in inspect(engine).get_columns("test_cases")}
        assert {"preflight_snapshot", "acknowledge_unknown_cost"} <= run_columns
        assert "context_chunks" in case_columns
        assert check_database_readiness(engine) is True
    finally:
        engine.dispose()
