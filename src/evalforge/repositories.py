"""Transaction-scoped repository operations.

Repositories flush but never commit. The caller owns the request/worker
transaction through :func:`evalforge.database.session_scope` or an equivalent
explicit transaction boundary.
"""

from __future__ import annotations

from builtins import list as list_type
from copy import deepcopy
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, selectinload

from evalforge.models import (
    Dataset,
    EvaluationResult,
    EvaluationRun,
    ModelProfile,
    PromptTemplate,
    ResultStatus,
    RunCandidate,
    RunStatus,
    TestCase,
    canonical_json_hash,
    utc_now,
)
from evalforge.schemas import (
    DatasetCreate,
    DatasetUpdate,
    EvaluationRunCreate,
    ModelProfileCreate,
    ModelProfileUpdate,
    PromptTemplateCreate,
    PromptTemplateUpdate,
    TestCaseCreate,
    TestCaseUpdate,
    extract_template_variables,
)

ModelT = TypeVar("ModelT")


class RepositoryError(RuntimeError):
    """Base class for safe persistence failures."""


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


class ValidationError(RepositoryError):
    pass


def _flush(session: Session, resource: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"{resource} conflicts with existing or referenced data") from exc


def _case_payload(data: TestCaseCreate | TestCase) -> dict[str, Any]:
    if isinstance(data, TestCase):
        return {
            "external_id": data.external_id,
            "position": data.position,
            "input": data.input_text,
            "context": data.context_text,
            "context_chunks": list(data.context_chunks),
            "expected_output": data.expected_output,
            "required_phrases": list(data.required_phrases),
            "constraints": dict(data.constraints_json),
            "tags": list(data.tags),
            "metadata": dict(data.metadata_json),
        }
    return {
        "external_id": data.external_id,
        "position": data.position,
        "input": data.input_text,
        "context": data.context_text,
        "context_chunks": list(data.context_chunks),
        "expected_output": data.expected_output,
        "required_phrases": list(data.required_phrases),
        "constraints": dict(data.constraints_json),
        "tags": list(data.tags),
        "metadata": dict(data.metadata_json),
    }


def _prompt_payload(prompt: PromptTemplate) -> dict[str, Any]:
    return {
        "name": prompt.name,
        "version": prompt.version,
        "system_template": prompt.system_template,
        "user_template": prompt.user_template,
        "variables": list(prompt.variables),
    }


def _model_payload(model: ModelProfile) -> dict[str, Any]:
    return {
        "name": model.name,
        "version": model.version,
        "provider": model.provider,
        "model_name": model.model_name,
        "api_mode": model.api_mode.value,
        "generation_parameters": dict(model.generation_parameters),
        "input_price_micro_usd_per_million_tokens": (
            model.input_price_micro_usd_per_million_tokens
        ),
        "output_price_micro_usd_per_million_tokens": (
            model.output_price_micro_usd_per_million_tokens
        ),
        "pricing_source": model.pricing_source,
    }


def _dataset_snapshot(dataset: Dataset) -> dict[str, Any]:
    ordered_cases = sorted(dataset.cases, key=lambda case: case.position)
    return {
        "id": dataset.id,
        "name": dataset.name,
        "version": dataset.version,
        "description": dataset.description,
        "content_hash": dataset.content_hash,
        "metadata": dict(dataset.metadata_json),
        "cases": [case.snapshot() for case in ordered_cases],
    }


class BaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session


class DatasetRepository(BaseRepository):
    def create(self, data: DatasetCreate) -> Dataset:
        cases = [
            TestCase(
                external_id=case.external_id,
                position=case.position,
                input_text=case.input_text,
                context_text=case.context_text,
                context_chunks=list(case.context_chunks),
                expected_output=case.expected_output,
                required_phrases=list(case.required_phrases),
                constraints_json=deepcopy(case.constraints_json),
                tags=list(case.tags),
                metadata_json=deepcopy(case.metadata_json),
                case_hash=canonical_json_hash(_case_payload(case)),
            )
            for case in data.cases
        ]
        content_payload = {
            "name": data.name,
            "version": data.version,
            "description": data.description,
            "metadata": data.metadata_json,
            "cases": [
                {**_case_payload(case), "case_hash": canonical_json_hash(_case_payload(case))}
                for case in data.cases
            ],
        }
        dataset = Dataset(
            name=data.name,
            description=data.description,
            version=data.version,
            content_hash=canonical_json_hash(content_payload),
            metadata_json=deepcopy(data.metadata_json),
            cases=cases,
        )
        self.session.add(dataset)
        _flush(self.session, "dataset")
        return dataset

    def get(self, dataset_id: str, *, with_cases: bool = False) -> Dataset:
        statement = select(Dataset).where(Dataset.id == dataset_id)
        if with_cases:
            statement = statement.options(selectinload(Dataset.cases))
        dataset = self.session.scalar(statement)
        if dataset is None:
            raise NotFoundError(f"dataset {dataset_id} was not found")
        return dataset

    def list(self, *, page: int = 1, limit: int = 50) -> tuple[list[Dataset], int]:
        offset = (page - 1) * limit
        total = self.session.scalar(select(func.count()).select_from(Dataset)) or 0
        rows = self.session.scalars(
            select(Dataset).order_by(Dataset.created_at.desc()).offset(offset).limit(limit)
        ).all()
        return list(rows), total

    def update(self, dataset_id: str, data: DatasetUpdate) -> Dataset:
        self._ensure_mutable(dataset_id)
        dataset = self.get(dataset_id, with_cases=True)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(dataset, field, deepcopy(value))
        self._refresh_hash(dataset)
        _flush(self.session, "dataset")
        return dataset

    def add_case(self, dataset_id: str, data: TestCaseCreate) -> TestCase:
        self._ensure_mutable(dataset_id)
        dataset = self.get(dataset_id, with_cases=True)
        case = TestCase(
            dataset=dataset,
            external_id=data.external_id,
            position=data.position,
            input_text=data.input_text,
            context_text=data.context_text,
            context_chunks=list(data.context_chunks),
            expected_output=data.expected_output,
            required_phrases=list(data.required_phrases),
            constraints_json=deepcopy(data.constraints_json),
            tags=list(data.tags),
            metadata_json=deepcopy(data.metadata_json),
            case_hash=canonical_json_hash(_case_payload(data)),
        )
        self.session.add(case)
        _flush(self.session, "test case")
        self._refresh_hash(dataset)
        _flush(self.session, "dataset")
        return case

    def get_case(self, case_id: str) -> TestCase:
        case = self.session.get(TestCase, case_id)
        if case is None:
            raise NotFoundError(f"test case {case_id} was not found")
        return case

    def update_case(self, case_id: str, data: TestCaseUpdate) -> TestCase:
        case = self.get_case(case_id)
        self._ensure_mutable(case.dataset_id)
        changes = data.model_dump(exclude_unset=True)
        if "context_chunks" in changes:
            chunks = list(changes["context_chunks"] or [])
            changes["context_chunks"] = chunks
            changes["context_text"] = "\n\n".join(chunks) or None
        elif "context_text" in changes:
            context_text = changes["context_text"]
            changes["context_chunks"] = [context_text] if context_text else []
        for field, value in changes.items():
            setattr(case, field, deepcopy(value))
        case.case_hash = canonical_json_hash(_case_payload(case))
        dataset = self.get(case.dataset_id, with_cases=True)
        self._refresh_hash(dataset)
        _flush(self.session, "test case")
        return case

    def delete_case(self, case_id: str) -> None:
        case = self.get_case(case_id)
        self._ensure_mutable(case.dataset_id)
        if self.session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.test_case_id == case_id)
        ):
            raise ConflictError("test case is referenced by evaluation provenance")
        dataset = self.get(case.dataset_id, with_cases=True)
        self.session.delete(case)
        _flush(self.session, "test case")
        self.session.expire(dataset, ["cases"])
        dataset = self.get(dataset.id, with_cases=True)
        self._refresh_hash(dataset)
        _flush(self.session, "dataset")

    def delete(self, dataset_id: str) -> None:
        dataset = self.get(dataset_id)
        if self.session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(EvaluationRun.dataset_id == dataset_id)
        ):
            raise ConflictError("dataset is referenced by immutable evaluation runs")
        self.session.delete(dataset)
        _flush(self.session, "dataset")

    @staticmethod
    def _refresh_hash(dataset: Dataset) -> None:
        dataset.content_hash = canonical_json_hash(
            {
                "name": dataset.name,
                "version": dataset.version,
                "description": dataset.description,
                "metadata": dict(dataset.metadata_json),
                "cases": [
                    {**_case_payload(case), "case_hash": case.case_hash}
                    for case in sorted(dataset.cases, key=lambda item: item.position)
                ],
            }
        )

    def _ensure_mutable(self, dataset_id: str) -> None:
        if self.session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(EvaluationRun.dataset_id == dataset_id)
        ):
            raise ConflictError(
                "dataset version is referenced by an evaluation run; create a new version"
            )


class PromptTemplateRepository(BaseRepository):
    def create(self, data: PromptTemplateCreate) -> PromptTemplate:
        variables = sorted(
            extract_template_variables(data.system_template)
            | extract_template_variables(data.user_template)
        )
        prompt = PromptTemplate(
            name=data.name,
            description=data.description,
            version=data.version,
            system_template=data.system_template,
            user_template=data.user_template,
            variables=variables,
            template_hash="",
            metadata_json=deepcopy(data.metadata_json),
        )
        prompt.template_hash = canonical_json_hash(_prompt_payload(prompt))
        self.session.add(prompt)
        _flush(self.session, "prompt template")
        return prompt

    def get(self, prompt_id: str) -> PromptTemplate:
        prompt = self.session.get(PromptTemplate, prompt_id)
        if prompt is None:
            raise NotFoundError(f"prompt template {prompt_id} was not found")
        return prompt

    def list(self, *, page: int = 1, limit: int = 50) -> tuple[list[PromptTemplate], int]:
        offset = (page - 1) * limit
        total = self.session.scalar(select(func.count()).select_from(PromptTemplate)) or 0
        rows = self.session.scalars(
            select(PromptTemplate)
            .order_by(PromptTemplate.name, PromptTemplate.version.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), total

    def update(self, prompt_id: str, data: PromptTemplateUpdate) -> PromptTemplate:
        if self.session.scalar(
            select(func.count())
            .select_from(RunCandidate)
            .where(RunCandidate.prompt_template_id == prompt_id)
        ):
            raise ConflictError(
                "prompt version is referenced by an evaluation run; create a new version"
            )
        prompt = self.get(prompt_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(prompt, field, deepcopy(value))
        prompt.variables = sorted(
            extract_template_variables(prompt.system_template)
            | extract_template_variables(prompt.user_template)
        )
        prompt.template_hash = canonical_json_hash(_prompt_payload(prompt))
        _flush(self.session, "prompt template")
        return prompt

    def delete(self, prompt_id: str) -> None:
        prompt = self.get(prompt_id)
        if self.session.scalar(
            select(func.count())
            .select_from(RunCandidate)
            .where(RunCandidate.prompt_template_id == prompt_id)
        ):
            raise ConflictError("prompt template is referenced by immutable run candidates")
        self.session.delete(prompt)
        _flush(self.session, "prompt template")


class ModelProfileRepository(BaseRepository):
    def create(self, data: ModelProfileCreate) -> ModelProfile:
        profile = ModelProfile(
            name=data.name,
            description=data.description,
            version=data.version,
            provider=data.provider,
            model_name=data.model_name,
            api_mode=data.api_mode,
            generation_parameters=deepcopy(data.generation_parameters),
            input_price_micro_usd_per_million_tokens=(
                data.input_price_micro_usd_per_million_tokens
            ),
            output_price_micro_usd_per_million_tokens=(
                data.output_price_micro_usd_per_million_tokens
            ),
            pricing_source=data.pricing_source,
            profile_hash="",
            enabled=data.enabled,
            metadata_json=deepcopy(data.metadata_json),
        )
        profile.profile_hash = canonical_json_hash(_model_payload(profile))
        self.session.add(profile)
        _flush(self.session, "model profile")
        return profile

    def get(self, profile_id: str, *, require_enabled: bool = False) -> ModelProfile:
        profile = self.session.get(ModelProfile, profile_id)
        if profile is None:
            raise NotFoundError(f"model profile {profile_id} was not found")
        if require_enabled and not profile.enabled:
            raise ValidationError(f"model profile {profile_id} is disabled")
        return profile

    def list(self, *, page: int = 1, limit: int = 50) -> tuple[list[ModelProfile], int]:
        offset = (page - 1) * limit
        total = self.session.scalar(select(func.count()).select_from(ModelProfile)) or 0
        rows = self.session.scalars(
            select(ModelProfile)
            .order_by(ModelProfile.name, ModelProfile.version.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), total

    def update(self, profile_id: str, data: ModelProfileUpdate) -> ModelProfile:
        profile = self.get(profile_id)
        updates = data.model_dump(exclude_unset=True)
        if set(updates) - {"enabled"} and self.session.scalar(
            select(func.count())
            .select_from(RunCandidate)
            .where(RunCandidate.model_profile_id == profile_id)
        ):
            raise ConflictError(
                "model version is referenced by an evaluation run; create a new version"
            )
        for field, value in updates.items():
            setattr(profile, field, deepcopy(value))
        prices = (
            profile.input_price_micro_usd_per_million_tokens,
            profile.output_price_micro_usd_per_million_tokens,
        )
        if any(price is not None for price in prices) != (profile.pricing_source is not None):
            raise ValidationError("known pricing and pricing_source must be configured together")
        profile.profile_hash = canonical_json_hash(_model_payload(profile))
        _flush(self.session, "model profile")
        return profile

    def delete(self, profile_id: str) -> None:
        profile = self.get(profile_id)
        if self.session.scalar(
            select(func.count())
            .select_from(RunCandidate)
            .where(RunCandidate.model_profile_id == profile_id)
        ):
            raise ConflictError("model profile is referenced by immutable run candidates")
        self.session.delete(profile)
        _flush(self.session, "model profile")


class EvaluationRunRepository(BaseRepository):
    def find_by_idempotency_key(self, key: str) -> EvaluationRun | None:
        return self.session.scalar(
            select(EvaluationRun).where(EvaluationRun.idempotency_key == key)
        )

    def create(
        self,
        data: EvaluationRunCreate,
        *,
        application_version: str,
        executor_type: str = "in_process",
        dataset: Dataset | None = None,
        prompts_by_id: dict[str, PromptTemplate] | None = None,
        models_by_id: dict[str, ModelProfile] | None = None,
        preflight_snapshot: dict[str, Any] | None = None,
    ) -> EvaluationRun:
        request_payload = data.model_dump(mode="json", exclude={"idempotency_key"})
        request_hash = canonical_json_hash(request_payload)
        if data.idempotency_key is not None:
            existing = self.find_by_idempotency_key(data.idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ConflictError("idempotency key was already used for a different request")
                return existing

        if dataset is None:
            dataset = DatasetRepository(self.session).get(str(data.dataset_id), with_cases=True)
        if not dataset.cases:
            raise ValidationError("an evaluation run requires at least one test case")

        if prompts_by_id is None:
            prompts_by_id = {
                prompt.id: prompt
                for prompt in self.session.scalars(
                    select(PromptTemplate).where(
                        PromptTemplate.id.in_([str(item) for item in data.prompt_ids])
                    )
                )
            }
        if models_by_id is None:
            models_by_id = {
                profile.id: profile
                for profile in self.session.scalars(
                    select(ModelProfile).where(
                        ModelProfile.id.in_([str(item) for item in data.model_ids])
                    )
                )
            }
        missing_prompts = [str(item) for item in data.prompt_ids if str(item) not in prompts_by_id]
        missing_models = [str(item) for item in data.model_ids if str(item) not in models_by_id]
        if missing_prompts or missing_models:
            raise NotFoundError("one or more prompt or model candidates were not found")
        disabled_models = [
            str(item) for item in data.model_ids if not models_by_id[str(item)].enabled
        ]
        if disabled_models:
            raise ValidationError("one or more model candidates are disabled")
        contains_real_provider = any(
            models_by_id[str(item)].api_mode.value != "deterministic" for item in data.model_ids
        )
        if contains_real_provider and not data.acknowledge_real_cost:
            raise ValidationError("real-provider candidates require acknowledge_real_cost=true")
        contains_unknown_pricing = any(
            models_by_id[str(item)].api_mode.value != "deterministic"
            and (
                models_by_id[str(item)].input_price_micro_usd_per_million_tokens is None
                or models_by_id[str(item)].output_price_micro_usd_per_million_tokens is None
            )
            for item in data.model_ids
        )
        if contains_unknown_pricing and not data.acknowledge_unknown_cost:
            raise ValidationError("unknown-price candidates require acknowledge_unknown_cost=true")

        metric_rows = [metric.model_dump(mode="json") for metric in data.metrics if metric.enabled]
        metric_snapshot = {
            "metrics": metric_rows,
            "versions": {metric["name"]: metric["version"] for metric in metric_rows},
            "directions": {metric["name"]: metric["direction"] for metric in metric_rows},
            "configuration_hash": canonical_json_hash(metric_rows),
        }
        run = EvaluationRun(
            name=data.name,
            dataset=dataset,
            dataset_snapshot=deepcopy(_dataset_snapshot(dataset)),
            dataset_hash=dataset.content_hash,
            metric_configuration_snapshot=metric_snapshot,
            preflight_snapshot=deepcopy(preflight_snapshot or {}),
            application_version=application_version,
            executor_type=executor_type,
            requested_by=data.requested_by,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            acknowledge_real_cost=data.acknowledge_real_cost,
            acknowledge_unknown_cost=data.acknowledge_unknown_cost,
            status=RunStatus.QUEUED,
            total_items=len(dataset.cases) * len(data.prompt_ids) * len(data.model_ids),
        )
        self.session.add(run)
        _flush(self.session, "evaluation run")

        ordinal = 0
        for prompt_id in data.prompt_ids:
            prompt = prompts_by_id[str(prompt_id)]
            for model_id in data.model_ids:
                model = models_by_id[str(model_id)]
                prompt_snapshot = prompt.snapshot()
                model_snapshot = model.snapshot()
                generation_parameters = deepcopy(model.generation_parameters)
                candidate_payload = {
                    "prompt": prompt_snapshot,
                    "model": model_snapshot,
                    "generation_parameters": generation_parameters,
                }
                candidate = RunCandidate(
                    run=run,
                    prompt_template=prompt,
                    model_profile=model,
                    ordinal=ordinal,
                    label=f"{prompt.name} v{prompt.version} / {model.name} v{model.version}",
                    prompt_snapshot=deepcopy(prompt_snapshot),
                    prompt_hash=prompt.template_hash,
                    model_snapshot=deepcopy(model_snapshot),
                    model_hash=model.profile_hash,
                    generation_parameters_snapshot=generation_parameters,
                    candidate_hash=canonical_json_hash(candidate_payload),
                    status=RunStatus.QUEUED,
                    total_items=len(dataset.cases),
                )
                self.session.add(candidate)
                ordinal += 1
        _flush(self.session, "run candidates")
        return run

    def get(
        self,
        run_id: str,
        *,
        with_detail: bool = False,
        with_candidates: bool = False,
    ) -> EvaluationRun:
        statement = select(EvaluationRun).where(EvaluationRun.id == run_id)
        if with_detail:
            statement = statement.options(
                selectinload(EvaluationRun.candidates),
                selectinload(EvaluationRun.results),
            )
        elif with_candidates:
            statement = statement.options(
                selectinload(EvaluationRun.candidates),
                defer(EvaluationRun.dataset_snapshot),
                defer(EvaluationRun.metric_configuration_snapshot),
            )
        run = self.session.scalar(statement)
        if run is None:
            raise NotFoundError(f"evaluation run {run_id} was not found")
        return run

    def list(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        status: RunStatus | None = None,
    ) -> tuple[list[EvaluationRun], int]:
        filters = () if status is None else (EvaluationRun.status == status,)
        offset = (page - 1) * limit
        total = (
            self.session.scalar(select(func.count()).select_from(EvaluationRun).where(*filters))
            or 0
        )
        rows = self.session.scalars(
            select(EvaluationRun)
            .where(*filters)
            .options(
                defer(EvaluationRun.dataset_snapshot),
                defer(EvaluationRun.metric_configuration_snapshot),
            )
            .order_by(EvaluationRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), total

    def list_results(
        self, run_id: str, *, page: int = 1, limit: int = 100
    ) -> tuple[list_type[EvaluationResult], int]:
        if self.session.get(EvaluationRun, run_id) is None:
            raise NotFoundError(f"evaluation run {run_id} was not found")
        filters = (EvaluationResult.run_id == run_id,)
        total = (
            self.session.scalar(select(func.count()).select_from(EvaluationResult).where(*filters))
            or 0
        )
        rows = self.session.scalars(
            select(EvaluationResult)
            .where(*filters)
            .order_by(EvaluationResult.created_at, EvaluationResult.id)
            .offset((page - 1) * limit)
            .limit(limit)
        ).all()
        return list(rows), total

    def get_candidate(self, candidate_id: str) -> RunCandidate:
        candidate = self.session.get(RunCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"run candidate {candidate_id} was not found")
        return candidate

    def add_result(self, result: EvaluationResult) -> EvaluationResult:
        candidate = self.get_candidate(result.run_candidate_id)
        if candidate.run_id != result.run_id:
            raise ValidationError("result run_id does not match its candidate")
        case = self.session.get(TestCase, result.test_case_id)
        if case is None:
            raise NotFoundError(f"test case {result.test_case_id} was not found")
        self.session.add(result)
        _flush(self.session, "evaluation result")
        return result

    def get_result(self, result_id: str) -> EvaluationResult:
        result = self.session.get(EvaluationResult, result_id)
        if result is None:
            raise NotFoundError(f"evaluation result {result_id} was not found")
        return result

    def transition_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        reason: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        at: datetime | None = None,
    ) -> EvaluationRun:
        run = self.get(run_id)
        run.transition_to(status, reason=reason, at=at)
        run.error_type = error_type
        run.error_message = error_message
        _flush(self.session, "evaluation run")
        return run

    def transition_candidate(
        self,
        candidate_id: str,
        status: RunStatus,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> RunCandidate:
        candidate = self.get_candidate(candidate_id)
        candidate.transition_to(status, reason=reason, at=at)
        _flush(self.session, "run candidate")
        return candidate

    def transition_result(
        self,
        result_id: str,
        status: ResultStatus,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> EvaluationResult:
        result = self.get_result(result_id)
        result.transition_to(status, reason=reason, at=at)
        _flush(self.session, "evaluation result")
        return result

    def recover_abandoned(self, *, reason: str = "application restarted") -> int:
        """Interrupt abandoned active work while leaving queued work resumable."""

        changed_at = utc_now()
        count = 0
        runs = self.session.scalars(
            select(EvaluationRun).where(
                EvaluationRun.status.in_([RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED])
            )
        ).all()
        for run in runs:
            run.transition_to(RunStatus.INTERRUPTED, reason=reason, at=changed_at)
            run.error_type = "interrupted"
            run.error_message = reason
            count += 1
        abandoned_run_ids = [run.id for run in runs]

        candidates = (
            self.session.scalars(
                select(RunCandidate).where(
                    RunCandidate.run_id.in_(abandoned_run_ids),
                    RunCandidate.status.in_(
                        [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED]
                    ),
                )
            ).all()
            if abandoned_run_ids
            else []
        )
        for candidate in candidates:
            candidate.transition_to(RunStatus.INTERRUPTED, reason=reason, at=changed_at)

        results = (
            self.session.scalars(
                select(EvaluationResult).where(
                    EvaluationResult.run_id.in_(abandoned_run_ids),
                    EvaluationResult.status.in_([ResultStatus.RUNNING, ResultStatus.QUEUED]),
                )
            ).all()
            if abandoned_run_ids
            else []
        )
        for result in results:
            was_running = result.status is ResultStatus.RUNNING
            generation_recorded = result.provider is not None or result.output_text is not None
            result.transition_to(ResultStatus.INTERRUPTED, reason=reason, at=changed_at)
            result.error_type = "interrupted"
            result.error_message = reason
            result.error_retryable = not generation_recorded
            if not generation_recorded:
                result.estimated_cost_micro_usd = None
                result.cost_source = "billing_ambiguous" if was_running else "not_incurred"

        self.session.flush()
        if abandoned_run_ids:
            terminal = {status for status in ResultStatus if status.is_terminal}
            grouped = self.session.execute(
                select(
                    EvaluationResult.run_id,
                    EvaluationResult.run_candidate_id,
                    EvaluationResult.status,
                    func.count(EvaluationResult.id),
                )
                .where(EvaluationResult.run_id.in_(abandoned_run_ids))
                .group_by(
                    EvaluationResult.run_id,
                    EvaluationResult.run_candidate_id,
                    EvaluationResult.status,
                )
            ).all()
            counts: dict[tuple[str, str], dict[ResultStatus, int]] = {}
            for run_id, candidate_id, result_status, result_count in grouped:
                counts.setdefault((str(run_id), str(candidate_id)), {})[
                    ResultStatus(result_status)
                ] = int(result_count)
            all_candidates = self.session.scalars(
                select(RunCandidate).where(RunCandidate.run_id.in_(abandoned_run_ids))
            ).all()
            for candidate in all_candidates:
                candidate_counts = counts.get((candidate.run_id, candidate.id), {})
                completed = sum(
                    value
                    for result_status, value in candidate_counts.items()
                    if result_status in terminal
                )
                failed = candidate_counts.get(ResultStatus.ERROR, 0)
                if (candidate.completed_items, candidate.failed_items) != (completed, failed):
                    candidate.completed_items = completed
                    candidate.failed_items = failed
                    candidate.state_version += 1
            for run in runs:
                run_counts = [
                    candidate_counts
                    for (counted_run_id, _candidate_id), candidate_counts in counts.items()
                    if counted_run_id == run.id
                ]
                completed = sum(
                    value
                    for candidate_counts in run_counts
                    for result_status, value in candidate_counts.items()
                    if result_status in terminal
                )
                succeeded = sum(
                    candidate_counts.get(ResultStatus.COMPLETED, 0)
                    for candidate_counts in run_counts
                )
                failed = sum(
                    candidate_counts.get(ResultStatus.ERROR, 0) for candidate_counts in run_counts
                )
                if (run.completed_items, run.succeeded_items, run.failed_items) != (
                    completed,
                    succeeded,
                    failed,
                ):
                    run.completed_items = completed
                    run.succeeded_items = succeeded
                    run.failed_items = failed
                    run.state_version += 1

        _flush(self.session, "abandoned evaluation work")
        return count


class Repositories:
    """Convenience bundle sharing one caller-owned Session."""

    def __init__(self, session: Session) -> None:
        self.datasets = DatasetRepository(session)
        self.prompts = PromptTemplateRepository(session)
        self.models = ModelProfileRepository(session)
        self.runs = EvaluationRunRepository(session)
