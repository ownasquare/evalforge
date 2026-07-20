"""Typed SQLAlchemy domain models for immutable LLM evaluation provenance."""

from __future__ import annotations

import json
import math
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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
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


class RecordStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PlanCode(StrEnum):
    OPEN_SOURCE = "open_source"
    HOSTED_TRIAL = "hosted_trial"
    TEAM = "team"


class EntitlementStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELED = "canceled"


class TeamPilotRequestStatus(StrEnum):
    PENDING = "pending"
    CANCELED = "canceled"
    QUALIFIED = "qualified"
    DECLINED = "declined"


class EvaluationFrequency(StrEnum):
    WEEKLY = "weekly"
    SEVERAL_TIMES_WEEK = "several_times_week"
    DAILY = "daily"
    RELEASE_DRIVEN = "release_driven"


class ActivationEventName(StrEnum):
    LANDING = "landing"
    SIGNUP = "signup"
    CORE_JOB_START = "core_job_start"
    EVALUATION_COMPLETE = "evaluation_complete"
    RESULT_ENGAGEMENT = "result_engagement"
    SECOND_USE = "second_use"
    UPGRADE_VIEW = "upgrade_view"
    CHECKOUT_START = "checkout_start"
    ENTITLEMENT_ACTIVATION = "entitlement_activation"
    TEAM_REQUEST_SUBMITTED = "team_request_submitted"


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


class Workspace(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (Index("ix_workspaces_status_name", "status", "name"),)

    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[RecordStatus] = mapped_column(
        SQLAlchemyEnum(
            RecordStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="workspace_status",
        ),
        default=RecordStatus.ACTIVE,
        nullable=False,
    )

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", passive_deletes=True
    )


class User(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        Index("ix_users_status_created", "status", "created_at"),
    )

    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[RecordStatus] = mapped_column(
        SQLAlchemyEnum(
            RecordStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="user_status",
        ),
        default=RecordStatus.ACTIVE,
        nullable=False,
    )

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class WorkspaceMembership(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_memberships_workspace_user"),
        Index("ix_memberships_user_status", "user_id", "status"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[RecordStatus] = mapped_column(
        SQLAlchemyEnum(
            RecordStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="membership_status",
        ),
        default=RecordStatus.ACTIVE,
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AuditEvent(UuidPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
    )

    workspace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class WorkspaceEntitlement(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Server-authoritative current commercial access for one workspace."""

    __tablename__ = "workspace_entitlements"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_entitlements_workspace"),
        CheckConstraint("seat_limit >= 1", name="ck_workspace_entitlements_seat_limit"),
        Index("ix_workspace_entitlements_status_period", "status", "current_period_end"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    plan_code: Mapped[PlanCode] = mapped_column(
        SQLAlchemyEnum(
            PlanCode,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="workspace_plan_code",
        ),
        nullable=False,
    )
    status: Mapped[EntitlementStatus] = mapped_column(
        SQLAlchemyEnum(
            EntitlementStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="workspace_entitlement_status",
        ),
        nullable=False,
    )
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )


class BillingEvent(UuidPrimaryKeyMixin, Base):
    """Append-only, replay-safe commercial state transition evidence."""

    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_billing_events_provider_event"),
        Index("ix_billing_events_workspace_created", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class TeamPilotRequest(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A bounded, non-financial request for a qualified team pilot."""

    __tablename__ = "team_pilot_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_team_pilot_requests_idempotency"
        ),
        CheckConstraint(
            "requested_seats >= 2 AND requested_seats <= 250",
            name="ck_team_pilot_requests_requested_seats",
        ),
        Index("ix_team_pilot_requests_workspace_status", "workspace_id", "status"),
        Index(
            "ux_team_pilot_requests_workspace_pending",
            "workspace_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_frequency: Mapped[EvaluationFrequency] = mapped_column(
        SQLAlchemyEnum(
            EvaluationFrequency,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="team_pilot_evaluation_frequency",
        ),
        nullable=False,
    )
    security_review_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[TeamPilotRequestStatus] = mapped_column(
        SQLAlchemyEnum(
            TeamPilotRequestStatus,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="team_pilot_request_status",
        ),
        default=TeamPilotRequestStatus.PENDING,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActivationEvent(UuidPrimaryKeyMixin, Base):
    """Append-only, content-minimized pilot funnel event."""

    __tablename__ = "activation_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "event_key", name="uq_activation_events_event_key"),
        Index("ix_activation_events_workspace_name_created", "workspace_id", "name", "created_at"),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_activation_events_workspace_run",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[ActivationEventName] = mapped_column(
        SQLAlchemyEnum(
            ActivationEventName,
            values_callable=_enum_values,
            native_enum=False,
            create_constraint=True,
            name="activation_event_name",
        ),
        nullable=False,
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36))
    metadata_json: Mapped[JSONDict] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class WorkspaceScopedMixin:
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )


class Dataset(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_datasets_workspace_id"),
        UniqueConstraint(
            "workspace_id", "name", "version", name="uq_datasets_workspace_name_version"
        ),
        CheckConstraint("version >= 1", name="ck_datasets_version_positive"),
        Index("ix_datasets_workspace_created", "workspace_id", "created_at"),
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
        primaryjoin="Dataset.id == TestCase.dataset_id",
        foreign_keys="TestCase.dataset_id",
    )
    runs: Mapped[list[EvaluationRun]] = relationship(
        back_populates="dataset",
        primaryjoin="Dataset.id == EvaluationRun.dataset_id",
        foreign_keys="EvaluationRun.dataset_id",
    )


class TestCase(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_test_cases_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "dataset_id",
            "external_id",
            name="uq_test_cases_workspace_dataset_external",
        ),
        UniqueConstraint(
            "workspace_id",
            "dataset_id",
            "position",
            name="uq_test_cases_workspace_dataset_position",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_test_cases_workspace_dataset",
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 0", name="ck_test_cases_position_nonnegative"),
        Index("ix_test_cases_workspace_dataset", "workspace_id", "dataset_id"),
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

    dataset: Mapped[Dataset] = relationship(
        back_populates="cases",
        primaryjoin="Dataset.id == TestCase.dataset_id",
        foreign_keys=[dataset_id],
    )
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="test_case",
        primaryjoin="TestCase.id == EvaluationResult.test_case_id",
        foreign_keys="EvaluationResult.test_case_id",
    )

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


class PromptTemplate(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_prompt_templates_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "name",
            "version",
            name="uq_prompt_templates_workspace_name_version",
        ),
        CheckConstraint("version >= 1", name="ck_prompt_templates_version_positive"),
        Index("ix_prompt_templates_workspace_created", "workspace_id", "created_at"),
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

    run_candidates: Mapped[list[RunCandidate]] = relationship(
        back_populates="prompt_template",
        primaryjoin="PromptTemplate.id == RunCandidate.prompt_template_id",
        foreign_keys="RunCandidate.prompt_template_id",
    )

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


class ModelProfile(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_model_profiles_workspace_id"),
        UniqueConstraint(
            "workspace_id", "name", "version", name="uq_model_profiles_workspace_name_version"
        ),
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
        Index(
            "ix_model_profiles_workspace_provider_model",
            "workspace_id",
            "provider",
            "model_name",
        ),
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

    run_candidates: Mapped[list[RunCandidate]] = relationship(
        back_populates="model_profile",
        primaryjoin="ModelProfile.id == RunCandidate.model_profile_id",
        foreign_keys="RunCandidate.model_profile_id",
    )

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


class EvaluationRun(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_evaluation_runs_workspace_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_evaluation_runs_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"],
            ["datasets.workspace_id", "datasets.id"],
            name="fk_evaluation_runs_workspace_dataset",
            ondelete="RESTRICT",
        ),
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
        CheckConstraint("lease_epoch >= 0", name="ck_evaluation_runs_lease_epoch_nonnegative"),
        CheckConstraint(
            "claim_attempts >= 0", name="ck_evaluation_runs_claim_attempts_nonnegative"
        ),
        Index(
            "ix_evaluation_runs_workspace_status_created", "workspace_id", "status", "created_at"
        ),
        Index("ix_evaluation_runs_workspace_dataset", "workspace_id", "dataset_id"),
        Index("ix_evaluation_runs_workspace_request_hash", "workspace_id", "request_hash"),
        Index(
            "ix_evaluation_runs_claimable",
            "status",
            "next_claim_at",
            "lease_expires_at",
            "queued_at",
        ),
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
    requested_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
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
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_claim_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("'1970-01-01 00:00:00'"),
        nullable=False,
    )

    dataset: Mapped[Dataset] = relationship(
        back_populates="runs",
        primaryjoin="Dataset.id == EvaluationRun.dataset_id",
        foreign_keys=[dataset_id],
    )
    candidates: Mapped[list[RunCandidate]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        primaryjoin="EvaluationRun.id == RunCandidate.run_id",
        foreign_keys="RunCandidate.run_id",
    )
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        primaryjoin="EvaluationRun.id == EvaluationResult.run_id",
        foreign_keys="EvaluationResult.run_id",
    )
    execution_attempts: Mapped[list[ExecutionAttempt]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        primaryjoin="EvaluationRun.id == ExecutionAttempt.run_id",
        foreign_keys="ExecutionAttempt.run_id",
    )
    calibration_reports: Mapped[list[CalibrationReport]] = relationship(
        back_populates="run",
        primaryjoin="EvaluationRun.id == CalibrationReport.run_id",
        foreign_keys="CalibrationReport.run_id",
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


class RunCandidate(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "run_candidates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_run_candidates_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "prompt_template_id",
            "model_profile_id",
            name="uq_run_candidates_workspace_matrix",
        ),
        UniqueConstraint(
            "workspace_id", "run_id", "ordinal", name="uq_run_candidates_workspace_ordinal"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_run_candidates_workspace_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "prompt_template_id"],
            ["prompt_templates.workspace_id", "prompt_templates.id"],
            name="fk_run_candidates_workspace_prompt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_profile_id"],
            ["model_profiles.workspace_id", "model_profiles.id"],
            name="fk_run_candidates_workspace_model",
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal >= 0", name="ck_run_candidates_ordinal_nonnegative"),
        CheckConstraint("total_items >= 0", name="ck_run_candidates_total_nonnegative"),
        CheckConstraint("completed_items >= 0", name="ck_run_candidates_completed_nonnegative"),
        CheckConstraint("failed_items >= 0", name="ck_run_candidates_failed_nonnegative"),
        CheckConstraint(
            "completed_items <= total_items", name="ck_run_candidates_completed_within_total"
        ),
        Index(
            "ux_run_candidates_workspace_run_id",
            "workspace_id",
            "run_id",
            "id",
            unique=True,
        ),
        Index("ix_run_candidates_workspace_run_status", "workspace_id", "run_id", "status"),
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

    run: Mapped[EvaluationRun] = relationship(
        back_populates="candidates",
        primaryjoin="EvaluationRun.id == RunCandidate.run_id",
        foreign_keys=[run_id],
    )
    prompt_template: Mapped[PromptTemplate] = relationship(
        back_populates="run_candidates",
        primaryjoin="PromptTemplate.id == RunCandidate.prompt_template_id",
        foreign_keys=[prompt_template_id],
    )
    model_profile: Mapped[ModelProfile] = relationship(
        back_populates="run_candidates",
        primaryjoin="ModelProfile.id == RunCandidate.model_profile_id",
        foreign_keys=[model_profile_id],
    )
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        primaryjoin="RunCandidate.id == EvaluationResult.run_candidate_id",
        foreign_keys="EvaluationResult.run_candidate_id",
    )
    calibration_reports: Mapped[list[CalibrationReport]] = relationship(
        back_populates="candidate",
        primaryjoin="RunCandidate.id == CalibrationReport.run_candidate_id",
        foreign_keys="CalibrationReport.run_candidate_id",
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


class EvaluationResult(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_results_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "run_candidate_id",
            "test_case_id",
            name="uq_results_workspace_candidate_case",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_results_workspace_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_candidate_id"],
            ["run_candidates.workspace_id", "run_candidates.id"],
            name="fk_results_workspace_candidate",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "test_case_id"],
            ["test_cases.workspace_id", "test_cases.id"],
            name="fk_results_workspace_case",
            ondelete="RESTRICT",
        ),
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
        Index("ix_results_workspace_run_status", "workspace_id", "run_id", "status"),
        Index("ix_results_workspace_candidate", "workspace_id", "run_candidate_id"),
        Index("ix_results_workspace_case_hash", "workspace_id", "case_hash"),
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

    run: Mapped[EvaluationRun] = relationship(
        back_populates="results",
        primaryjoin="EvaluationRun.id == EvaluationResult.run_id",
        foreign_keys=[run_id],
    )
    candidate: Mapped[RunCandidate] = relationship(
        back_populates="results",
        primaryjoin="RunCandidate.id == EvaluationResult.run_candidate_id",
        foreign_keys=[run_candidate_id],
    )
    test_case: Mapped[TestCase] = relationship(
        back_populates="results",
        primaryjoin="TestCase.id == EvaluationResult.test_case_id",
        foreign_keys=[test_case_id],
    )

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


class CalibrationReport(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable, content-minimized human-calibration evidence for one run candidate."""

    __tablename__ = "calibration_reports"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_calibration_reports_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "run_candidate_id",
            "report_sha256",
            name="uq_calibration_reports_workspace_report_hash",
        ),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "run_candidate_id",
            "metric_name",
            "manifest_sha256",
            "selected_threshold",
            name="uq_calibration_reports_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_calibration_reports_workspace_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_candidate_id"],
            ["run_candidates.workspace_id", "run_candidates.id"],
            name="fk_calibration_reports_workspace_candidate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "run_id", "run_candidate_id"],
            ["run_candidates.workspace_id", "run_candidates.run_id", "run_candidates.id"],
            name="fk_calibration_reports_workspace_run_candidate",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "selected_threshold >= 0 AND selected_threshold <= 1",
            name="ck_calibration_reports_threshold_unit_interval",
        ),
        CheckConstraint(
            "metric_direction IN ('higher_is_better', 'lower_is_better')",
            name="ck_calibration_reports_metric_direction",
        ),
        CheckConstraint(
            "schema_version = 'evalforge.calibration-report.v1'",
            name="ck_calibration_reports_schema_version",
        ),
        CheckConstraint(
            "evidence_kind = 'offline_statistical_evidence'",
            name="ck_calibration_reports_evidence_kind",
        ),
        CheckConstraint(
            "production_validated = false",
            name="ck_calibration_reports_not_production_validated",
        ),
        CheckConstraint(
            "length(manifest_sha256) = 64 AND manifest_sha256 = lower(manifest_sha256)",
            name="ck_calibration_reports_manifest_hash",
        ),
        CheckConstraint(
            "length(report_sha256) = 64 AND report_sha256 = lower(report_sha256)",
            name="ck_calibration_reports_report_hash",
        ),
        CheckConstraint("sample_size > 0", name="ck_calibration_reports_sample_positive"),
        CheckConstraint(
            "human_pass_count >= 0 AND human_fail_count >= 0",
            name="ck_calibration_reports_human_counts_nonnegative",
        ),
        CheckConstraint(
            "human_pass_count + human_fail_count = sample_size",
            name="ck_calibration_reports_human_counts_total",
        ),
        CheckConstraint("reviewer_count > 0", name="ck_calibration_reports_reviewer_positive"),
        CheckConstraint(
            "reviewer_count <= sample_size",
            name="ck_calibration_reports_reviewer_within_sample",
        ),
        CheckConstraint(
            "precision >= 0 AND precision <= 1",
            name="ck_calibration_reports_precision_unit_interval",
        ),
        CheckConstraint(
            "recall >= 0 AND recall <= 1",
            name="ck_calibration_reports_recall_unit_interval",
        ),
        CheckConstraint("f1 >= 0 AND f1 <= 1", name="ck_calibration_reports_f1_unit_interval"),
        Index(
            "ix_calibration_reports_workspace_run_created",
            "workspace_id",
            "run_id",
            "created_at",
        ),
        Index(
            "ix_calibration_reports_workspace_candidate_metric",
            "workspace_id",
            "run_candidate_id",
            "metric_name",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    run_candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("run_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_direction: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    production_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    human_pass_count: Mapped[int] = mapped_column(Integer, nullable=False)
    human_fail_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    precision: Mapped[float] = mapped_column(Float, nullable=False)
    recall: Mapped[float] = mapped_column(Float, nullable=False)
    f1: Mapped[float] = mapped_column(Float, nullable=False)
    report_payload: Mapped[JSONDict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    run: Mapped[EvaluationRun] = relationship(
        back_populates="calibration_reports",
        primaryjoin="EvaluationRun.id == CalibrationReport.run_id",
        foreign_keys=[run_id],
    )
    candidate: Mapped[RunCandidate] = relationship(
        back_populates="calibration_reports",
        primaryjoin="RunCandidate.id == CalibrationReport.run_candidate_id",
        foreign_keys=[run_candidate_id],
    )


class CalibrationReportIntegrityError(ValueError):
    """Raised when minimized calibration evidence and its hash disagree."""


def validate_calibration_report_integrity(report: CalibrationReport) -> None:
    """Verify the canonical payload, mirrored columns, privacy shape, and derived rates."""

    if not isinstance(report, CalibrationReport):
        raise TypeError("report must be a CalibrationReport")
    payload = report.report_payload
    expected_keys = {
        "calibration_set_sha256",
        "confusion_matrix",
        "dataset",
        "evidence_kind",
        "f1",
        "human_fail_count",
        "human_pass_count",
        "label_manifest_sha256",
        "metric",
        "precision",
        "production_validated",
        "recall",
        "reviewer_count",
        "sample_size",
        "schema_version",
        "selected_threshold",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise CalibrationReportIntegrityError("calibration report payload shape is invalid")
    dataset = payload.get("dataset")
    metric = payload.get("metric")
    confusion = payload.get("confusion_matrix")
    if (
        not isinstance(dataset, dict)
        or set(dataset) != {"id", "version", "sha256"}
        or not isinstance(metric, dict)
        or set(metric) != {"name", "version", "direction"}
        or not isinstance(confusion, dict)
        or set(confusion) != {"false_negative", "false_positive", "true_negative", "true_positive"}
    ):
        raise CalibrationReportIntegrityError("calibration report evidence shape is invalid")

    mirrored = {
        "evidence_kind": report.evidence_kind,
        "f1": report.f1,
        "human_fail_count": report.human_fail_count,
        "human_pass_count": report.human_pass_count,
        "label_manifest_sha256": report.manifest_sha256,
        "precision": report.precision,
        "production_validated": report.production_validated,
        "recall": report.recall,
        "reviewer_count": report.reviewer_count,
        "sample_size": report.sample_size,
        "schema_version": report.schema_version,
        "selected_threshold": report.selected_threshold,
    }
    if any(payload.get(field) != value for field, value in mirrored.items()):
        raise CalibrationReportIntegrityError("calibration report columns do not match payload")
    if metric != {
        "name": report.metric_name,
        "version": report.metric_version,
        "direction": report.metric_direction,
    }:
        raise CalibrationReportIntegrityError("calibration report metric does not match payload")

    hash_values = (
        report.manifest_sha256,
        report.report_sha256,
        payload.get("calibration_set_sha256"),
        dataset.get("sha256"),
    )
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in hash_values
    ):
        raise CalibrationReportIntegrityError("calibration report hashes are invalid")
    try:
        calculated_hash = canonical_json_hash(payload)
    except (TypeError, ValueError):
        raise CalibrationReportIntegrityError(
            "calibration report payload is not canonical JSON"
        ) from None
    if calculated_hash != report.report_sha256:
        raise CalibrationReportIntegrityError("calibration report payload hash does not match")

    counts = tuple(confusion.values())
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise CalibrationReportIntegrityError("calibration report confusion matrix is invalid")
    true_positive = confusion["true_positive"]
    true_negative = confusion["true_negative"]
    false_positive = confusion["false_positive"]
    false_negative = confusion["false_negative"]
    if (
        sum(counts) != report.sample_size
        or true_positive + false_negative != report.human_pass_count
        or true_negative + false_positive != report.human_fail_count
        or report.reviewer_count > report.sample_size
    ):
        raise CalibrationReportIntegrityError("calibration report counts are inconsistent")
    precision = _calibration_ratio(true_positive, true_positive + false_positive)
    recall = _calibration_ratio(true_positive, true_positive + false_negative)
    f1 = _calibration_ratio(2.0 * precision * recall, precision + recall)
    if not all(
        math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-12)
        for observed, expected in (
            (report.precision, precision),
            (report.recall, recall),
            (report.f1, f1),
        )
    ):
        raise CalibrationReportIntegrityError("calibration report rates are inconsistent")


def _calibration_ratio(numerator: float, denominator: float) -> float:
    """Match the stable six-decimal calibration report representation."""

    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


class ExecutionAttempt(WorkspaceScopedMixin, UuidPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable-identity lease epoch with mutable liveness and outcome fields."""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "lease_epoch", name="uq_execution_attempts_run_epoch"),
        UniqueConstraint("lease_token", name="uq_execution_attempts_lease_token"),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_execution_attempts_workspace_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("lease_epoch > 0", name="ck_execution_attempts_epoch_positive"),
        Index("ix_execution_attempts_run_started", "run_id", "started_at"),
        Index("ix_execution_attempts_owner_active", "lease_owner", "finished_at"),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    lease_owner: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(40))
    error_type: Mapped[str | None] = mapped_column(String(100))

    run: Mapped[EvaluationRun] = relationship(
        back_populates="execution_attempts",
        primaryjoin="EvaluationRun.id == ExecutionAttempt.run_id",
        foreign_keys=[run_id],
    )


_IMMUTABLE_PROVENANCE_FIELDS: Final[dict[type[Base], frozenset[str]]] = {
    EvaluationRun: frozenset(
        {
            "workspace_id",
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
            "workspace_id",
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
            "workspace_id",
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


def _reject_audit_mutation(_mapper: Any, _connection: Any, _target: AuditEvent) -> None:
    raise ImmutableProvenanceError("audit events are append-only")


def _reject_calibration_mutation(
    _mapper: Any,
    _connection: Any,
    _target: CalibrationReport,
) -> None:
    raise ImmutableProvenanceError("calibration reports are append-only")


def _reject_commercial_event_mutation(
    _mapper: Any,
    _connection: Any,
    _target: BillingEvent | ActivationEvent,
) -> None:
    raise ImmutableProvenanceError("commercial evidence events are append-only")


for _model in _IMMUTABLE_PROVENANCE_FIELDS:
    event.listen(_model, "before_update", _reject_provenance_update)

event.listen(AuditEvent, "before_update", _reject_audit_mutation)
event.listen(AuditEvent, "before_delete", _reject_audit_mutation)
event.listen(CalibrationReport, "before_update", _reject_calibration_mutation)
event.listen(CalibrationReport, "before_delete", _reject_calibration_mutation)
event.listen(BillingEvent, "before_update", _reject_commercial_event_mutation)
event.listen(BillingEvent, "before_delete", _reject_commercial_event_mutation)
event.listen(ActivationEvent, "before_update", _reject_commercial_event_mutation)
event.listen(ActivationEvent, "before_delete", _reject_commercial_event_mutation)
