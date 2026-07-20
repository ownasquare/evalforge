from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from evalforge.api.app import create_app
from evalforge.commercial import CommercialPilotService, require_run_entitlement
from evalforge.config import Settings
from evalforge.container import apply_migrations, build_container
from evalforge.database import (
    Base,
    check_database_readiness,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from evalforge.evaluation.leases import LeaseLostError, LeaseManager
from evalforge.models import EntitlementStatus, EvaluationRun, ExecutionAttempt, RunStatus
from evalforge.repositories import Repositories
from evalforge.schemas import EvaluationRunCreate
from evalforge.security.permissions import local_workspace_context
from evalforge.seed import seed_demo

POSTGRES_TEST_URL_ENV = "EVALFORGE_POSTGRES_TEST_URL"
SCHEMA_PREFIX = "evalforge_test_"
pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _configured_postgres_url() -> URL:
    raw_url = os.environ.get(POSTGRES_TEST_URL_ENV)
    if not raw_url:
        pytest.skip(f"{POSTGRES_TEST_URL_ENV} is not configured")
    try:
        url = make_url(raw_url)
    except Exception:
        pytest.fail(f"{POSTGRES_TEST_URL_ENV} is not a valid SQLAlchemy URL")
    if url.drivername != "postgresql+psycopg":
        pytest.fail(f"{POSTGRES_TEST_URL_ENV} must use the postgresql+psycopg driver")
    if not url.database:
        pytest.fail(f"{POSTGRES_TEST_URL_ENV} must select an owned test database")
    return url


def _scoped_url(base_url: URL, schema_name: str) -> URL:
    query = dict(base_url.query)
    query["options"] = f"-csearch_path={schema_name},public"
    return base_url.set(query=query)


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    base_url = _configured_postgres_url()
    schema_name = f"{SCHEMA_PREFIX}{uuid4().hex}"
    admin_engine = create_database_engine(base_url)
    schema_created = False
    database_engine: Engine | None = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema_name))
        schema_created = True

        test_url = _scoped_url(base_url, schema_name)
        settings = Settings(
            _env_file=None,
            environment="test",
            database_url=test_url.render_as_string(hide_password=False),
            auth_mode="local",
            api_host="127.0.0.1",
            dashboard_host="127.0.0.1",
            auto_migrate=False,
            seed_demo=False,
            openai_api_key=None,
            compatible_api_key=None,
        )
        apply_migrations(settings)
        database_engine = create_database_engine(settings)
        yield database_engine
    finally:
        if database_engine is not None:
            database_engine.dispose()
        try:
            if schema_created:
                with admin_engine.begin() as connection:
                    connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        finally:
            admin_engine.dispose()


def test_postgresql_migration_seed_and_run_lifecycle(postgres_engine: Engine) -> None:
    assert check_database_readiness(postgres_engine) is True
    with postgres_engine.connect() as connection:
        migration_context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(migration_context, Base.metadata) == []

    factory = create_session_factory(postgres_engine)
    context = local_workspace_context()
    create_request: EvaluationRunCreate
    run_id: str

    with session_scope(factory) as session:
        first_counts = seed_demo(session, context)
        second_counts = seed_demo(session, context)
        assert (
            first_counts
            == second_counts
            == {
                "datasets": 2,
                "prompts": 2,
                "models": 3,
            }
        )

        repositories = Repositories(session, context)
        datasets, dataset_total = repositories.datasets.list()
        prompts, prompt_total = repositories.prompts.list()
        models, model_total = repositories.models.list()
        assert (dataset_total, prompt_total, model_total) == (2, 2, 3)

        create_request = EvaluationRunCreate(
            dataset_id=UUID(datasets[0].id),
            prompt_ids=[UUID(prompts[0].id)],
            model_ids=[UUID(models[0].id)],
            name="PostgreSQL lifecycle proof",
            idempotency_key="postgresql-lifecycle-proof",
        )
        run = repositories.runs.create(
            create_request,
            application_version="postgresql-test",
        )
        run_id = run.id
        assert run.status is RunStatus.QUEUED
        assert run.total_items > 0

    with session_scope(factory) as session:
        repositories = Repositories(session, context)
        run = repositories.runs.get(run_id, with_detail=True)
        assert len(run.candidates) == 1
        candidate_id = run.candidates[0].id
        repositories.runs.transition_run(run_id, RunStatus.RUNNING)
        repositories.runs.transition_candidate(candidate_id, RunStatus.RUNNING)

    with session_scope(factory) as session:
        repositories = Repositories(session, context)
        assert repositories.runs.recover_abandoned(reason="postgresql lifecycle proof") == 1

    with session_scope(factory) as session:
        repositories = Repositories(session, context)
        replayed = repositories.runs.create(
            create_request,
            application_version="postgresql-test",
        )
        persisted = repositories.runs.get(run_id, with_detail=True)
        assert replayed.id == run_id
        assert persisted.status is RunStatus.INTERRUPTED
        assert persisted.candidates[0].status is RunStatus.INTERRUPTED


def test_postgresql_atomic_claim_takeover_and_stale_fencing(postgres_engine: Engine) -> None:
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    second_engine = create_database_engine(database_url)
    first_factory = create_session_factory(postgres_engine)
    second_factory = create_session_factory(second_engine)
    context = local_workspace_context()
    try:
        with session_scope(first_factory) as session:
            seed_demo(session, context)
            repositories = Repositories(session, context)
            datasets, _ = repositories.datasets.list()
            prompts, _ = repositories.prompts.list()
            models, _ = repositories.models.list()
            run = repositories.runs.create(
                EvaluationRunCreate(
                    dataset_id=UUID(datasets[0].id),
                    prompt_ids=[UUID(prompts[0].id)],
                    model_ids=[UUID(models[0].id)],
                    name="PostgreSQL lease contention",
                ),
                application_version="postgresql-test",
            )
            run_id = run.id

        with first_factory() as session:
            queued_run = session.get(EvaluationRun, run_id)
            database_now = session.scalar(select(func.current_timestamp()))
            assert queued_run is not None
            assert database_now is not None
            assert queued_run.status is RunStatus.QUEUED
            assert queued_run.next_claim_at <= database_now

        first_manager = LeaseManager(first_factory, lease_seconds=10)
        second_manager = LeaseManager(second_factory, lease_seconds=10)

        def claim(manager: LeaseManager, worker_id: str):
            return manager.claim_next(worker_id, run_id=run_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(
                pool.map(
                    lambda pair: claim(*pair),
                    [(first_manager, "pg-worker-a"), (second_manager, "pg-worker-b")],
                )
            )
        active_claims = [claim_result for claim_result in claims if claim_result is not None]
        assert len(active_claims) == 1
        first_claim = active_claims[0]
        stale_manager = first_manager if first_claim.worker_id == "pg-worker-a" else second_manager
        takeover_manager = second_manager if stale_manager is first_manager else first_manager
        assert takeover_manager.claim_next("pg-observer", run_id=run_id) is None

        renewed = stale_manager.renew(first_claim)
        assert renewed.epoch == first_claim.epoch
        with session_scope(first_factory) as session:
            session.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

        takeover = takeover_manager.claim_next("pg-takeover", run_id=run_id)
        assert takeover is not None
        assert takeover.takeover is True
        assert takeover.epoch == first_claim.epoch + 1
        assert takeover.token != first_claim.token
        with pytest.raises(LeaseLostError):
            stale_manager.renew(first_claim)
        with first_factory() as session, pytest.raises(LeaseLostError):
            stale_manager.fence(session, first_claim)
        assert stale_manager.finish(first_claim, outcome="stale") is False
        assert takeover_manager.finish(takeover, outcome="completed") is True

        with first_factory() as session:
            attempts = list(
                session.scalars(
                    select(ExecutionAttempt)
                    .where(ExecutionAttempt.run_id == run_id)
                    .order_by(ExecutionAttempt.lease_epoch)
                )
            )
            assert [attempt.outcome for attempt in attempts] == ["lease_expired", "completed"]
    finally:
        second_engine.dispose()


def test_postgresql_api_workflow_and_cross_process_cancellation(
    postgres_engine: Engine,
) -> None:
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    factory = create_session_factory(postgres_engine)
    with session_scope(factory) as session:
        seed_demo(session, local_workspace_context())

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        auth_mode="local",
        api_host="127.0.0.1",
        dashboard_host="127.0.0.1",
        executor_mode="api_only",
        auto_migrate=False,
        seed_demo=False,
    )
    container = build_container(settings, migrate=False)
    application = create_app(settings, container=container)
    try:
        with TestClient(application) as client:
            ready = client.get("/health/ready")
            assert ready.status_code == 200
            assert ready.json() == {
                "status": "ready",
                "database": "ready",
                "worker": "external_unobserved",
                "worker_observed": False,
                "executor_role": "api_only",
            }

            datasets = client.get("/api/v1/datasets").json()["items"]
            prompts = client.get("/api/v1/prompts").json()["items"]
            models = client.get("/api/v1/models").json()["items"]
            response = client.post(
                "/api/v1/runs",
                headers={"Idempotency-Key": "postgresql-api-cancellation"},
                json={
                    "name": "PostgreSQL API cancellation",
                    "dataset_id": datasets[0]["id"],
                    "prompt_ids": [prompts[0]["id"]],
                    "model_ids": [models[0]["id"]],
                },
            )
            assert response.status_code == 202
            run_id = response.json()["id"]

            observer_engine = create_database_engine(database_url)
            try:
                observer_factory = create_session_factory(observer_engine)
                with observer_factory() as observer:
                    persisted = observer.scalar(
                        select(EvaluationRun).where(EvaluationRun.id == run_id)
                    )
                    assert persisted is not None
                    assert persisted.status is RunStatus.QUEUED
            finally:
                observer_engine.dispose()

            cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
    finally:
        asyncio.run(container.close())


def test_postgresql_run_admission_and_trial_cancellation_serialize(
    postgres_engine: Engine,
) -> None:
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        auth_mode="oidc",
        oidc_issuer="https://identity.postgres.test",
        oidc_audience="evalforge-api",
        oidc_jwks_url="https://identity.postgres.test/jwks.json",
        public_base_url="https://api.postgres.test",
        commercial_pilot_enabled=True,
        auto_migrate=False,
        seed_demo=False,
    )
    factory = create_session_factory(postgres_engine)
    context = local_workspace_context()
    with session_scope(factory) as session:
        seed_demo(session, context)
        repositories = Repositories(session, context)
        datasets, _ = repositories.datasets.list(limit=1)
        prompts, _ = repositories.prompts.list(limit=2)
        models, _ = repositories.models.list(limit=1)
        request = EvaluationRunCreate(
            dataset_id=UUID(datasets[0].id),
            prompt_ids=[UUID(prompt.id) for prompt in prompts],
            model_ids=[UUID(models[0].id)],
            name="Serialized commercial admission",
        )
        CommercialPilotService(session, context, settings).start_trial(
            idempotency_key="postgres-serialized-trial",
            request_id="postgres-test",
        )

    admission_locked = Event()
    release_admission = Event()

    def admit_run() -> str:
        with session_scope(factory) as session:
            require_run_entitlement(session, context, settings, lock=True)
            admission_locked.set()
            assert release_admission.wait(timeout=5)
            run = Repositories(session, context).runs.create(
                request,
                application_version="postgresql-test",
            )
            return run.id

    def cancel_trial() -> EntitlementStatus:
        assert admission_locked.wait(timeout=5)
        with session_scope(factory) as session:
            return (
                CommercialPilotService(session, context, settings)
                .cancel_trial(
                    idempotency_key="postgres-serialized-cancel",
                    request_id="postgres-test",
                )
                .status
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = pool.submit(admit_run)
        assert admission_locked.wait(timeout=5)
        canceled = pool.submit(cancel_trial)
        time.sleep(0.15)
        assert canceled.done() is False
        release_admission.set()
        run_id = admitted.result(timeout=5)
        assert canceled.result(timeout=5) is EntitlementStatus.CANCELED

    with session_scope(factory) as session:
        assert Repositories(session, context).runs.get(run_id).id == run_id
        entitlement = CommercialPilotService(session, context, settings).entitlement()
        assert entitlement.status is EntitlementStatus.CANCELED
        assert entitlement.can_start_runs is False
