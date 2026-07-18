"""Persistent evaluation orchestration across prompts, models, and test cases."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from evalforge.config import Settings
from evalforge.database import SessionFactory, session_scope
from evalforge.errors import CapabilityError, ConflictError, LimitError, NotFoundError
from evalforge.evaluation.adapters import (
    AdapterRegistry,
    DeterministicAdapter,
    resolve_demo_profile,
)
from evalforge.evaluation.metrics import MetricRegistry, aggregate_metric_results
from evalforge.evaluation.prompts import estimate_rendered_prompt_size, render_prompt
from evalforge.evaluation.types import (
    ApiMode as AdapterApiMode,
)
from evalforge.evaluation.types import (
    EvaluationCase,
    GenerationRequest,
    OutputConstraints,
    ProviderError,
)
from evalforge.evaluation.types import (
    MetricDirection as EvaluationMetricDirection,
)
from evalforge.evaluation.types import (
    MetricResult as EvaluationMetricResult,
)
from evalforge.models import (
    ApiMode,
    Dataset,
    EvaluationResult,
    EvaluationRun,
    MetricDirection,
    ModelProfile,
    PromptTemplate,
    ResultStatus,
    RunStatus,
    canonical_json_hash,
)
from evalforge.observability import get_logger
from evalforge.repositories import (
    ConflictError as RepositoryConflictError,
)
from evalforge.repositories import EvaluationRunRepository
from evalforge.repositories import (
    NotFoundError as RepositoryNotFoundError,
)
from evalforge.repositories import (
    ValidationError as RepositoryValidationError,
)
from evalforge.schemas import (
    EvaluationRunCreate,
    MetricConfiguration,
    validate_generation_parameters,
)

DEFAULT_METRIC_WEIGHTS: Mapping[str, float] = {
    "correctness": 1.0,
    "relevance": 1.0,
    "groundedness": 1.0,
    "hallucination_risk": 1.0,
    "phrase_coverage": 1.0,
    "json_validity": 1.0,
    "constraint_adherence": 1.0,
    "style_adherence": 0.5,
}
DEFAULT_THRESHOLDS: Mapping[str, float] = {
    "correctness": 0.7,
    "relevance": 0.55,
    "groundedness": 0.65,
    "hallucination_risk": 0.25,
    "phrase_coverage": 1.0,
    "json_validity": 1.0,
    "constraint_adherence": 1.0,
    "style_adherence": 0.8,
}
MAX_PERSISTED_OUTPUT_CHARS = 2_000_000
MAX_DATABASE_INTEGER = 2_147_483_647
INPUT_GUARD_METHOD = "utf8_bytes_plus_request_overhead_v2"


def default_metric_configurations(registry: MetricRegistry) -> list[MetricConfiguration]:
    """Return the visible, versioned default quality policy."""
    configurations: list[MetricConfiguration] = []
    for name, weight in DEFAULT_METRIC_WEIGHTS.items():
        direction = (
            MetricDirection.LOWER_IS_BETTER
            if name == "hallucination_risk"
            else MetricDirection.HIGHER_IS_BETTER
        )
        configurations.append(
            MetricConfiguration(
                name=name,
                version=registry.versions[name],
                direction=direction,
                weight=weight,
                threshold=DEFAULT_THRESHOLDS[name],
            )
        )
    return configurations


class EvaluationService:
    """Create, execute, recover, and cancel immutable evaluation runs."""

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        adapters: AdapterRegistry,
        metrics: MetricRegistry,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.adapters = adapters
        self.metrics = metrics
        self._generation_limit = asyncio.Semaphore(settings.max_concurrent_generations)
        self._logger = get_logger("evaluation_service")

    def preflight(self, data: EvaluationRunCreate) -> dict[str, Any]:
        """Validate the complete planned matrix before creating any billable work."""
        with self.session_factory() as session:
            snapshot, _dataset, _prompts, _models = self._preflight_in_session(
                data, session, lock=False
            )
            return snapshot

    def _preflight_in_session(
        self,
        data: EvaluationRunCreate,
        session: Session,
        *,
        lock: bool,
    ) -> tuple[dict[str, Any], Dataset, list[PromptTemplate], list[ModelProfile]]:
        """Validate and return the exact rows used to build an immutable run."""
        configurations = data.metrics or default_metric_configurations(self.metrics)
        self._validate_metric_configuration(configurations)
        dataset_statement = (
            select(Dataset)
            .where(Dataset.id == str(data.dataset_id))
            .options(selectinload(Dataset.cases))
        )
        prompt_statement = select(PromptTemplate).where(
            PromptTemplate.id.in_([str(item) for item in data.prompt_ids])
        )
        model_statement = select(ModelProfile).where(
            ModelProfile.id.in_([str(item) for item in data.model_ids])
        )
        if lock:
            dataset_statement = dataset_statement.with_for_update()
            prompt_statement = prompt_statement.with_for_update()
            model_statement = model_statement.with_for_update()
        dataset = session.scalar(dataset_statement)
        prompts_by_id = {item.id: item for item in session.scalars(prompt_statement)}
        models_by_id = {item.id: item for item in session.scalars(model_statement)}
        if dataset is None:
            raise NotFoundError("Evaluation input")
        try:
            prompts = [prompts_by_id[str(item)] for item in data.prompt_ids]
            models = [models_by_id[str(item)] for item in data.model_ids]
        except KeyError as exc:
            raise NotFoundError("Evaluation input") from exc
        if any(not model.enabled for model in models):
            raise CapabilityError("One or more model candidates are disabled.")

        case_count = len(dataset.cases)
        for case in dataset.cases:
            if len(case.input_text) > self.settings.max_input_chars_per_case:
                raise LimitError(
                    f"Case {case.external_id} exceeds the configured input character limit."
                )
            if (
                case.context_text is not None
                and len(case.context_text) > self.settings.max_context_chars_per_case
            ):
                raise LimitError(
                    f"Case {case.external_id} exceeds the configured context character limit."
                )
        for prompt in prompts:
            if (
                len(prompt.system_template) > self.settings.max_prompt_chars
                or len(prompt.user_template) > self.settings.max_prompt_chars
            ):
                raise LimitError(f"Prompt {prompt.name} exceeds the configured character limit.")
        for model in models:
            if model.api_mode is ApiMode.DETERMINISTIC:
                try:
                    resolve_demo_profile(model.model_name)
                except ValueError as exc:
                    raise CapabilityError(
                        f"Model {model.name} uses an unsupported deterministic profile."
                    ) from exc
            try:
                validate_generation_parameters(
                    model.generation_parameters,
                    max_output_tokens=self.settings.max_output_tokens,
                    allow_seed=model.api_mode is ApiMode.DETERMINISTIC,
                )
            except ValueError as exc:
                raise CapabilityError(f"Model {model.name}: {exc}") from exc
        variant_count = len(prompts) * len(models)
        call_count = case_count * variant_count
        if case_count == 0:
            raise LimitError("An evaluation dataset must contain at least one test case.")
        if case_count > self.settings.max_cases_per_dataset:
            raise LimitError(
                f"Dataset has {case_count} cases; the configured limit is "
                f"{self.settings.max_cases_per_dataset}."
            )
        if variant_count > self.settings.max_variants_per_run:
            raise LimitError(
                f"Run has {variant_count} variants; the configured limit is "
                f"{self.settings.max_variants_per_run}."
            )
        if call_count > self.settings.max_calls_per_run:
            raise LimitError(
                f"Run plans {call_count} calls; the configured limit is "
                f"{self.settings.max_calls_per_run}."
            )

        estimated_input_tokens_per_model = 0
        for prompt in prompts:
            for case in dataset.cases:
                rendered_size = estimate_rendered_prompt_size(
                    system_template=prompt.system_template,
                    user_template=prompt.user_template,
                    input_text=case.input_text,
                    context=case.context_text,
                    expected_output=case.expected_output,
                )
                if rendered_size.characters > self.settings.max_rendered_prompt_chars_per_call:
                    raise LimitError(
                        f"Prompt {prompt.name} expands beyond the per-call character limit."
                    )
                estimated_input_tokens_per_model += max(1, rendered_size.utf8_bytes) + (
                    self.settings.input_token_overhead_per_request
                )
        estimated_input_tokens = estimated_input_tokens_per_model * len(models)
        if estimated_input_tokens > self.settings.max_estimated_input_tokens_per_run:
            raise LimitError(
                f"Run estimates {estimated_input_tokens} input tokens; the configured limit is "
                f"{self.settings.max_estimated_input_tokens_per_run}."
            )

        real_models = [model for model in models if model.api_mode is not ApiMode.DETERMINISTIC]
        if real_models and not self.settings.real_runs_enabled:
            raise CapabilityError("Real-provider evaluation is disabled on the server.")
        if real_models and not bool(getattr(data, "acknowledge_real_cost", False)):
            raise CapabilityError("Real-provider runs require an explicit cost acknowledgment.")
        for model in real_models:
            self._validate_real_model(model)

        max_output_tokens = sum(
            min(
                int(model.generation_parameters.get("max_output_tokens", 512)),
                self.settings.max_output_tokens,
            )
            * case_count
            * len(prompts)
            for model in models
        )
        unknown_pricing = [
            model.name
            for model in real_models
            if model.input_price_micro_usd_per_million_tokens is None
            or model.output_price_micro_usd_per_million_tokens is None
        ]
        estimated_known_cost_micro_usd = 0
        for model in real_models:
            input_price = model.input_price_micro_usd_per_million_tokens
            output_price = model.output_price_micro_usd_per_million_tokens
            if input_price is None or output_price is None:
                continue
            model_output_tokens = (
                min(
                    int(model.generation_parameters.get("max_output_tokens", 512)),
                    self.settings.max_output_tokens,
                )
                * case_count
                * len(prompts)
            )
            estimated_known_cost_micro_usd += math.ceil(
                (
                    (estimated_input_tokens_per_model * input_price)
                    + (model_output_tokens * output_price)
                )
                / 1_000_000
            )
        if estimated_known_cost_micro_usd > self.settings.max_estimated_cost_micro_usd_per_run:
            raise LimitError("The estimated run cost exceeds the configured server budget.")
        missing_reference = sum(not bool(case.expected_output) for case in dataset.cases)
        missing_context = sum(not bool(case.context_text) for case in dataset.cases)
        preflight_snapshot = {
            "dataset_id": dataset.id,
            "case_count": case_count,
            "prompt_count": len(prompts),
            "model_count": len(models),
            "variant_count": variant_count,
            "provider_call_count": call_count,
            "max_requested_output_tokens": max_output_tokens,
            "estimated_input_tokens": estimated_input_tokens,
            "input_token_estimate_method": INPUT_GUARD_METHOD,
            "estimated_known_cost_micro_usd": estimated_known_cost_micro_usd,
            "cost_estimate_complete": not unknown_pricing,
            "real_provider": bool(real_models),
            "real_provider_models": [model.name for model in real_models],
            "unknown_pricing_models": unknown_pricing,
            "inapplicable_counts": {
                "correctness": missing_reference,
                "groundedness": missing_context,
                "hallucination_risk": missing_context,
            },
            "limits": {
                "max_cases": self.settings.max_cases_per_dataset,
                "max_variants": self.settings.max_variants_per_run,
                "max_calls": self.settings.max_calls_per_run,
                "max_output_tokens": self.settings.max_output_tokens,
                "max_concurrency": self.settings.max_concurrent_generations,
                "max_estimated_input_tokens": (self.settings.max_estimated_input_tokens_per_run),
                "input_token_overhead_per_request": (
                    self.settings.input_token_overhead_per_request
                ),
                "max_estimated_cost_micro_usd": (
                    self.settings.max_estimated_cost_micro_usd_per_run
                ),
            },
        }
        return preflight_snapshot, dataset, prompts, models

    def create_run(self, data: EvaluationRunCreate) -> EvaluationRun:
        """Persist a validated run and its immutable candidate snapshots."""
        prepared = (
            data
            if data.metrics
            else data.model_copy(update={"metrics": default_metric_configurations(self.metrics)})
        )
        request_hash = canonical_json_hash(
            prepared.model_dump(mode="json", exclude={"idempotency_key"})
        )
        if prepared.idempotency_key is not None:
            with self.session_factory() as session:
                existing = EvaluationRunRepository(session).find_by_idempotency_key(
                    prepared.idempotency_key
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ConflictError(
                            "Idempotency key was already used for a different request."
                        )
                    return existing
        try:
            with session_scope(self.session_factory) as session:
                preflight_snapshot, dataset, prompts, models = self._preflight_in_session(
                    prepared, session, lock=True
                )
                return EvaluationRunRepository(session).create(
                    prepared,
                    application_version=self.settings.application_version,
                    executor_type="persistent_local_worker",
                    dataset=dataset,
                    prompts_by_id={prompt.id: prompt for prompt in prompts},
                    models_by_id={model.id: model for model in models},
                    preflight_snapshot=preflight_snapshot,
                )
        except RepositoryConflictError as exc:
            if prepared.idempotency_key is not None:
                with self.session_factory() as session:
                    existing = EvaluationRunRepository(session).find_by_idempotency_key(
                        prepared.idempotency_key
                    )
                    if existing is not None and existing.request_hash == request_hash:
                        return existing
            raise ConflictError(str(exc)) from exc
        except RepositoryNotFoundError as exc:
            raise NotFoundError("Evaluation input") from exc
        except RepositoryValidationError as exc:
            raise LimitError(str(exc)) from exc

    def pending_run_ids(self) -> list[str]:
        """Return persisted queued work in stable FIFO order."""
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(EvaluationRun.id)
                    .where(EvaluationRun.status == RunStatus.QUEUED)
                    .order_by(EvaluationRun.queued_at)
                )
            )

    def recover_interrupted(self) -> int:
        """Mark previously in-flight work interrupted while preserving queued work."""
        with session_scope(self.session_factory) as session:
            return EvaluationRunRepository(session).recover_abandoned(
                reason="application restarted while work was in flight"
            )

    def cancel_run(self, run_id: str) -> EvaluationRun:
        """Cancel queued work immediately or request cooperative running cancellation."""
        for attempt in range(3):
            try:
                with session_scope(self.session_factory) as session:
                    repository = EvaluationRunRepository(session)
                    try:
                        run = repository.get(run_id, with_detail=True)
                    except RepositoryNotFoundError as exc:
                        raise NotFoundError("Evaluation run") from exc
                    if run.status.is_terminal:
                        return run
                    if run.status is RunStatus.QUEUED:
                        run.transition_to(RunStatus.CANCELLED, reason="cancelled before execution")
                        for candidate in run.candidates:
                            candidate.transition_to(RunStatus.CANCELLED, reason="run cancelled")
                    elif run.status is RunStatus.RUNNING:
                        run.transition_to(
                            RunStatus.CANCEL_REQUESTED, reason="cancellation requested"
                        )
                        for candidate in run.candidates:
                            if candidate.status is RunStatus.RUNNING:
                                candidate.transition_to(
                                    RunStatus.CANCEL_REQUESTED,
                                    reason="run cancellation requested",
                                )
                    session.flush()
                    return run
            except StaleDataError:
                if attempt == 2:
                    raise ConflictError(
                        "The run changed while cancellation was requested; retry the request."
                    ) from None
        raise AssertionError("unreachable cancellation retry state")

    async def execute_run(self, run_id: str) -> None:
        """Execute all queued case/candidate pairs with bounded provider concurrency."""
        try:
            result_ids = self._prepare_execution(run_id)
        except StaleDataError:
            self._logger.info("run_claim_lost", run_id=run_id)
            return
        except Exception as exc:
            self._fail_run_setup(run_id, exc)
            return
        if not result_ids:
            return
        try:
            outcomes = await asyncio.gather(
                *(self._execute_result(result_id) for result_id in result_ids),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    self._logger.error(
                        "result_task_unhandled",
                        run_id=run_id,
                        error_type=type(outcome).__name__,
                    )
        finally:
            self._refresh_progress(run_id)

    def _prepare_execution(self, run_id: str) -> list[str]:
        with session_scope(self.session_factory) as session:
            repository = EvaluationRunRepository(session)
            run = repository.get(run_id, with_detail=True)
            if run.status is not RunStatus.QUEUED:
                return []
            run.transition_to(RunStatus.RUNNING, reason="local worker claimed run")
            result_ids: list[str] = []
            cases = list(run.dataset_snapshot.get("cases", []))
            for candidate in sorted(run.candidates, key=lambda item: item.ordinal):
                candidate.transition_to(RunStatus.RUNNING, reason="candidate execution started")
                for case in cases:
                    rendered = render_prompt(
                        system_template=str(candidate.prompt_snapshot.get("system_template", "")),
                        user_template=str(candidate.prompt_snapshot["user_template"]),
                        input_text=str(case["input"]),
                        context=case.get("context"),
                        expected_output=case.get("expected_output"),
                    )
                    result = EvaluationResult(
                        run_id=run.id,
                        run_candidate_id=candidate.id,
                        test_case_id=str(case["id"]),
                        input_snapshot=deepcopy(case),
                        case_hash=str(case["case_hash"]),
                        prompt_snapshot=deepcopy(candidate.prompt_snapshot),
                        prompt_hash=candidate.prompt_hash,
                        model_snapshot=deepcopy(candidate.model_snapshot),
                        model_hash=candidate.model_hash,
                        generation_parameters_snapshot=deepcopy(
                            candidate.generation_parameters_snapshot
                        ),
                        rendered_system_prompt=rendered.system,
                        rendered_user_prompt=rendered.user,
                        metric_versions={},
                        metric_directions={},
                        metric_applicability={},
                        metric_results={},
                        provider_metadata={},
                        estimated_cost_micro_usd=None,
                        cost_source="not_incurred",
                        status=ResultStatus.QUEUED,
                    )
                    repository.add_result(result)
                    result_ids.append(result.id)
            session.flush()
            return result_ids

    async def _execute_result(self, result_id: str) -> None:
        async with self._generation_limit:
            provider_returned = False
            try:
                prepared = self._load_and_start_result(result_id)
                if prepared is None:
                    return
                request, provider_name = prepared
                if request.api_mode is AdapterApiMode.DEMO:
                    profile = _demo_profile(request.model)
                    response = await DeterministicAdapter(profile=profile).generate(request)
                else:
                    response = await self.adapters.generate(provider_name, request)
                provider_returned = True
                self._persist_generation_evidence(result_id, response)
                try:
                    self._score_and_complete_result(result_id)
                except Exception as exc:
                    self._logger.error(
                        "result_scoring_error",
                        result_id=result_id,
                        error_type=type(exc).__name__,
                    )
                    self._record_result_error(
                        result_id,
                        error_type="scoring_error",
                        retryable=False,
                        retry_count=None,
                        message=(
                            "The provider response was recorded, but evaluation scoring failed."
                        ),
                    )
            except ProviderError as exc:
                self._logger.warning(
                    "result_provider_error",
                    result_id=result_id,
                    error_code=exc.code,
                    retryable=exc.retryable,
                    attempts=exc.attempts,
                )
                self._record_result_error(
                    result_id,
                    error_type=exc.code,
                    retryable=exc.retryable,
                    retry_count=max(0, exc.attempts - 1),
                    cost_source=(
                        "billing_ambiguous"
                        if exc.code in {"provider_timeout", "provider_upstream", "provider_error"}
                        else "not_incurred"
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.error(
                    "result_evaluation_error",
                    result_id=result_id,
                    error_type=type(exc).__name__,
                )
                try:
                    self._record_result_error(
                        result_id,
                        error_type="evaluation_error",
                        retryable=False,
                        retry_count=None,
                        cost_source=("billing_ambiguous" if provider_returned else "not_incurred"),
                    )
                except Exception as finalizer_error:
                    self._logger.error(
                        "result_error_finalizer_failed",
                        result_id=result_id,
                        error_type=type(finalizer_error).__name__,
                    )
            finally:
                try:
                    self._refresh_progress_for_result(result_id)
                except Exception as exc:
                    self._logger.error(
                        "result_progress_refresh_failed",
                        result_id=result_id,
                        error_type=type(exc).__name__,
                    )

    def _load_and_start_result(self, result_id: str) -> tuple[GenerationRequest, str] | None:
        with session_scope(self.session_factory) as session:
            repository = EvaluationRunRepository(session)
            result = repository.get_result(result_id)
            run = repository.get(result.run_id)
            if run.status is RunStatus.CANCEL_REQUESTED:
                result.transition_to(ResultStatus.CANCELLED, reason="run cancellation requested")
                return None
            if run.status is not RunStatus.RUNNING:
                result.transition_to(ResultStatus.INTERRUPTED, reason="run is not executable")
                return None
            result.transition_to(ResultStatus.RUNNING, reason="provider generation started")
            parameters = result.generation_parameters_snapshot
            model_snapshot = result.model_snapshot
            adapter_mode = _adapter_api_mode(str(model_snapshot["api_mode"]))
            request = GenerationRequest(
                model=str(model_snapshot["model_name"]),
                api_mode=adapter_mode,
                system_prompt=result.rendered_system_prompt,
                user_prompt=result.rendered_user_prompt,
                temperature=float(parameters.get("temperature", 0.0)),
                max_output_tokens=min(
                    int(parameters.get("max_output_tokens", 512)),
                    self.settings.max_output_tokens,
                ),
                seed=int(parameters.get("seed", 0)),
                expected_output=_optional_text(result.input_snapshot.get("expected_output")),
                context=_optional_text(result.input_snapshot.get("context")),
                metadata={"run_id": result.run_id, "result_id": result.id},
            )
            return request, str(model_snapshot["provider"])

    def _persist_generation_evidence(self, result_id: str, response: Any) -> None:
        with session_scope(self.session_factory) as session:
            repository = EvaluationRunRepository(session)
            result = repository.get_result(result_id)
            if result.status is not ResultStatus.RUNNING:
                return
            raw_output = str(response.text)
            result.output_text = raw_output[:MAX_PERSISTED_OUTPUT_CHARS]
            result.provider = _bounded_evidence_text(str(response.provider), 100)
            result.model_name = _bounded_evidence_text(str(response.model), 200)
            result.api_mode = _model_api_mode(response.api_mode)
            result.request_id = _bounded_evidence_text(str(response.request_id), 255)
            result.finish_reason = (
                _bounded_evidence_text(str(response.finish_reason), 100)
                if response.finish_reason is not None
                else None
            )
            result.retry_count = min(max(0, int(response.retry_count)), MAX_DATABASE_INTEGER)
            result.latency_ms = min(max(0, int(response.latency_ms)), MAX_DATABASE_INTEGER)
            result.input_tokens = min(max(0, int(response.input_tokens)), MAX_DATABASE_INTEGER)
            result.output_tokens = min(max(0, int(response.output_tokens)), MAX_DATABASE_INTEGER)
            result.total_tokens = min(max(0, int(response.total_tokens)), MAX_DATABASE_INTEGER)
            cost, cost_source = _calculate_cost(
                result.model_snapshot,
                response,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            result.estimated_cost_micro_usd = cost
            result.cost_source = cost_source
            provider_metadata = dict(response.metadata)
            if len(raw_output) > MAX_PERSISTED_OUTPUT_CHARS:
                provider_metadata["output_truncated"] = True
                provider_metadata["original_output_characters"] = len(raw_output)
            result.provider_metadata = provider_metadata
            result.state_version += 1

    def _score_and_complete_result(self, result_id: str) -> None:
        with session_scope(self.session_factory) as session:
            repository = EvaluationRunRepository(session)
            result = repository.get_result(result_id)
            if result.status is not ResultStatus.RUNNING:
                return
            if result.output_text is None:
                raise ValueError("generation evidence is missing output_text")
            case = _evaluation_case(result.input_snapshot, result.output_text)
            configured = _configured_metrics(repository.get(result.run_id))
            metric_results = [
                _apply_configured_threshold(metric, configured.get(metric.name))
                for metric in self.metrics.evaluate(case)
                if not configured or metric.name in configured
            ]
            weights = {
                name: float(configuration.get("weight", 1.0))
                for name, configuration in configured.items()
            }
            aggregate = aggregate_metric_results(metric_results, weights=weights)
            serialized = {metric.name: _serialize_metric(metric) for metric in metric_results}
            serialized[aggregate.name] = _serialize_metric(aggregate)
            result.metric_versions = {
                metric.name: metric.version for metric in (*metric_results, aggregate)
            }
            result.metric_directions = {
                metric.name: metric.direction.value for metric in (*metric_results, aggregate)
            }
            result.metric_applicability = {
                metric.name: metric.status.value for metric in (*metric_results, aggregate)
            }
            result.metric_results = serialized
            result.aggregate_score = aggregate.score
            result.aggregate_passed = aggregate.passed
            result.effective_metric_weight = float(
                aggregate.evidence.get("effective_denominator", 0.0)
            )
            result.transition_to(ResultStatus.COMPLETED, reason="generation and scoring completed")

    def _record_result_error(
        self,
        result_id: str,
        *,
        error_type: str,
        retryable: bool,
        retry_count: int | None,
        message: str = "The evaluation item could not be completed.",
        cost_source: str | None = None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            result = EvaluationRunRepository(session).get_result(result_id)
            if result.status.is_terminal:
                return
            result.error_type = error_type[:100]
            result.error_message = message
            result.error_retryable = retryable
            if retry_count is not None:
                result.retry_count = retry_count
            if cost_source is not None:
                result.estimated_cost_micro_usd = None
                result.cost_source = cost_source
            result.transition_to(ResultStatus.ERROR, reason="provider or evaluator error")

    def _refresh_progress_for_result(self, result_id: str) -> None:
        with self.session_factory() as session:
            result = EvaluationRunRepository(session).get_result(result_id)
            run_id = result.run_id
        self._refresh_progress(run_id)

    def _refresh_progress(self, run_id: str) -> None:
        for attempt in range(5):
            try:
                self._refresh_progress_once(run_id)
                return
            except StaleDataError:
                if attempt == 4:
                    self._logger.error(
                        "progress_refresh_contention",
                        run_id=run_id,
                        attempts=attempt + 1,
                    )

    def _refresh_progress_once(self, run_id: str) -> None:
        with session_scope(self.session_factory) as session:
            repository = EvaluationRunRepository(session)
            run = repository.get(run_id, with_candidates=True)
            terminal = {status for status in ResultStatus if status.is_terminal}
            grouped_rows = session.execute(
                select(
                    EvaluationResult.run_candidate_id,
                    EvaluationResult.status,
                    func.count(EvaluationResult.id),
                )
                .where(EvaluationResult.run_id == run_id)
                .group_by(EvaluationResult.run_candidate_id, EvaluationResult.status)
            ).all()
            counts: dict[str, dict[ResultStatus, int]] = {}
            for candidate_id, result_status, count in grouped_rows:
                counts.setdefault(str(candidate_id), {})[ResultStatus(result_status)] = int(count)
            completed_items = sum(
                count
                for candidate_counts in counts.values()
                for result_status, count in candidate_counts.items()
                if result_status in terminal
            )
            succeeded_items = sum(
                candidate_counts.get(ResultStatus.COMPLETED, 0)
                for candidate_counts in counts.values()
            )
            failed_items = sum(
                candidate_counts.get(ResultStatus.ERROR, 0) for candidate_counts in counts.values()
            )
            if (run.completed_items, run.succeeded_items, run.failed_items) != (
                completed_items,
                succeeded_items,
                failed_items,
            ):
                run.completed_items = completed_items
                run.succeeded_items = succeeded_items
                run.failed_items = failed_items
                run.state_version += 1
            for candidate in run.candidates:
                candidate_counts = counts.get(candidate.id, {})
                candidate_completed = sum(
                    count
                    for result_status, count in candidate_counts.items()
                    if result_status in terminal
                )
                candidate_failed = candidate_counts.get(ResultStatus.ERROR, 0)
                if (candidate.completed_items, candidate.failed_items) != (
                    candidate_completed,
                    candidate_failed,
                ):
                    candidate.completed_items = candidate_completed
                    candidate.failed_items = candidate_failed
                    candidate.state_version += 1
                if (
                    candidate.status in {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}
                    and candidate.completed_items == candidate.total_items
                ):
                    if run.status is RunStatus.CANCEL_REQUESTED:
                        target = RunStatus.CANCELLED
                    else:
                        target = (
                            RunStatus.COMPLETED_WITH_ERRORS
                            if candidate.failed_items
                            else RunStatus.COMPLETED
                        )
                    candidate.transition_to(target, reason="candidate matrix completed")

            if run.completed_items != run.total_items or run.status.is_terminal:
                session.flush()
                return
            if run.status is RunStatus.CANCEL_REQUESTED:
                run.transition_to(RunStatus.CANCELLED, reason="cancellation completed")
            elif run.failed_items:
                run.transition_to(
                    RunStatus.COMPLETED_WITH_ERRORS,
                    reason="run completed with one or more item errors",
                )
            else:
                run.transition_to(RunStatus.COMPLETED, reason="run completed")
            session.flush()

    def _fail_run_setup(self, run_id: str, error: Exception) -> None:
        with session_scope(self.session_factory) as session:
            repository = EvaluationRunRepository(session)
            try:
                run = repository.get(run_id, with_candidates=True)
            except RepositoryNotFoundError:
                return
            if run.status is not RunStatus.QUEUED:
                return
            run.transition_to(RunStatus.FAILED, reason="run setup failed")
            run.error_type = type(error).__name__[:100]
            run.error_message = "The evaluation run could not be prepared."
            for candidate in run.candidates:
                if not candidate.status.is_terminal:
                    candidate.transition_to(RunStatus.FAILED, reason="run setup failed")
                    candidate.error_type = type(error).__name__[:100]
                    candidate.error_message = "The evaluation candidate could not be prepared."
            session.flush()

    def _validate_real_model(self, model: ModelProfile) -> None:
        if model.provider == "openai":
            configured = self.settings.openai_api_key is not None
            allowed = self.settings.openai_model_allowlist
        else:
            configured = (
                self.settings.compatible_api_key is not None
                or self.settings.compatible_auth_mode == "none"
            )
            allowed = self.settings.compatible_model_allowlist
        if not configured:
            raise CapabilityError(f"Provider credentials are not configured for {model.name}.")
        if model.model_name not in allowed:
            raise CapabilityError(f"Model {model.model_name} is not in the server allowlist.")

    def _validate_metric_configuration(self, configurations: list[MetricConfiguration]) -> None:
        enabled = [configuration for configuration in configurations if configuration.enabled]
        if not enabled:
            raise CapabilityError("At least one evaluation metric must be enabled.")
        names = [configuration.name for configuration in enabled]
        if len(names) != len(set(names)):
            raise CapabilityError("Each evaluation metric may be configured only once.")
        if "aggregate_quality" in names:
            raise CapabilityError("aggregate_quality is derived and cannot be configured directly.")
        expected_directions = {
            name: (
                MetricDirection.LOWER_IS_BETTER
                if name == "hallucination_risk"
                else MetricDirection.HIGHER_IS_BETTER
            )
            for name in DEFAULT_METRIC_WEIGHTS
        }
        for configuration in enabled:
            expected_version = self.metrics.versions.get(configuration.name)
            if expected_version is None:
                raise CapabilityError(f"Metric {configuration.name} is not registered.")
            if configuration.version != expected_version:
                raise CapabilityError(
                    f"Metric {configuration.name} must use version {expected_version}."
                )
            if configuration.direction is not expected_directions[configuration.name]:
                raise CapabilityError(
                    f"Metric {configuration.name} has an invalid comparison direction."
                )
        if not any(configuration.weight > 0 for configuration in enabled):
            raise CapabilityError("At least one enabled metric must have a positive weight.")


def _configured_metrics(run: EvaluationRun) -> dict[str, dict[str, Any]]:
    rows = run.metric_configuration_snapshot.get("metrics", [])
    return {str(row["name"]): dict(row) for row in rows}


def _apply_configured_threshold(
    metric: EvaluationMetricResult,
    configuration: Mapping[str, Any] | None,
) -> EvaluationMetricResult:
    if configuration is None or metric.score is None:
        return metric
    threshold = float(configuration.get("threshold", metric.threshold or 0.0))
    passed = (
        metric.score <= threshold
        if metric.direction is EvaluationMetricDirection.LOWER_IS_BETTER
        else metric.score >= threshold
    )
    return replace(metric, threshold=threshold, passed=passed)


def _evaluation_case(snapshot: Mapping[str, Any], output: str) -> EvaluationCase:
    constraints_data = dict(snapshot.get("constraints") or {})
    allowed_constraint_keys = {
        "min_words",
        "max_words",
        "min_sentences",
        "max_sentences",
        "required_prefix",
        "required_suffix",
        "forbidden_phrases",
    }
    output_constraints = {
        key: value for key, value in constraints_data.items() if key in allowed_constraint_keys
    }
    if "forbidden_phrases" in output_constraints:
        output_constraints["forbidden_phrases"] = tuple(output_constraints["forbidden_phrases"])
    metadata = dict(snapshot.get("metadata") or {})
    json_schema = constraints_data.get("json_schema")
    raw_context_chunks = snapshot.get("context_chunks")
    context_chunks = (
        tuple(str(chunk) for chunk in raw_context_chunks if str(chunk).strip())
        if isinstance(raw_context_chunks, list)
        else ()
    )
    return EvaluationCase(
        input_text=str(snapshot["input"]),
        output=output,
        reference=_optional_text(snapshot.get("expected_output")),
        context=context_chunks or _optional_text(snapshot.get("context")),
        relevance_keywords=tuple(metadata.get("relevance_keywords", ())),
        required_phrases=tuple(snapshot.get("required_phrases") or ()),
        expects_json=json_schema is not None or bool(constraints_data.get("expects_json", False)),
        json_schema=json_schema if isinstance(json_schema, Mapping) else None,
        constraints=OutputConstraints(**output_constraints),
    )


def _serialize_metric(metric: EvaluationMetricResult) -> dict[str, Any]:
    payload = asdict(metric)
    payload["status"] = metric.status.value
    payload["direction"] = metric.direction.value
    payload["evidence"] = dict(metric.evidence)
    return payload


def _adapter_api_mode(value: str) -> AdapterApiMode:
    if value == ApiMode.DETERMINISTIC.value:
        return AdapterApiMode.DEMO
    if value == ApiMode.RESPONSES.value:
        return AdapterApiMode.RESPONSES
    return AdapterApiMode.CHAT_COMPLETIONS


def _model_api_mode(value: AdapterApiMode) -> ApiMode:
    if value is AdapterApiMode.DEMO:
        return ApiMode.DETERMINISTIC
    if value is AdapterApiMode.RESPONSES:
        return ApiMode.RESPONSES
    return ApiMode.CHAT_COMPLETIONS


def _demo_profile(model_name: str) -> str:
    return resolve_demo_profile(model_name)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _calculate_cost(
    model_snapshot: Mapping[str, Any],
    response: Any,
    *,
    input_tokens: int,
    output_tokens: int,
) -> tuple[int | None, str]:
    metadata = dict(response.metadata)
    if metadata.get("synthetic"):
        return 0, "synthetic"
    if not bool(metadata.get("usage_reported")):
        return None, "usage_unavailable"
    pricing_known = bool(model_snapshot.get("pricing_known", False)) or bool(
        model_snapshot.get("metadata", {}).get("pricing_known", False)
    )
    if not pricing_known:
        return None, "pricing_unavailable"
    input_price_value = model_snapshot.get("input_price_micro_usd_per_million_tokens")
    output_price_value = model_snapshot.get("output_price_micro_usd_per_million_tokens")
    if input_price_value is None or output_price_value is None:
        return None, "pricing_unavailable"
    input_price = int(input_price_value)
    output_price = int(output_price_value)
    raw_cost = ((input_tokens * input_price) + (output_tokens * output_price)) / 1_000_000
    cost = math.ceil(raw_cost) if raw_cost > 0 else 0
    return cost, "reported_usage"


def _bounded_evidence_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[: max_length - 17]}_{digest}"
