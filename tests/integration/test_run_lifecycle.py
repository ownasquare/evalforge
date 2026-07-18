from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from evalforge.analytics import build_run_comparison
from evalforge.config import Settings
from evalforge.database import SessionFactory, session_scope
from evalforge.evaluation.adapters import AdapterRegistry
from evalforge.evaluation.metrics import MetricRegistry
from evalforge.evaluation.service import CapabilityError, EvaluationService, LimitError
from evalforge.evaluation.types import ApiMode as AdapterApiMode
from evalforge.evaluation.types import EvaluationCase, GenerationRequest, GenerationResponse
from evalforge.models import ResultStatus, RunStatus
from evalforge.repositories import Repositories
from evalforge.schemas import (
    DatasetCreate,
    EvaluationRunCreate,
    ModelProfileCreate,
    PromptTemplateCreate,
)
from evalforge.schemas import TestCaseCreate as CaseCreate
from evalforge.security.permissions import WorkspaceContext


class BlockingAdapter:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return GenerationResponse(
            text=request.expected_output or "completed",
            provider="openai",
            model=request.model,
            api_mode=request.api_mode,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=10,
            request_id="fake_request",
            finish_reason="stop",
            metadata={"usage_reported": False},
        )


class ExplodingMetricRegistry(MetricRegistry):
    def evaluate(self, _case: EvaluationCase) -> tuple[()]:
        raise RuntimeError("intentional scoring failure")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_inflight_cancellation_finishes_without_duplicate_provider_calls(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    configured = settings.model_copy(
        update={
            "real_runs_enabled": True,
            "openai_model_allowlist": ["gpt-test"],
            "max_concurrent_generations": 1,
        }
    )
    with session_scope(session_factory) as session:
        repositories = Repositories(session, workspace_context)
        dataset = repositories.datasets.create(
            DatasetCreate(
                name="Cancellation benchmark",
                cases=[
                    CaseCreate(
                        external_id="first",
                        position=0,
                        input_text="First question",
                        expected_output="First answer",
                    ),
                    CaseCreate(
                        external_id="second",
                        position=1,
                        input_text="Second question",
                        expected_output="Second answer",
                    ),
                ],
            )
        )
        prompt = repositories.prompts.create(
            PromptTemplateCreate(name="Cancellation prompt", user_template="{input}")
        )
        model = repositories.models.create(
            ModelProfileCreate(
                name="Blocking provider",
                provider="openai",
                model_name="gpt-test",
                api_mode="responses",
                generation_parameters={"temperature": 0.0, "max_output_tokens": 64},
                input_price_micro_usd_per_million_tokens=1_000_000,
                output_price_micro_usd_per_million_tokens=1_000_000,
                pricing_source="test fixture",
            )
        )
        dataset_id, prompt_id, model_id = dataset.id, prompt.id, model.id

    adapter = BlockingAdapter()
    registry = AdapterRegistry()
    registry.register("openai", adapter)
    service = EvaluationService(
        settings=configured,
        session_factory=session_factory,
        adapters=registry,
        metrics=MetricRegistry(),
    )
    request = EvaluationRunCreate(
        dataset_id=UUID(dataset_id),
        prompt_ids=[UUID(prompt_id)],
        model_ids=[UUID(model_id)],
    )
    with pytest.raises(CapabilityError, match="cost acknowledgment"):
        service.preflight(request, workspace_context)
    cost_acknowledged = request.model_copy(update={"acknowledge_real_cost": True})
    with pytest.raises(CapabilityError, match="data-transfer consent"):
        service.preflight(cost_acknowledged, workspace_context)
    transfer_acknowledged = cost_acknowledged.model_copy(
        update={"acknowledge_external_data_transfer": True}
    )
    with pytest.raises(CapabilityError, match="spend ceiling"):
        service.preflight(transfer_acknowledged, workspace_context)
    with pytest.raises(LimitError, match="configured server cap"):
        service.preflight(
            transfer_acknowledged.model_copy(
                update={
                    "spend_limit_micro_usd": (configured.max_estimated_cost_micro_usd_per_run + 1)
                }
            ),
            workspace_context,
        )
    with pytest.raises(LimitError, match="user-selected spend ceiling"):
        service.preflight(
            transfer_acknowledged.model_copy(update={"spend_limit_micro_usd": 1}),
            workspace_context,
        )

    run = service.create_run(
        transfer_acknowledged.model_copy(update={"spend_limit_micro_usd": 1_000_000}),
        workspace_context,
    )
    assert run.preflight_snapshot["external_data_transfer_acknowledged"] is True
    assert run.preflight_snapshot["spend_limit_micro_usd"] == 1_000_000

    execution = asyncio.create_task(service.execute_run(run.id))
    await asyncio.wait_for(adapter.started.wait(), timeout=2)
    cancellation = service.cancel_run(run.id, workspace_context)
    assert cancellation.status is RunStatus.CANCEL_REQUESTED
    assert cancellation.candidates[0].status is RunStatus.CANCEL_REQUESTED
    adapter.release.set()
    await asyncio.wait_for(execution, timeout=2)

    with session_factory() as session:
        completed = Repositories(session, workspace_context).runs.get(run.id, with_detail=True)
        statuses = sorted(result.status.value for result in completed.results)
        completed_result = next(
            result for result in completed.results if result.status is ResultStatus.COMPLETED
        )
        assert completed.status is RunStatus.CANCELLED
        assert completed.candidates[0].status is RunStatus.CANCELLED
        assert statuses == ["cancelled", "completed"]
        assert completed.completed_items == completed.total_items == 2
        assert completed.succeeded_items == 1
        assert adapter.calls == 1
        assert completed_result.estimated_cost_micro_usd is None
        assert completed_result.cost_source == "usage_unavailable"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_evidence_survives_scoring_failure_and_is_counted_operationally(
    settings: Settings,
    session_factory: SessionFactory,
    workspace_context: WorkspaceContext,
) -> None:
    with session_scope(session_factory) as session:
        repositories = Repositories(session, workspace_context)
        dataset = repositories.datasets.create(
            DatasetCreate(
                name="Scoring failure benchmark",
                cases=[
                    CaseCreate(
                        external_id="case-1",
                        position=0,
                        input_text="What is the answer?",
                        expected_output="The answer is recorded.",
                    )
                ],
            )
        )
        prompt = repositories.prompts.create(
            PromptTemplateCreate(name="Scoring failure prompt", user_template="{input}")
        )
        model = repositories.models.create(
            ModelProfileCreate(
                name="Scoring failure demo",
                provider="demo",
                model_name="demo-reliable",
                api_mode="deterministic",
                generation_parameters={
                    "temperature": 0.0,
                    "max_output_tokens": 64,
                    "seed": 7,
                },
                metadata_json={"synthetic": True, "pricing_known": True},
            )
        )
        dataset_id, prompt_id, model_id = dataset.id, prompt.id, model.id

    service = EvaluationService(
        settings=settings,
        session_factory=session_factory,
        adapters=AdapterRegistry(),
        metrics=ExplodingMetricRegistry(),
    )
    run = service.create_run(
        EvaluationRunCreate(
            dataset_id=UUID(dataset_id),
            prompt_ids=[UUID(prompt_id)],
            model_ids=[UUID(model_id)],
        ),
        workspace_context,
    )

    await service.execute_run(run.id)

    with session_factory() as session:
        completed = Repositories(session, workspace_context).runs.get(run.id, with_detail=True)
        result = completed.results[0]
        comparison = build_run_comparison(session, workspace_context.workspace_id, run.id)
        candidate = comparison["candidates"][0]
        assert completed.status is RunStatus.COMPLETED_WITH_ERRORS
        assert result.status is ResultStatus.ERROR
        assert result.error_type == "scoring_error"
        assert result.output_text == "The answer is recorded."
        assert result.provider == "deterministic-demo"
        assert result.cost_source == "synthetic"
        assert result.estimated_cost_micro_usd == 0
        assert result.total_tokens > 0
        assert candidate["completed"] == 0
        assert candidate["generated"] == 1
        assert candidate["known_cost_items"] == 1
        assert candidate["token_usage_items"] == 1
        assert candidate["total_tokens"] == result.total_tokens


@pytest.mark.integration
def test_provider_evidence_is_bounded_before_database_persistence(
    settings: Settings,
    session_factory: SessionFactory,
    sample_result: Any,
    session: Any,
    workspace_context: WorkspaceContext,
) -> None:
    session.add(sample_result)
    sample_result.status = ResultStatus.RUNNING
    session.commit()

    service = EvaluationService(
        settings=settings,
        session_factory=session_factory,
        adapters=AdapterRegistry(),
        metrics=MetricRegistry(),
    )
    response = GenerationResponse(
        text="evidence",
        provider="p" * 300,
        model="m" * 400,
        api_mode=AdapterApiMode.RESPONSES,
        input_tokens=10**20,
        output_tokens=10**20,
        total_tokens=10**20,
        latency_ms=10**20,
        request_id="r" * 600,
        finish_reason="f" * 300,
        metadata={"usage_reported": True},
    )

    claim = service.claim_next("bounded-evidence-test", run_id=sample_result.run_id)
    assert claim is not None
    service._persist_generation_evidence(sample_result.id, response, claim)

    with session_factory() as session:
        persisted = Repositories(session, workspace_context).runs.get_result(sample_result.id)
        assert len(persisted.provider or "") <= 100
        assert len(persisted.model_name or "") <= 200
        assert len(persisted.request_id or "") <= 255
        assert len(persisted.finish_reason or "") <= 100
        assert persisted.input_tokens == 2_147_483_647
        assert persisted.output_tokens == 2_147_483_647
        assert persisted.total_tokens == 2_147_483_647
        assert persisted.latency_ms == 2_147_483_647
