"""Typed SQLAlchemy domain models for immutable LLM evaluation provenance."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, ClassVar, Final

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy import (
    Enum as SQLAlchemyEnum,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evalforge.database import Base

JSONDict = dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


def canonical_json_hash(value: object) -> str:
    """Hash JSON-compatible data using stable UTF-8 bytes."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class ApiMode(StrEnum):
    DETERMINISTIC = "deterministic"
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ERRORS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }


class ResultStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ResultStatus.COMPLETED,
            ResultStatus.ERROR,
            ResultStatus.CANCELLED,
            ResultStatus.INTERRUPTED,
        }


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class MetricApplicability(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class InvalidStateTransition(ValueError):
    """Raised when an evaluator attempts an invalid lifecycle transition."""


class ImmutableProvenanceError(ValueError):
    """Raised when persisted snapshot provenance is changed."""


RUN_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.QUEUED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCEL_REQUESTED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.CANCEL_REQUESTED,
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ERRORS,
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.CANCEL_REQUESTED: frozenset(
        {
            RunStatus.CANCELLED,
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ERRORS,
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.COMPLETED_WITH_ERRORS: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.INTERRUPTED: frozenset(),
}

RESULT_TRANSITIONS: Final[dict[ResultStatus, frozenset[ResultStatus]]] = {
    ResultStatus.QUEUED: frozenset(
        {
            ResultStatus.RUNNING,
            ResultStatus.CANCELLED,
            ResultStatus.ERROR,
            ResultStatus.INTERRUPTED,
        }
    ),
    ResultStatus.RUNNING: frozenset(
        {
            ResultStatus.COMPLETED,
            ResultStatus.ERROR,
            ResultStatus.CANCELLED,
            ResultStatus.INTERRUPTED,
        }
    ),
    ResultStatus.COMPLETED: frozenset(),
    ResultStatus.ERROR: frozenset(),
    ResultStatus.CANCELLED: frozenset(),
    ResultStatus.INTERRUPTED: frozenset(),
}


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class UuidPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Dataset(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_datasets_name_version"),
        CheckConstraint("version >= 1", name="ck_datasets_version_positive"),
        Index("ix_datasets_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )

    cases: Mapped[list[TestCase]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TestCase.position",
    )
    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="dataset")


class TestCase(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "external_id", name="uq_test_cases_dataset_external"),
        UniqueConstraint("dataset_id", "position", name="uq_test_cases_dataset_position"),
        CheckConstraint("position >= 0", name="ck_test_cases_position_nonnegative"),
        Index("ix_test_cases_dataset_id", "dataset_id"),
        Index("ix_test_cases_case_hash", "case_hash"),
    )

    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_text: Mapped[str | None] = mapped_column(Text)
    context_chunks: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    expected_output: Mapped[str | None] = mapped_column(Text)
    required_phrases: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    constraints_json: Mapped[JSONDict] = mapped_column(
        "constraints", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    tags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    case_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    dataset: Mapped[Dataset] = relationship(back_populates="cases")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="test_case")

    def snapshot(self) -> JSONDict:
        return {
            "id": self.id,
            "external_id": self.external_id,
            "position": self.position,
            "input": self.input_text,
            "context": self.context_text,
            "context_chunks": list(self.context_chunks),
            "expected_output": self.expected_output,
            "required_phrases": list(self.required_phrases),
            "constraints": dict(self.constraints_json),
            "tags": list(self.tags),
            "metadata": dict(self.metadata_json),
            "case_hash": self.case_hash,
        }


class PromptTemplate(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_templates_name_version"),
        CheckConstraint("version >= 1", name="ck_prompt_templates_version_positive"),
        Index("ix_prompt_templates_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    system_template: Mapped[str] = mapped_column(Text, default="", nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    template_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )

    run_candidates: Mapped[list[RunCandidate]] = relationship(back_populates="prompt_template")

    def snapshot(self) -> JSONDict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "system_template": self.system_template,
            "user_template": self.user_template,
            "variables": list(self.variables),
            "template_hash": self.template_hash,
        }


class ModelProfile(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_profiles_name_version"),
        CheckConstraint("version >= 1", name="ck_model_profiles_version_positive"),
        CheckConstraint(
            "input_price_micro_usd_per_million_tokens IS NULL OR "
            "input_price_micro_usd_per_million_tokens >= 0",
            name="ck_model_profiles_input_price_nonnegative",
        ),
        CheckConstraint(
            "output_price_micro_usd_per_million_tokens IS NULL OR "
            "output_price_micro_usd_per_million_tokens >= 0",
            name="ck_model_profiles_output_price_nonnegative",
        ),
        CheckConstraint(
            "(input_price_micro_usd_per_million_tokens IS NULL AND "
            "output_price_micro_usd_per_million_tokens IS NULL AND pricing_source IS NULL) "
            "OR pricing_source IS NOT NULL",
            name="ck_model_profiles_pricing_source_present",
        ),
        Index("ix_model_profiles_provider_model", "provider", "model_name"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_mode: Mapped[ApiMode] = mapped_column(
        SQLAlchemyEnum(
            ApiMode,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="api_mode",
        ),
        nullable=False,
    )
    generation_parameters: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    input_price_micro_usd_per_million_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_price_micro_usd_per_million_tokens: Mapped[int | None] = mapped_column(BigInteger)
    pricing_source: Mapped[str | None] = mapped_column(String(200))
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )

    run_candidates: Mapped[list[RunCandidate]] = relationship(back_populates="model_profile")

    def snapshot(self) -> JSONDict:
        pricing_known = bool(self.metadata_json.get("synthetic")) or (
            self.input_price_micro_usd_per_million_tokens is not None
            and self.output_price_micro_usd_per_million_tokens is not None
        )
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "provider": self.provider,
            "model_name": self.model_name,
            "api_mode": self.api_mode.value,
            "generation_parameters": dict(self.generation_parameters),
            "input_price_micro_usd_per_million_tokens": (
                self.input_price_micro_usd_per_million_tokens
            ),
            "output_price_micro_usd_per_million_tokens": (
                self.output_price_micro_usd_per_million_tokens
            ),
            "pricing_source": self.pricing_source,
            "pricing_known": pricing_known,
            "enabled": self.enabled,
            "metadata": dict(self.metadata_json),
            "profile_hash": self.profile_hash,
        }


class EvaluationRun(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_evaluation_runs_idempotency_key"),
        CheckConstraint("total_items >= 0", name="ck_evaluation_runs_total_nonnegative"),
        CheckConstraint("completed_items >= 0", name="ck_evaluation_runs_completed_nonnegative"),
        CheckConstraint("succeeded_items >= 0", name="ck_evaluation_runs_succeeded_nonnegative"),
        CheckConstraint("failed_items >= 0", name="ck_evaluation_runs_failed_nonnegative"),
        CheckConstraint(
            "completed_items <= total_items", name="ck_evaluation_runs_completed_within_total"
        ),
        CheckConstraint(
            "succeeded_items + failed_items <= completed_items",
            name="ck_evaluation_runs_outcomes_within_completed",
        ),
        Index("ix_evaluation_runs_status_created", "status", "created_at"),
        Index("ix_evaluation_runs_dataset_id", "dataset_id"),
        Index("ix_evaluation_runs_request_hash", "request_hash"),
    )

    name: Mapped[str | None] = mapped_column(String(200))
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )
    dataset_snapshot: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_configuration_snapshot: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False
    )
    preflight_snapshot: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    application_version: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_type: Mapped[str] = mapped_column(String(64), default="in_process", nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    acknowledge_real_cost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledge_unknown_cost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[RunStatus] = mapped_column(
        SQLAlchemyEnum(
            RunStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="run_status",
        ),
        default=RunStatus.QUEUED,
        nullable=False,
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __mapper_args__: ClassVar[dict[str, Any]] = {  # type: ignore[misc]
        "version_id_col": state_version,
        "version_id_generator": False,
    }
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dataset: Mapped[Dataset] = relationship(back_populates="runs")
    candidates: Mapped[list[RunCandidate]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    def transition_to(
        self,
        target: RunStatus,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> None:
        target = RunStatus(target)
        if target == self.status:
            return
        if target not in RUN_TRANSITIONS[self.status]:
            raise InvalidStateTransition(f"cannot transition run from {self.status} to {target}")

        changed_at = at or utc_now()
        self.status = target
        self.status_reason = reason
        self.state_version += 1
        if target is RunStatus.RUNNING:
            self.started_at = self.started_at or changed_at
            self.heartbeat_at = changed_at
        if target is RunStatus.CANCEL_REQUESTED:
            self.cancel_requested_at = changed_at
        if target.is_terminal:
            self.finished_at = changed_at


class RunCandidate(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "run_candidates"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "prompt_template_id", "model_profile_id", name="uq_run_candidates_matrix"
        ),
        UniqueConstraint("run_id", "ordinal", name="uq_run_candidates_run_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_run_candidates_ordinal_nonnegative"),
        CheckConstraint("total_items >= 0", name="ck_run_candidates_total_nonnegative"),
        CheckConstraint("completed_items >= 0", name="ck_run_candidates_completed_nonnegative"),
        CheckConstraint("failed_items >= 0", name="ck_run_candidates_failed_nonnegative"),
        CheckConstraint(
            "completed_items <= total_items", name="ck_run_candidates_completed_within_total"
        ),
        Index("ix_run_candidates_run_status", "run_id", "status"),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    prompt_template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("prompt_templates.id", ondelete="RESTRICT"), nullable=False
    )
    model_profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    prompt_snapshot: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_snapshot: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_parameters_snapshot: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False
    )
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[RunStatus] = mapped_column(
        SQLAlchemyEnum(
            RunStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="candidate_status",
        ),
        default=RunStatus.QUEUED,
        nullable=False,
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __mapper_args__: ClassVar[dict[str, Any]] = {  # type: ignore[misc]
        "version_id_col": state_version,
        "version_id_generator": False,
    }
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[EvaluationRun] = relationship(back_populates="candidates")
    prompt_template: Mapped[PromptTemplate] = relationship(back_populates="run_candidates")
    model_profile: Mapped[ModelProfile] = relationship(back_populates="run_candidates")
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )

    def transition_to(
        self,
        target: RunStatus,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> None:
        target = RunStatus(target)
        if target == self.status:
            return
        if target not in RUN_TRANSITIONS[self.status]:
            raise InvalidStateTransition(
                f"cannot transition candidate from {self.status} to {target}"
            )

        changed_at = at or utc_now()
        self.status = target
        self.status_reason = reason
        self.state_version += 1
        if target is RunStatus.RUNNING:
            self.started_at = self.started_at or changed_at
            self.heartbeat_at = changed_at
        if target.is_terminal:
            self.finished_at = changed_at


class EvaluationResult(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_candidate_id", "test_case_id", name="uq_results_candidate_case"),
        CheckConstraint("retry_count >= 0", name="ck_results_retry_count_nonnegative"),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ck_results_latency_nonnegative"
        ),
        CheckConstraint("input_tokens >= 0", name="ck_results_input_tokens_nonnegative"),
        CheckConstraint("output_tokens >= 0", name="ck_results_output_tokens_nonnegative"),
        CheckConstraint("total_tokens >= 0", name="ck_results_total_tokens_nonnegative"),
        CheckConstraint(
            "estimated_cost_micro_usd IS NULL OR estimated_cost_micro_usd >= 0",
            name="ck_results_cost_micro_usd_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost_micro_usd IS NULL OR cost_source IS NOT NULL",
            name="ck_results_cost_source_consistent",
        ),
        CheckConstraint(
            "aggregate_score IS NULL OR (aggregate_score >= 0 AND aggregate_score <= 1)",
            name="ck_results_aggregate_score_unit_interval",
        ),
        CheckConstraint(
            "effective_metric_weight >= 0", name="ck_results_effective_weight_nonnegative"
        ),
        Index("ix_evaluation_results_run_status", "run_id", "status"),
        Index("ix_evaluation_results_candidate_id", "run_candidate_id"),
        Index("ix_evaluation_results_case_hash", "case_hash"),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    run_candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("run_candidates.id", ondelete="CASCADE"), nullable=False
    )
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False
    )

    input_snapshot: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    case_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_snapshot: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_snapshot: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_parameters_snapshot: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False
    )
    rendered_system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rendered_user_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    output_text: Mapped[str | None] = mapped_column(Text)
    metric_versions: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    metric_directions: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False
    )
    metric_applicability: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False
    )
    metric_results: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    aggregate_score: Mapped[float | None] = mapped_column(Float)
    aggregate_passed: Mapped[bool | None] = mapped_column(Boolean)
    effective_metric_weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(200))
    api_mode: Mapped[ApiMode | None] = mapped_column(
        SQLAlchemyEnum(
            ApiMode,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="result_api_mode",
        )
    )
    request_id: Mapped[str | None] = mapped_column(String(255))
    finish_reason: Mapped[str | None] = mapped_column(String(100))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger)
    cost_source: Mapped[str | None] = mapped_column(String(200))
    provider_metadata: Mapped[JSONDict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )

    status: Mapped[ResultStatus] = mapped_column(
        SQLAlchemyEnum(
            ResultStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="result_status",
        ),
        default=ResultStatus.QUEUED,
        nullable=False,
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __mapper_args__: ClassVar[dict[str, Any]] = {  # type: ignore[misc]
        "version_id_col": state_version,
        "version_id_generator": False,
    }
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[EvaluationRun] = relationship(back_populates="results")
    candidate: Mapped[RunCandidate] = relationship(back_populates="results")
    test_case: Mapped[TestCase] = relationship(back_populates="results")

    def transition_to(
        self,
        target: ResultStatus,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> None:
        target = ResultStatus(target)
        if target == self.status:
            return
        if target not in RESULT_TRANSITIONS[self.status]:
            raise InvalidStateTransition(f"cannot transition result from {self.status} to {target}")

        changed_at = at or utc_now()
        self.status = target
        self.status_reason = reason
        self.state_version += 1
        if target is ResultStatus.RUNNING:
            self.started_at = self.started_at or changed_at
        if target.is_terminal:
            self.finished_at = changed_at


_IMMUTABLE_PROVENANCE_FIELDS: Final[dict[type[Base], frozenset[str]]] = {
    EvaluationRun: frozenset(
        {
            "dataset_id",
            "dataset_snapshot",
            "dataset_hash",
            "metric_configuration_snapshot",
            "preflight_snapshot",
            "application_version",
            "executor_type",
            "idempotency_key",
            "request_hash",
            "acknowledge_real_cost",
            "acknowledge_unknown_cost",
        }
    ),
    RunCandidate: frozenset(
        {
            "run_id",
            "prompt_template_id",
            "model_profile_id",
            "ordinal",
            "prompt_snapshot",
            "prompt_hash",
            "model_snapshot",
            "model_hash",
            "generation_parameters_snapshot",
            "candidate_hash",
        }
    ),
    EvaluationResult: frozenset(
        {
            "run_id",
            "run_candidate_id",
            "test_case_id",
            "input_snapshot",
            "case_hash",
            "prompt_snapshot",
            "prompt_hash",
            "model_snapshot",
            "model_hash",
            "generation_parameters_snapshot",
            "rendered_system_prompt",
            "rendered_user_prompt",
        }
    ),
}

_IMMUTABLE_TERMINAL_RESULT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "output_text",
        "metric_versions",
        "metric_directions",
        "metric_applicability",
        "metric_results",
        "aggregate_score",
        "aggregate_passed",
        "effective_metric_weight",
        "provider",
        "model_name",
        "api_mode",
        "request_id",
        "finish_reason",
        "retry_count",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_cost_micro_usd",
        "cost_source",
        "provider_metadata",
        "error_type",
        "error_message",
        "error_retryable",
    }
)


def _reject_provenance_update(_mapper: Any, _connection: Any, target: Base) -> None:
    state = inspect(target)
    immutable_fields = _IMMUTABLE_PROVENANCE_FIELDS[type(target)]
    if isinstance(target, EvaluationResult):
        status_history = state.attrs.status.history
        previous_status = (
            ResultStatus(status_history.deleted[0])
            if status_history.deleted
            else ResultStatus(target.status)
        )
        if previous_status.is_terminal:
            immutable_fields = immutable_fields | _IMMUTABLE_TERMINAL_RESULT_FIELDS
    changed = [field for field in immutable_fields if state.attrs[field].history.has_changes()]
    if changed:
        fields = ", ".join(sorted(changed))
        raise ImmutableProvenanceError(f"immutable provenance fields changed: {fields}")


for _model in _IMMUTABLE_PROVENANCE_FIELDS:
    event.listen(_model, "before_update", _reject_provenance_update)
