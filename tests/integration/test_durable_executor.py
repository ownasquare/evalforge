from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from evalforge.config import Settings
from evalforge.database import (
    SessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from evalforge.evaluation.adapters import AdapterRegistry
from evalforge.evaluation.executor import LocalRunExecutor
from evalforge.evaluation.leases import LeaseClaim, LeaseLostError, LeaseManager
from evalforge.evaluation.metrics import MetricRegistry
from evalforge.evaluation.service import EvaluationService
from evalforge.evaluation.types import GenerationRequest, GenerationResponse, ProviderError
from evalforge.models import (
    Dataset,
    EvaluationRun,
    ExecutionAttempt,
    ResultStatus,
    RunStatus,
    canonical_json_hash,
)
from evalforge.repositories import Repositories
from evalforge.schemas import (
    DatasetCreate,
    EvaluationRunCreate,
    ModelProfileCreate,
    PromptTemplateCreate,
)
from evalforge.schemas import TestCaseCreate as CaseCreate
from evalforge.security.permissions import WorkspaceContext


class CountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        return GenerationResponse(
            text=request.expected_output or "durable provider output",
            provider="openai",
            model=request.model,
            api_mode=request.api_mode,
            input_tokens=4,
            output_tokens=3,
            total_tokens=7,
            latency_ms=5,
            request_id=f"provider-call-{self.calls}",
            finish_reason="stop",
            retry_count=0,
            metadata={"usage_reported": True},
        )


class BlockingAdapter(CountingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return await super().generate(request)


class RateLimitedAdapter(CountingAdapter):
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        del request
        self.calls += 1
        raise ProviderError(
            "provider rejected the request rate",
            code="provider_rate_limited",
            retryable=True,
            status_code=429,
            attempts=3,
        )


class TakeoverBeforeScoringService(EvaluationService):
    """Failure injector that transfers the lease after output persistence."""

    takeover_claim: LeaseClaim | None = None

    def _score_and_complete_result(self, result_id: str, claim: LeaseClaim) -> None:
        del result_id
        _set_expiry(
            self.session_factory,
            claim.run_id,
            _database_now(self.session_factory) - timedelta(seconds=1),
        )
        takeover = self.claim_next("persisted-output-resumer", run_id=claim.run_id)
        assert takeover is not None
        self.takeover_claim = takeover
        raise LeaseLostError("injected worker loss after provider evidence persistence")


def _create_queued_run(
    session_factory: SessionFactory,
    workspace_id: str,
    *,
    name: str,
) -> str:
    with session_scope(session_factory) as session:
        dataset_payload = {
            "name": f"{name} dataset",
            "version": 1,
            "description": None,
            "metadata": {},
            "cases": [],
        }
        dataset = Dataset(
            workspace_id=workspace_id,
            name=str(dataset_payload["name"]),
            description=None,
            version=1,
            content_hash=canonical_json_hash(dataset_payload),
            metadata_json={},
        )
        session.add(dataset)
        session.flush()
        dataset_snapshot = {
            "id": dataset.id,
            **dataset_payload,
            "content_hash": dataset.content_hash,
        }
        run = EvaluationRun(
            workspace_id=workspace_id,
            name=name,
            dataset=dataset,
            dataset_snapshot=dataset_snapshot,
            dataset_hash=dataset.content_hash,
            metric_configuration_snapshot={"metrics": []},
            preflight_snapshot={"case_count": 0, "provider_call_count": 0},
            application_version="durable-test",
            executor_type="database_worker",
            status=RunStatus.QUEUED,
            total_items=0,
            next_claim_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        session.add(run)
        session.flush()
        return run.id


def _claim_concurrently(
    first: LeaseManager,
    second: LeaseManager,
    run_id: str,
) -> list[LeaseClaim | None]:
    barrier = Barrier(3)

    def claim(manager: LeaseManager, worker_id: str) -> LeaseClaim | None:
        barrier.wait()
        return manager.claim_next(worker_id, run_id=run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(claim, first, "worker-a"),
            pool.submit(claim, second, "worker-b"),
        ]
        barrier.wait()
        return [future.result(timeout=5) for future in futures]


def _database_now(session_factory: SessionFactory) -> datetime:
    with session_factory() as session:
        value = session.scalar(select(func.current_timestamp()))
    assert isinstance(value, datetime)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _set_expiry(
    session_factory: SessionFactory,
    run_id: str,
    expires_at: datetime,
) -> None:
    with session_scope(session_factory) as session:
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.lease_expires_at = expires_at


def _service(settings: Settings, session_factory: SessionFactory) -> EvaluationService:
    configured = settings.model_copy(
        update={
            "worker_poll_interval_seconds": 0.1,
            "worker_lease_seconds": 10,
            "worker_heartbeat_seconds": 1,
        }
    )
    return EvaluationService(
        settings=configured,
        session_factory=session_factory,
        adapters=AdapterRegistry(),
        metrics=MetricRegistry(),
    )


def _provider_service(
    settings: Settings,
    session_factory: SessionFactory,
    adapter: CountingAdapter,
    *,
    service_class: type[EvaluationService] = EvaluationService,
) -> EvaluationService:
    configured = settings.model_copy(
        update={
            "real_runs_enabled": True,
            "openai_model_allowlist": ["gpt-durable-test"],
            "max_concurrent_generations": 1,
            "worker_poll_interval_seconds": 0.1,
            "worker_lease_seconds": 10,
            "worker_heartbeat_seconds": 5,
        }
    )
    adapters = AdapterRegistry()
    adapters.register("openai", adapter)
    return service_class(
        settings=configured,
        session_factory=session_factory,
        adapters=adapters,
        metrics=MetricRegistry(),
    )


def _create_runnable_run(
    service: EvaluationService,
    session_factory: SessionFactory,
    context: WorkspaceContext,
) -> str:
    with session_scope(session_factory) as session:
        repositories = Repositories(session, context)
        dataset = repositories.datasets.create(
            DatasetCreate(
                name="Durable discovery dataset",
                cases=[
                    CaseCreate(
                        external_id="durable-case",
                        position=0,
                        input_text="Return the expected value.",
                        expected_output="durable",
                    )
                ],
            )
        )
        prompt = repositories.prompts.create(
            PromptTemplateCreate(
                name="Durable discovery prompt",
                user_template="{input}",
            )
        )
        model = repositories.models.create(
            ModelProfileCreate(
                name="Durable deterministic model",
                provider="demo",
                model_name="demo-reliable",
                api_mode="deterministic",
                generation_parameters={
                    "temperature": 0.0,
                    "max_output_tokens": 32,
                    "seed": 7,
                },
                metadata_json={"synthetic": True, "pricing_known": True},
            )
        )
        dataset_id, prompt_id, model_id = dataset.id, prompt.id, model.id

    run = service.create_run(
        EvaluationRunCreate(
            dataset_id=UUID(dataset_id),
            prompt_ids=[UUID(prompt_id)],
            model_ids=[UUID(model_id)],
        ),
        context,
    )
    return run.id


def _create_provider_run(
    service: EvaluationService,
    session_factory: SessionFactory,
    context: WorkspaceContext,
    *,
    name: str,
) -> str:
    with session_scope(session_factory) as session:
        repositories = Repositories(session, context)
        dataset = repositories.datasets.create(
            DatasetCreate(
                name=f"{name} dataset",
                cases=[
                    CaseCreate(
                        external_id=f"{name}-case",
                        position=0,
                        input_text="Return the expected value.",
                        expected_output="durable",
                    )
                ],
            )
        )
        prompt = repositories.prompts.create(
            PromptTemplateCreate(
                name=f"{name} prompt",
                user_template="{input}",
            )
        )
        model = repositories.models.create(
            ModelProfileCreate(
                name=f"{name} model",
                provider="openai",
                model_name="gpt-durable-test",
                api_mode="responses",
                generation_parameters={
                    "temperature": 0.0,
                    "max_output_tokens": 32,
                },
                input_price_micro_usd_per_million_tokens=1,
                output_price_micro_usd_per_million_tokens=1,
                pricing_source="test fixture",
            )
        )
        dataset_id, prompt_id, model_id = dataset.id, prompt.id, model.id

    run = service.create_run(
        EvaluationRunCreate(
            dataset_id=UUID(dataset_id),
            prompt_ids=[UUID(prompt_id)],
            model_ids=[UUID(model_id)],
            acknowledge_real_cost=True,
            acknowledge_external_data_transfer=True,
            spend_limit_micro_usd=1_000_000,
        ),
        context,
    )
    _make_claimable(session_factory, run.id)
    return run.id


def _make_claimable(session_factory: SessionFactory, run_id: str) -> None:
    with session_scope(session_factory) as session:
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.next_claim_at = datetime(1970, 1, 1, tzinfo=UTC)


@pytest.mark.integration
def test_two_managers_atomically_claim_one_run_across_two_engines(
    database_url: str,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Atomic claim",
    )
    second_engine = create_database_engine(database_url)
    second_factory = create_session_factory(second_engine)
    try:
        claims = _claim_concurrently(
            LeaseManager(session_factory, lease_seconds=30),
            LeaseManager(second_factory, lease_seconds=30),
            run_id,
        )
    finally:
        second_engine.dispose()

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.run_id == run_id
    assert winner.epoch == 1
    assert winner.takeover is False

    with session_factory() as session:
        run = session.get(EvaluationRun, run_id)
        attempts = list(
            session.scalars(select(ExecutionAttempt).where(ExecutionAttempt.run_id == run_id))
        )
    assert run is not None
    assert run.lease_owner == winner.worker_id
    assert run.lease_token == winner.token
    assert run.lease_epoch == winner.epoch
    assert run.claim_attempts == 1
    assert len(attempts) == 1
    assert attempts[0].lease_token == winner.token


@pytest.mark.integration
def test_committed_queued_work_is_discoverable_without_submit(
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Committed discovery",
    )

    claim = LeaseManager(session_factory, lease_seconds=30).claim_next("fresh-process")

    assert claim is not None
    assert claim.run_id == run_id
    assert claim.workspace_id == workspace_context.workspace_id


@pytest.mark.integration
def test_heartbeat_renewal_blocks_takeover_past_the_old_deadline(
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Heartbeat",
    )
    owner = LeaseManager(session_factory, lease_seconds=10)
    contender = LeaseManager(session_factory, lease_seconds=10)
    claim = owner.claim_next("heartbeat-owner", run_id=run_id)
    assert claim is not None

    old_deadline = _database_now(session_factory) + timedelta(seconds=1)
    _set_expiry(session_factory, run_id, old_deadline)
    renewed = owner.renew(claim)
    assert renewed.token == claim.token
    assert renewed.epoch == claim.epoch
    assert renewed.expires_at > old_deadline

    time.sleep(1.1)
    assert contender.claim_next("takeover-too-early", run_id=run_id) is None


@pytest.mark.integration
def test_expiry_transfers_ownership_and_rejects_every_stale_operation(
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Lease transfer",
    )
    first_manager = LeaseManager(session_factory, lease_seconds=30)
    second_manager = LeaseManager(session_factory, lease_seconds=30)
    first = first_manager.claim_next("worker-before-expiry", run_id=run_id)
    assert first is not None
    _set_expiry(
        session_factory,
        run_id,
        _database_now(session_factory) - timedelta(seconds=1),
    )

    second = second_manager.claim_next("worker-after-expiry", run_id=run_id)
    assert second is not None
    assert second.takeover is True
    assert second.epoch == first.epoch + 1
    assert second.token != first.token
    assert second.worker_id != first.worker_id

    with pytest.raises(LeaseLostError):
        first_manager.renew(first)
    with session_factory() as session, pytest.raises(LeaseLostError):
        first_manager.fence(session, first)
    assert first_manager.finish(first, outcome="stale_worker_rejected") is False

    with session_factory() as session:
        run = session.get(EvaluationRun, run_id)
    assert run is not None
    assert run.lease_owner == second.worker_id
    assert run.lease_token == second.token
    assert run.lease_epoch == second.epoch

    assert second_manager.finish(second, outcome="completed") is True
    with session_factory() as session:
        attempts = list(
            session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.run_id == run_id)
                .order_by(ExecutionAttempt.lease_epoch)
            )
        )
    assert [(attempt.lease_epoch, attempt.lease_token) for attempt in attempts] == [
        (first.epoch, first.token),
        (second.epoch, second.token),
    ]
    assert attempts[0].outcome == "lease_expired"
    assert attempts[0].finished_at is not None
    assert attempts[1].outcome == "completed"
    assert attempts[1].finished_at is not None


@pytest.mark.integration
def test_attempt_finish_is_idempotent_and_preserves_first_outcome(
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Attempt evidence",
    )
    manager = LeaseManager(session_factory, lease_seconds=30)
    claim = manager.claim_next("evidence-worker", run_id=run_id)
    assert claim is not None
    renewed = manager.renew(claim)

    assert manager.finish(renewed, outcome="completed") is True
    assert manager.finish(renewed, outcome="must_not_replace", error_type="stale") is False

    with session_factory() as session:
        run = session.get(EvaluationRun, run_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.run_id == run_id,
                ExecutionAttempt.lease_epoch == renewed.epoch,
            )
        )
    assert run is not None
    assert run.lease_owner is None
    assert run.lease_token is None
    assert run.lease_expires_at is None
    assert run.lease_epoch == renewed.epoch
    assert attempt is not None
    assert attempt.lease_owner == renewed.worker_id
    assert attempt.lease_token == renewed.token
    assert attempt.heartbeat_at >= attempt.started_at
    assert attempt.finished_at is not None
    assert attempt.outcome == "completed"
    assert attempt.error_type is None


@pytest.mark.integration
def test_expired_owner_cannot_finish_and_run_remains_takeover_eligible(
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Expired finish fence",
    )
    manager = LeaseManager(session_factory, lease_seconds=30)
    claim = manager.claim_next("expired-owner", run_id=run_id)
    assert claim is not None
    with session_scope(session_factory) as session:
        manager.fence(session, claim)
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.transition_to(RunStatus.RUNNING, reason="worker started")
    _set_expiry(
        session_factory,
        run_id,
        _database_now(session_factory) - timedelta(seconds=1),
    )

    assert manager.finish(claim, outcome="must_not_release") is False

    with session_factory() as session:
        run = session.get(EvaluationRun, run_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.run_id == run_id,
                ExecutionAttempt.lease_epoch == claim.epoch,
            )
        )
    assert run is not None
    assert run.lease_token == claim.token
    assert attempt is not None
    assert attempt.outcome == "lease_lost"
    assert attempt.error_type == "LeaseLostError"

    takeover = manager.claim_next("takeover-owner", run_id=run_id)
    assert takeover is not None
    assert takeover.takeover is True
    assert manager.finish(takeover, outcome="completed") is True


@pytest.mark.integration
def test_cancellation_committed_by_an_api_process_is_visible_to_the_lease_owner(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Cross-process cancellation",
    )
    manager = LeaseManager(session_factory, lease_seconds=30)
    claim = manager.claim_next("database-worker", run_id=run_id)
    assert claim is not None
    with session_scope(session_factory) as session:
        manager.fence(session, claim)
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.transition_to(RunStatus.RUNNING, reason="worker started")

    service = _service(settings, session_factory)
    cancelled = service.cancel_run(run_id, workspace_context)
    assert cancelled.status is RunStatus.CANCEL_REQUESTED

    with session_factory() as session:
        manager.fence(session, claim)
        visible = session.get(EvaluationRun, run_id)
        assert visible is not None
        assert visible.status is RunStatus.CANCEL_REQUESTED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_claim_before_provider_invocation_replays_once_and_keeps_attempt_evidence(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    adapter = CountingAdapter()
    service = _provider_service(settings, session_factory, adapter)
    run_id = _create_provider_run(
        service,
        session_factory,
        workspace_context,
        name="Safe queued replay",
    )
    first = service.claim_next("worker-that-died-before-provider", run_id=run_id)
    assert first is not None
    _set_expiry(
        session_factory,
        run_id,
        _database_now(session_factory) - timedelta(seconds=1),
    )
    takeover = service.claim_next("safe-replay-worker", run_id=run_id)
    assert takeover is not None
    assert takeover.takeover is True

    await service.execute_run(run_id, takeover)

    with session_factory() as session:
        run = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        attempts = list(
            session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.run_id == run_id)
                .order_by(ExecutionAttempt.lease_epoch)
            )
        )
        assert run.status is RunStatus.COMPLETED
        assert [result.status for result in run.results] == [ResultStatus.COMPLETED]
        assert len(run.results) == 1
        assert adapter.calls == 1
        assert len(attempts) == 2
        assert attempts[0].lease_epoch == first.epoch
        assert attempts[0].finished_at is not None
        assert attempts[0].outcome in {"lease_expired", "taken_over"}
        assert attempts[1].lease_epoch == takeover.epoch
        assert attempts[1].outcome == "completed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_takeover_never_replays_a_billing_ambiguous_provider_invocation(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    adapter = BlockingAdapter()
    service = _provider_service(settings, session_factory, adapter)
    run_id = _create_provider_run(
        service,
        session_factory,
        workspace_context,
        name="Ambiguous invocation",
    )
    first = service.claim_next("provider-invoker", run_id=run_id)
    assert first is not None
    first_execution = asyncio.create_task(service.execute_run(run_id, first))
    await asyncio.wait_for(adapter.started.wait(), timeout=2)

    _set_expiry(
        session_factory,
        run_id,
        _database_now(session_factory) - timedelta(seconds=1),
    )
    takeover = service.claim_next("ambiguity-resolver", run_id=run_id)
    assert takeover is not None
    assert takeover.takeover is True
    adapter.release.set()
    await asyncio.wait_for(first_execution, timeout=2)

    with session_factory() as session:
        before_recovery = Repositories(session, workspace_context).runs.get(
            run_id,
            with_detail=True,
        )
        assert len(before_recovery.results) == 1
        assert before_recovery.results[0].status is ResultStatus.RUNNING
        assert before_recovery.results[0].output_text is None

    await service.execute_run(run_id, takeover)

    with session_factory() as session:
        recovered = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        attempts = list(
            session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.run_id == run_id)
                .order_by(ExecutionAttempt.lease_epoch)
            )
        )
        assert adapter.calls == 1
        assert recovered.status is RunStatus.COMPLETED_WITH_ERRORS
        assert len(recovered.results) == 1
        result = recovered.results[0]
        assert result.status is ResultStatus.INTERRUPTED
        assert result.error_retryable is False
        assert result.output_text is None
        assert result.estimated_cost_micro_usd is None
        assert result.cost_source == "billing_ambiguous"
        assert attempts[0].outcome == "lease_expired"
        assert attempts[1].outcome == "completed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_failure_stops_provider_work_and_closes_attempt_for_safe_takeover(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = BlockingAdapter()
    service = _provider_service(settings, session_factory, adapter)
    run_id = _create_provider_run(
        service,
        session_factory,
        workspace_context,
        name="Heartbeat failure containment",
    )
    claim = service.claim_next("heartbeat-failure-worker", run_id=run_id)
    assert claim is not None
    original_heartbeat = service._heartbeat_claim

    async def fail_after_provider_starts(_claim: LeaseClaim) -> None:
        await adapter.started.wait()
        raise RuntimeError("injected heartbeat storage failure")

    monkeypatch.setattr(service, "_heartbeat_claim", fail_after_provider_starts)
    await asyncio.wait_for(service.execute_run(run_id, claim), timeout=2)

    assert adapter.started.is_set()
    assert adapter.cancelled.is_set()
    adapter.release.set()
    await asyncio.sleep(0)
    assert adapter.calls == 0

    with session_factory() as session:
        interrupted = Repositories(session, workspace_context).runs.get(
            run_id,
            with_detail=True,
        )
        first_attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.run_id == run_id,
                ExecutionAttempt.lease_epoch == claim.epoch,
            )
        )
        assert interrupted.status is RunStatus.RUNNING
        assert interrupted.lease_owner == claim.worker_id
        assert interrupted.lease_token == claim.token
        assert len(interrupted.results) == 1
        assert interrupted.results[0].status is ResultStatus.RUNNING
        assert interrupted.results[0].output_text is None
        assert first_attempt is not None
        assert first_attempt.finished_at is not None
        assert first_attempt.outcome == "heartbeat_failed"
        assert first_attempt.error_type == "RuntimeError"

    _set_expiry(
        session_factory,
        run_id,
        _database_now(session_factory) - timedelta(seconds=1),
    )
    takeover = service.claim_next("heartbeat-recovery-worker", run_id=run_id)
    assert takeover is not None
    assert takeover.takeover is True
    monkeypatch.setattr(service, "_heartbeat_claim", original_heartbeat)
    await service.execute_run(run_id, takeover)

    with session_factory() as session:
        recovered = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        attempts = list(
            session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.run_id == run_id)
                .order_by(ExecutionAttempt.lease_epoch)
            )
        )
        assert recovered.status is RunStatus.COMPLETED_WITH_ERRORS
        assert recovered.results[0].status is ResultStatus.INTERRUPTED
        assert recovered.results[0].cost_source == "billing_ambiguous"
        assert adapter.calls == 0
        assert [attempt.outcome for attempt in attempts] == [
            "heartbeat_failed",
            "completed",
        ]
        assert all(attempt.finished_at is not None for attempt in attempts)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_takeover_scores_persisted_output_without_a_second_provider_call(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    adapter = CountingAdapter()
    interrupted_service = _provider_service(
        settings,
        session_factory,
        adapter,
        service_class=TakeoverBeforeScoringService,
    )
    assert isinstance(interrupted_service, TakeoverBeforeScoringService)
    run_id = _create_provider_run(
        interrupted_service,
        session_factory,
        workspace_context,
        name="Persisted output resume",
    )
    first = interrupted_service.claim_next("generation-worker", run_id=run_id)
    assert first is not None

    await interrupted_service.execute_run(run_id, first)
    takeover = interrupted_service.takeover_claim
    assert takeover is not None
    with session_factory() as session:
        interrupted = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        assert len(interrupted.results) == 1
        result = interrupted.results[0]
        assert result.status is ResultStatus.RUNNING
        assert result.output_text == "durable"
        assert result.provider == "openai"
        assert not result.metric_results
        assert adapter.calls == 1

    resuming_service = _provider_service(settings, session_factory, adapter)
    await resuming_service.execute_run(run_id, takeover)

    with session_factory() as session:
        completed = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        assert completed.status is RunStatus.COMPLETED
        assert len(completed.results) == 1
        result = completed.results[0]
        assert result.status is ResultStatus.COMPLETED
        assert result.output_text == "durable"
        assert result.aggregate_score is not None
        assert result.metric_results
        assert adapter.calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rate_limited_provider_records_billing_ambiguity(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    adapter = RateLimitedAdapter()
    service = _provider_service(settings, session_factory, adapter)
    run_id = _create_provider_run(
        service,
        session_factory,
        workspace_context,
        name="Rate limited billing",
    )
    claim = service.claim_next("rate-limited-worker", run_id=run_id)
    assert claim is not None

    await service.execute_run(run_id, claim)

    with session_factory() as session:
        run = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        assert run.status is RunStatus.COMPLETED_WITH_ERRORS
        assert len(run.results) == 1
        result = run.results[0]
        assert result.status is ResultStatus.ERROR
        assert result.error_type == "provider_rate_limited"
        assert result.retry_count == 2
        assert result.estimated_cost_micro_usd is None
        assert result.cost_source == "billing_ambiguous"
        assert adapter.calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_error", [LeaseLostError, StaleDataError])
async def test_ownership_failure_closes_attempt_without_releasing_active_run(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    ownership_error: type[Exception],
) -> None:
    adapter = CountingAdapter()
    service = _provider_service(settings, session_factory, adapter)
    run_id = _create_provider_run(
        service,
        session_factory,
        workspace_context,
        name=f"Ownership failure {ownership_error.__name__}",
    )
    claim = service.claim_next("ownership-failure-worker", run_id=run_id)
    assert claim is not None
    with session_scope(session_factory) as session:
        service.leases.fence(session, claim)
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.transition_to(RunStatus.RUNNING, reason="worker started")

    original_prepare = service._prepare_execution

    def raise_ownership_error(_claim: LeaseClaim) -> list[str]:
        raise ownership_error("injected ownership failure")

    monkeypatch.setattr(service, "_prepare_execution", raise_ownership_error)
    await service.execute_run(run_id, claim)

    with session_factory() as session:
        interrupted = session.get(EvaluationRun, run_id)
        first_attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.run_id == run_id,
                ExecutionAttempt.lease_epoch == claim.epoch,
            )
        )
    assert interrupted is not None
    assert interrupted.lease_token == claim.token
    assert first_attempt is not None
    assert first_attempt.outcome == "lease_lost"
    assert first_attempt.error_type == ownership_error.__name__

    _set_expiry(
        session_factory,
        run_id,
        _database_now(session_factory) - timedelta(seconds=1),
    )
    takeover = service.claim_next("ownership-recovery-worker", run_id=run_id)
    assert takeover is not None
    monkeypatch.setattr(service, "_prepare_execution", original_prepare)
    await service.execute_run(run_id, takeover)

    with session_factory() as session:
        recovered = Repositories(session, workspace_context).runs.get(run_id, with_detail=True)
        attempts = list(
            session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.run_id == run_id)
                .order_by(ExecutionAttempt.lease_epoch)
            )
        )
    assert recovered.status is RunStatus.COMPLETED
    assert adapter.calls == 1
    assert [attempt.outcome for attempt in attempts] == ["lease_lost", "completed"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_executor_discovers_a_committed_run_without_submit(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    service = _service(settings, session_factory)
    run_id = _create_runnable_run(service, session_factory, workspace_context)
    executor = LocalRunExecutor(service)

    await executor.start()
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            with session_factory() as session:
                run = session.get(EvaluationRun, run_id)
                assert run is not None
                if run.status.is_terminal:
                    break
            await asyncio.sleep(0.02)
        assert run.status is RunStatus.COMPLETED
    finally:
        await executor.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_starting_another_executor_preserves_an_unexpired_active_claim(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    run_id = _create_queued_run(
        session_factory,
        workspace_context.workspace_id,
        name="Restart preservation",
    )
    manager = LeaseManager(session_factory, lease_seconds=30)
    claim = manager.claim_next("surviving-worker", run_id=run_id)
    assert claim is not None
    with session_scope(session_factory) as session:
        manager.fence(session, claim)
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.transition_to(RunStatus.RUNNING, reason="active owner is still healthy")

    restarted_executor = LocalRunExecutor(_service(settings, session_factory))
    await restarted_executor.start()
    try:
        await asyncio.sleep(0.15)
        with session_factory() as session:
            preserved = session.get(EvaluationRun, run_id)
        assert preserved is not None
        assert preserved.status is RunStatus.RUNNING
        assert preserved.lease_owner == claim.worker_id
        assert preserved.lease_token == claim.token
        assert preserved.lease_epoch == claim.epoch
    finally:
        close_task = asyncio.create_task(restarted_executor.close())
        done, _pending = await asyncio.wait({close_task}, timeout=0.5)
        closed_promptly = close_task in done
        if not closed_promptly:
            close_task.cancel()
            with suppress(asyncio.CancelledError):
                await close_task
        assert closed_promptly, "an idle executor must stop without waiting for another lease"
