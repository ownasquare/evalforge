"""Validated public contracts for EvalForge resources and run provenance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from string import Formatter
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalforge.models import (
    ActivationEventName,
    ApiMode,
    EntitlementStatus,
    EvaluationFrequency,
    MetricApplicability,
    MetricDirection,
    PlanCode,
    ResultStatus,
    RunStatus,
    TeamPilotRequestStatus,
)

JSONDict = dict[str, Any]
PageItemT = TypeVar("PageItemT")
ALLOWED_PROMPT_FIELDS = frozenset({"input", "context"})
ALLOWED_GENERATION_PARAMETERS = frozenset({"temperature", "max_output_tokens", "seed"})
_SENSITIVE_METADATA_FRAGMENTS = (
    "apikey",
    "apitoken",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "password",
    "secret",
    "credential",
    "authorization",
    "baseurl",
    "endpoint",
)


def _validate_json_tree(
    value: Any,
    *,
    reject_sensitive_keys: bool,
    depth: int = 0,
) -> None:
    if depth > 20:
        raise ValueError("JSON values may be nested at most 20 levels")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            compact_key = "".join(character for character in key.casefold() if character.isalnum())
            if reject_sensitive_keys and (
                compact_key.endswith("token")
                or any(fragment in compact_key for fragment in _SENSITIVE_METADATA_FRAGMENTS)
            ):
                raise ValueError("metadata must not contain credentials or provider endpoints")
            _validate_json_tree(
                nested,
                reject_sensitive_keys=reject_sensitive_keys,
                depth=depth + 1,
            )
    elif isinstance(value, list):
        for nested in value:
            _validate_json_tree(
                nested,
                reject_sensitive_keys=reject_sensitive_keys,
                depth=depth + 1,
            )
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("metadata values must be JSON-compatible")


def _validate_metadata(value: JSONDict) -> JSONDict:
    _validate_json_tree(value, reject_sensitive_keys=True)
    return value


def _reject_explicit_nulls(model: BaseModel, fields: set[str]) -> None:
    rejected = sorted(
        field for field in fields & model.model_fields_set if getattr(model, field) is None
    )
    if rejected:
        raise ValueError(f"fields may not be null: {', '.join(rejected)}")


def extract_template_variables(template: str) -> set[str]:
    """Return allowed format fields or raise for an unsafe/unknown expression."""

    variables: set[str] = set()
    placeholder_count = 0
    try:
        parsed = Formatter().parse(template)
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            placeholder_count += 1
            if placeholder_count > 20:
                raise ValueError("prompt templates support at most 20 placeholders")
            if (
                field_name not in ALLOWED_PROMPT_FIELDS
                or format_spec
                or conversion
                or "." in field_name
                or "[" in field_name
            ):
                allowed = ", ".join(sorted(ALLOWED_PROMPT_FIELDS))
                raise ValueError(f"prompt fields must be simple values from: {allowed}")
            variables.add(field_name)
    except ValueError as exc:
        if str(exc).startswith("prompt fields"):
            raise
        raise ValueError("prompt template contains invalid braces") from exc
    return variables


def validate_generation_parameters(
    parameters: Mapping[str, Any],
    *,
    max_output_tokens: int,
    allow_seed: bool,
) -> None:
    """Validate exactly the generation options implemented by the adapters."""

    unknown = sorted(set(parameters) - ALLOWED_GENERATION_PARAMETERS)
    if unknown:
        raise ValueError(f"unsupported generation parameters: {', '.join(unknown)}")
    temperature = parameters.get("temperature", 0.0)
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(float(temperature))
        or not 0 <= float(temperature) <= 2
    ):
        raise ValueError("temperature must be a finite number between 0 and 2")
    requested_tokens = parameters.get("max_output_tokens", 512)
    if (
        isinstance(requested_tokens, bool)
        or not isinstance(requested_tokens, int)
        or not 1 <= requested_tokens <= max_output_tokens
    ):
        raise ValueError(f"max_output_tokens must be an integer between 1 and {max_output_tokens}")
    seed = parameters.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not -(2**63) <= seed < 2**63:
        raise ValueError("seed must fit in a signed 64-bit integer")
    if not allow_seed and "seed" in parameters:
        raise ValueError("seed is supported only by deterministic demo profiles")


class StrictSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class CaseConstraints(StrictSchema):
    min_words: int | None = Field(default=None, ge=0)
    max_words: int | None = Field(default=None, ge=0)
    min_sentences: int | None = Field(default=None, ge=0)
    max_sentences: int | None = Field(default=None, ge=0)
    required_prefix: str | None = Field(default=None, min_length=1, max_length=500)
    required_suffix: str | None = Field(default=None, min_length=1, max_length=500)
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=100)
    expects_json: bool = False
    json_schema: JSONDict | None = None

    @field_validator("forbidden_phrases")
    @classmethod
    def normalize_forbidden_phrases(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 500 for item in normalized):
            raise ValueError("forbidden phrases must be at most 500 characters")
        return normalized

    @field_validator("json_schema")
    @classmethod
    def validate_json_schema(cls, value: JSONDict | None) -> JSONDict | None:
        if value is None:
            return None
        _validate_json_tree(value, reject_sensitive_keys=False)
        _reject_external_schema_references(value)
        try:
            validator_for(value).check_schema(value)
        except SchemaError as exc:
            raise ValueError("json_schema must be a valid JSON Schema") from exc
        return value

    @model_validator(mode="after")
    def validate_ranges(self) -> CaseConstraints:
        if (
            self.min_words is not None
            and self.max_words is not None
            and self.min_words > self.max_words
        ):
            raise ValueError("min_words cannot exceed max_words")
        if (
            self.min_sentences is not None
            and self.max_sentences is not None
            and self.min_sentences > self.max_sentences
        ):
            raise ValueError("min_sentences cannot exceed max_sentences")
        return self


def _reject_external_schema_references(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "$ref" and (not isinstance(nested, str) or not nested.startswith("#")):
                raise ValueError("json_schema external references are not supported")
            _reject_external_schema_references(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_external_schema_references(nested)


def normalize_case_constraints(value: JSONDict) -> JSONDict:
    return CaseConstraints.model_validate(value).model_dump(mode="json", exclude_none=True)


def normalize_case_metadata(value: JSONDict) -> JSONDict:
    _validate_metadata(value)
    normalized = dict(value)
    if "relevance_keywords" not in normalized:
        return normalized
    keywords = normalized["relevance_keywords"]
    if not isinstance(keywords, list) or any(not isinstance(item, str) for item in keywords):
        raise ValueError("metadata relevance_keywords must be a list of strings")
    cleaned = list(dict.fromkeys(item.strip() for item in keywords if item.strip()))
    if len(cleaned) > 100 or any(len(item) > 500 for item in cleaned):
        raise ValueError("relevance_keywords exceeds the supported size")
    normalized["relevance_keywords"] = cleaned
    return normalized


class TestCaseBase(StrictSchema):
    external_id: str = Field(min_length=1, max_length=200)
    position: int = Field(default=0, ge=0, le=2_147_483_647)
    input_text: str = Field(min_length=1, max_length=20_000)
    context_text: str | None = Field(default=None, max_length=100_000)
    context_chunks: list[str] = Field(default_factory=list, max_length=100)
    expected_output: str | None = Field(default=None, max_length=100_000)
    required_phrases: list[str] = Field(default_factory=list, max_length=100)
    constraints_json: JSONDict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata_json: JSONDict = Field(default_factory=dict)

    @field_validator("required_phrases", "tags")
    @classmethod
    def normalize_string_list(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 500 for item in normalized):
            raise ValueError("list values must be at most 500 characters")
        return normalized

    @field_validator("context_chunks")
    @classmethod
    def normalize_context_chunks(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if any(len(item) > 100_000 for item in normalized):
            raise ValueError("context chunks must be at most 100000 characters")
        if len("\n\n".join(normalized)) > 100_000:
            raise ValueError("combined context chunks exceed 100000 characters")
        return normalized

    @model_validator(mode="after")
    def align_context_representations(self) -> TestCaseBase:
        if self.context_chunks:
            joined = "\n\n".join(self.context_chunks)
            if self.context_text is not None and self.context_text != joined:
                raise ValueError("context_text must match the joined context_chunks")
            self.context_text = joined
        elif self.context_text:
            self.context_chunks = [self.context_text]
        return self

    @field_validator("constraints_json")
    @classmethod
    def validate_constraints(cls, value: JSONDict) -> JSONDict:
        return normalize_case_constraints(value)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: JSONDict) -> JSONDict:
        return normalize_case_metadata(value)


class TestCaseCreate(TestCaseBase):
    pass


class TestCaseUpdate(StrictSchema):
    external_id: str | None = Field(default=None, min_length=1, max_length=200)
    position: int | None = Field(default=None, ge=0, le=2_147_483_647)
    input_text: str | None = Field(default=None, min_length=1, max_length=20_000)
    context_text: str | None = Field(default=None, max_length=100_000)
    context_chunks: list[str] | None = Field(default=None, max_length=100)
    expected_output: str | None = Field(default=None, max_length=100_000)
    required_phrases: list[str] | None = Field(default=None, max_length=100)
    constraints_json: JSONDict | None = None
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata_json: JSONDict | None = None

    @field_validator("required_phrases", "tags")
    @classmethod
    def normalize_optional_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 500 for item in normalized):
            raise ValueError("list values must be at most 500 characters")
        return normalized

    @field_validator("context_chunks")
    @classmethod
    def normalize_optional_context_chunks(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value if item.strip()]
        if any(len(item) > 100_000 for item in normalized):
            raise ValueError("context chunks must be at most 100000 characters")
        if len("\n\n".join(normalized)) > 100_000:
            raise ValueError("combined context chunks exceed 100000 characters")
        return normalized

    @field_validator("constraints_json")
    @classmethod
    def validate_optional_constraints(cls, value: JSONDict | None) -> JSONDict | None:
        return normalize_case_constraints(value) if value is not None else None

    @field_validator("metadata_json")
    @classmethod
    def validate_optional_metadata(cls, value: JSONDict | None) -> JSONDict | None:
        return normalize_case_metadata(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> TestCaseUpdate:
        _reject_explicit_nulls(
            self,
            {
                "external_id",
                "position",
                "input_text",
                "context_chunks",
                "required_phrases",
                "constraints_json",
                "tags",
                "metadata_json",
            },
        )
        return self


class TestCaseRead(TestCaseBase):
    id: UUID
    workspace_id: UUID
    dataset_id: UUID
    case_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime
    updated_at: datetime


class DatasetBase(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    metadata_json: JSONDict = Field(default_factory=dict)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata_json(cls, value: JSONDict) -> JSONDict:
        return _validate_metadata(value)


class DatasetCreate(DatasetBase):
    cases: list[TestCaseCreate] = Field(default_factory=list, max_length=500)


class DatasetUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    metadata_json: JSONDict | None = None

    @field_validator("metadata_json")
    @classmethod
    def validate_optional_metadata_json(cls, value: JSONDict | None) -> JSONDict | None:
        return _validate_metadata(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> DatasetUpdate:
        _reject_explicit_nulls(self, {"name", "metadata_json"})
        return self


class DatasetRead(DatasetBase):
    id: UUID
    workspace_id: UUID
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime
    updated_at: datetime


class DatasetDetail(DatasetRead):
    cases: list[TestCaseRead]


class PromptTemplateBase(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    system_template: str = Field(default="", max_length=50_000)
    user_template: str = Field(min_length=1, max_length=50_000)
    metadata_json: JSONDict = Field(default_factory=dict)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata_json(cls, value: JSONDict) -> JSONDict:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def validate_prompt_fields(self) -> PromptTemplateBase:
        extract_template_variables(self.system_template)
        extract_template_variables(self.user_template)
        return self


class PromptTemplateCreate(PromptTemplateBase):
    pass


class PromptTemplateUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    system_template: str | None = Field(default=None, max_length=50_000)
    user_template: str | None = Field(default=None, min_length=1, max_length=50_000)
    metadata_json: JSONDict | None = None

    @field_validator("system_template", "user_template")
    @classmethod
    def validate_changed_template(cls, value: str | None) -> str | None:
        if value is not None:
            extract_template_variables(value)
        return value

    @field_validator("metadata_json")
    @classmethod
    def validate_optional_metadata_json(cls, value: JSONDict | None) -> JSONDict | None:
        return _validate_metadata(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> PromptTemplateUpdate:
        _reject_explicit_nulls(self, {"name", "system_template", "user_template", "metadata_json"})
        return self


class PromptTemplateRead(PromptTemplateBase):
    id: UUID
    workspace_id: UUID
    variables: list[str]
    template_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime
    updated_at: datetime


class ModelProfileBase(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    provider: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    api_mode: ApiMode
    generation_parameters: JSONDict = Field(default_factory=dict)
    input_price_micro_usd_per_million_tokens: int | None = Field(
        default=None, ge=0, le=1_000_000_000_000
    )
    output_price_micro_usd_per_million_tokens: int | None = Field(
        default=None, ge=0, le=1_000_000_000_000
    )
    pricing_source: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool = True
    metadata_json: JSONDict = Field(default_factory=dict)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata_json(cls, value: JSONDict) -> JSONDict:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def validate_pricing_source(self) -> ModelProfileBase:
        prices = (
            self.input_price_micro_usd_per_million_tokens,
            self.output_price_micro_usd_per_million_tokens,
        )
        if any(price is not None for price in prices) and self.pricing_source is None:
            raise ValueError("known pricing requires pricing_source")
        if all(price is None for price in prices) and self.pricing_source is not None:
            raise ValueError("pricing_source requires at least one known price")
        return self


class ModelProfileCreate(ModelProfileBase):
    pass


class ModelProfileUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    generation_parameters: JSONDict | None = None
    input_price_micro_usd_per_million_tokens: int | None = Field(
        default=None, ge=0, le=1_000_000_000_000
    )
    output_price_micro_usd_per_million_tokens: int | None = Field(
        default=None, ge=0, le=1_000_000_000_000
    )
    pricing_source: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    metadata_json: JSONDict | None = None

    @field_validator("metadata_json")
    @classmethod
    def validate_optional_metadata_json(cls, value: JSONDict | None) -> JSONDict | None:
        return _validate_metadata(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> ModelProfileUpdate:
        _reject_explicit_nulls(self, {"name", "generation_parameters", "enabled", "metadata_json"})
        return self


class ModelProfileRead(ModelProfileBase):
    id: UUID
    workspace_id: UUID
    profile_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime
    updated_at: datetime


class MetricConfiguration(StrictSchema):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER
    weight: float = Field(default=1.0, ge=0, le=1_000, allow_inf_nan=False)
    threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    enabled: bool = True


class MetricResult(StrictSchema):
    name: str
    version: str
    direction: MetricDirection
    applicability: MetricApplicability
    score: float | None = Field(default=None, ge=0, le=1)
    threshold: float | None = Field(default=None, ge=0, le=1)
    passed: bool | None = None
    reason: str
    evidence: JSONDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_applicability(self) -> MetricResult:
        if self.applicability is MetricApplicability.NOT_APPLICABLE:
            if self.score is not None or self.passed is not None:
                raise ValueError("not_applicable metrics cannot carry score or passed values")
        elif self.applicability is MetricApplicability.APPLICABLE and self.score is None:
            raise ValueError("applicable metrics require a score")
        return self


class EvaluationRunCreate(StrictSchema):
    dataset_id: UUID
    prompt_ids: list[UUID] = Field(min_length=1, max_length=100)
    model_ids: list[UUID] = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    metrics: list[MetricConfiguration] = Field(default_factory=list)
    requested_by: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    acknowledge_real_cost: bool = False
    acknowledge_unknown_cost: bool = False
    acknowledge_external_data_transfer: bool = False
    spend_limit_micro_usd: int | None = Field(default=None, ge=1, le=1_000_000_000_000)

    @field_validator("prompt_ids", "model_ids")
    @classmethod
    def reject_duplicate_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("candidate ids must be unique")
        return value


class RunCandidateRead(StrictSchema):
    id: UUID
    workspace_id: UUID
    run_id: UUID
    prompt_template_id: UUID
    model_profile_id: UUID
    ordinal: int
    label: str
    prompt_snapshot: JSONDict
    prompt_hash: str
    model_snapshot: JSONDict
    model_hash: str
    generation_parameters_snapshot: JSONDict
    candidate_hash: str
    status: RunStatus
    status_reason: str | None
    state_version: int
    total_items: int
    completed_items: int
    failed_items: int
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationResultRead(StrictSchema):
    id: UUID
    workspace_id: UUID
    run_id: UUID
    run_candidate_id: UUID
    test_case_id: UUID
    input_snapshot: JSONDict
    case_hash: str
    prompt_snapshot: JSONDict
    prompt_hash: str
    model_snapshot: JSONDict
    model_hash: str
    generation_parameters_snapshot: JSONDict
    rendered_system_prompt: str
    rendered_user_prompt: str
    output_text: str | None
    metric_versions: JSONDict
    metric_directions: JSONDict
    metric_applicability: JSONDict
    metric_results: JSONDict
    aggregate_score: float | None
    aggregate_passed: bool | None
    effective_metric_weight: float
    provider: str | None
    model_name: str | None
    api_mode: ApiMode | None
    request_id: str | None
    finish_reason: str | None
    retry_count: int
    latency_ms: int | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_micro_usd: int | None
    cost_source: str | None
    provider_metadata: JSONDict
    status: ResultStatus
    status_reason: str | None
    state_version: int
    error_type: str | None
    error_message: str | None
    error_retryable: bool | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CalibrationDatasetIdentityRead(StrictSchema):
    id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CalibrationMetricIdentityRead(StrictSchema):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    direction: MetricDirection


class CalibrationConfusionMatrixRead(StrictSchema):
    true_positive: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)


class CalibrationReportRead(StrictSchema):
    """Content-minimized immutable calibration evidence."""

    id: UUID
    run_id: UUID
    candidate_id: UUID
    dataset: CalibrationDatasetIdentityRead
    metric: CalibrationMetricIdentityRead
    selected_threshold: float = Field(ge=0, le=1)
    label_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_kind: Literal["offline_statistical_evidence"]
    production_validated: Literal[False]
    sample_size: int = Field(ge=1)
    human_pass_count: int = Field(ge=0)
    human_fail_count: int = Field(ge=0)
    reviewer_count: int = Field(ge=1)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    confusion_matrix: CalibrationConfusionMatrixRead
    created_at: datetime


class CalibrationImportRead(StrictSchema):
    status: Literal["created", "already_exists"]
    report: CalibrationReportRead


class CalibrationReportPage(StrictSchema):
    items: list[CalibrationReportRead]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=100)


class EvaluationRunRead(StrictSchema):
    id: UUID
    workspace_id: UUID
    name: str | None
    dataset_id: UUID
    dataset_snapshot: JSONDict
    dataset_hash: str
    metric_configuration_snapshot: JSONDict
    preflight_snapshot: JSONDict
    application_version: str
    executor_type: str
    requested_by: str | None
    idempotency_key: str | None
    request_hash: str | None
    acknowledge_real_cost: bool
    acknowledge_unknown_cost: bool
    status: RunStatus
    status_reason: str | None
    state_version: int
    total_items: int
    completed_items: int
    succeeded_items: int
    failed_items: int
    error_type: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationRunDetail(EvaluationRunRead):
    candidates: list[RunCandidateRead]
    results: list[EvaluationResultRead]


class EvaluationRunSummary(StrictSchema):
    id: UUID
    workspace_id: UUID
    name: str | None
    dataset_id: UUID
    dataset_hash: str
    application_version: str
    executor_type: str
    status: RunStatus
    status_reason: str | None
    state_version: int
    total_items: int
    completed_items: int
    succeeded_items: int
    failed_items: int
    error_type: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationRunApiDetail(EvaluationRunSummary):
    candidates: list[RunCandidateRead]


class EvaluationRunPreflightRead(StrictSchema):
    dataset_id: UUID
    case_count: int = Field(ge=1)
    prompt_count: int = Field(ge=1)
    model_count: int = Field(ge=1)
    variant_count: int = Field(ge=1)
    provider_call_count: int = Field(ge=1)
    automatic_provider_retries: Literal[0]
    maximum_provider_request_count: int = Field(ge=1)
    max_requested_output_tokens: int = Field(ge=1)
    estimated_input_tokens: int = Field(ge=1)
    input_token_estimate_method: str
    estimated_known_cost_micro_usd: int = Field(ge=0)
    cost_estimate_complete: bool
    real_provider: bool
    real_provider_models: list[str]
    unknown_pricing_models: list[str]
    external_data_transfer_acknowledged: bool
    spend_limit_micro_usd: int | None = Field(default=None, ge=1)
    spend_limit_basis: str
    inapplicable_counts: dict[str, int]
    limits: dict[str, int]


class CommercialPlanRead(StrictSchema):
    code: PlanCode
    name: str
    audience: str
    price_label: str
    features: list[str]
    self_hosted: bool
    available: bool


class WorkspaceEntitlementRead(StrictSchema):
    workspace_id: UUID
    plan_code: PlanCode
    status: EntitlementStatus
    seat_limit: int = Field(ge=1)
    active_memberships: int = Field(ge=0)
    source: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    can_start_runs: bool
    hosted: bool
    commercial_pilot_enabled: bool


class TeamPilotRequestCreate(StrictSchema):
    requested_seats: int = Field(ge=2, le=250)
    evaluation_frequency: EvaluationFrequency
    security_review_required: bool = False


class TeamPilotRequestRead(StrictSchema):
    id: UUID
    workspace_id: UUID
    requested_by_user_id: UUID
    requested_seats: int = Field(ge=2, le=250)
    evaluation_frequency: EvaluationFrequency
    security_review_required: bool
    status: TeamPilotRequestStatus
    created_at: datetime
    updated_at: datetime
    canceled_at: datetime | None = None


class ClientActivationEventCreate(StrictSchema):
    name: Literal[
        "landing",
        "signup",
        "upgrade_view",
    ]
    source: str = Field(default="direct", min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    surface: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")


class ActivationEventRead(StrictSchema):
    id: UUID
    workspace_id: UUID
    actor_user_id: UUID | None = None
    name: ActivationEventName
    source: str
    run_id: UUID | None = None
    created_at: datetime


class BillingEventRead(StrictSchema):
    id: UUID
    workspace_id: UUID
    actor_user_id: UUID | None = None
    provider: str
    event_type: str
    payload_sha256: str = Field(min_length=64, max_length=64)
    created_at: datetime


class CommercialFunnelRead(StrictSchema):
    event_counts: dict[ActivationEventName, int]
    unique_actors: dict[ActivationEventName, int]
    acquisition_sources: dict[str, int]
    activated_runs: int = Field(ge=0)
    activation_duration_sample_size: int = Field(ge=0)
    activation_duration_excluded_actors: int = Field(ge=0)
    activation_duration_p50_seconds: float | None = Field(default=None, ge=0)
    activation_duration_p90_seconds: float | None = Field(default=None, ge=0)
    pending_team_requests: int = Field(ge=0)
    total_team_requests: int = Field(ge=0)
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None


class MetaRead(StrictSchema):
    version: str
    environment: str
    database_backend: str
    executor: str
    auth_mode: str
    registered_adapters: list[str]


class WorkspaceAccessRead(StrictSchema):
    id: UUID
    name: str
    role: str


class SessionRead(StrictSchema):
    user_id: UUID
    display_name: str
    email: str | None = None
    auth_mode: str
    workspaces: list[WorkspaceAccessRead]


class Page(StrictSchema, Generic[PageItemT]):
    items: list[PageItemT]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=500)


class ErrorEnvelope(StrictSchema):
    detail: str
    code: str
    request_id: str | None = None


# Short aliases used by route and dashboard layers.
RunCreate = EvaluationRunCreate
RunRead = EvaluationRunRead
RunDetail = EvaluationRunDetail
ResultRead = EvaluationResultRead
