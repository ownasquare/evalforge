"""Provider-neutral, persistence-neutral evaluation value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class MetricStatus(StrEnum):
    """Whether a metric produced a meaningful score."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class MetricDirection(StrEnum):
    """How a metric should be oriented for comparison and aggregation."""

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class ApiMode(StrEnum):
    """Explicit provider API surface selected for one generation."""

    DEMO = "demo"
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


@dataclass(frozen=True, slots=True)
class OutputConstraints:
    """Machine-checkable output constraints supplied by an evaluation case."""

    min_words: int | None = None
    max_words: int | None = None
    min_sentences: int | None = None
    max_sentences: int | None = None
    required_prefix: str | None = None
    required_suffix: str | None = None
    forbidden_phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("min_words", "max_words", "min_sentences", "max_sentences"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
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

    @property
    def configured(self) -> bool:
        """Return whether at least one explicit rule is active."""

        return any(
            (
                self.min_words is not None,
                self.max_words is not None,
                self.min_sentences is not None,
                self.max_sentences is not None,
                self.required_prefix is not None,
                self.required_suffix is not None,
                bool(self.forbidden_phrases),
            )
        )


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """All evidence needed to score one generated output."""

    input_text: str
    output: str
    reference: str | None = None
    context: str | tuple[str, ...] | None = None
    relevance_keywords: tuple[str, ...] = ()
    required_phrases: tuple[str, ...] = ()
    expects_json: bool = False
    json_schema: Mapping[str, Any] | None = None
    constraints: OutputConstraints = field(default_factory=OutputConstraints)


@dataclass(frozen=True, slots=True)
class MetricResult:
    """Versioned, explainable result from one evaluation metric."""

    name: str
    version: str
    score: float | None
    status: MetricStatus
    threshold: float | None
    passed: bool | None
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Metric name cannot be blank")
        if not self.version.strip():
            raise ValueError("Metric version cannot be blank")
        if self.status == MetricStatus.APPLICABLE:
            if self.score is None:
                raise ValueError("Applicable metrics require a score")
            if not 0.0 <= self.score <= 1.0:
                raise ValueError("Metric scores must be between 0 and 1")
        elif self.score is not None:
            raise ValueError("Unavailable metrics cannot carry a score")
        if self.score is None and self.passed is not None:
            raise ValueError("Unavailable metrics cannot carry a pass result")

    @property
    def applicable(self) -> bool:
        return self.status == MetricStatus.APPLICABLE


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """A generation request intentionally incapable of carrying credentials."""

    model: str
    api_mode: ApiMode
    system_prompt: str
    user_prompt: str
    temperature: float = 0.0
    max_output_tokens: int = 512
    seed: int = 0
    expected_output: str | None = None
    context: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Model cannot be blank")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True, slots=True)
class GenerationResponse:
    """Normalized generation response shared by all provider adapters."""

    text: str
    provider: str
    model: str
    api_mode: ApiMode
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    request_id: str
    finish_reason: str | None
    retry_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens", "latency_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


class ProviderError(RuntimeError):
    """Sanitized provider failure safe for persistence and API responses."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.attempts = attempts


@runtime_checkable
class ModelAdapter(Protocol):
    """Adapter contract consumed by the run service."""

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate exactly once through the request's selected API mode."""
