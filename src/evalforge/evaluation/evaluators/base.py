"""Provider-neutral contracts for offline and external evaluators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from evalforge.evaluation.types import EvaluationCase, MetricResult


class EvaluatorExecution(StrEnum):
    OFFLINE = "offline"
    EXTERNAL = "external"


class EvaluatorCostBehavior(StrEnum):
    NONE = "none"
    BOUNDED = "bounded"
    UNKNOWN = "unknown"


class EvaluatorDataField(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    REFERENCE = "reference"
    CONTEXT = "context"


class EvaluatorContractError(RuntimeError):
    """Raised when an evaluator violates its immutable declaration."""


@dataclass(frozen=True, slots=True)
class EvaluatorDeclaration:
    """Immutable call, cost, and data-disclosure behavior for one evaluator."""

    name: str
    version: str
    execution: EvaluatorExecution
    calls_per_case: int
    cost_behavior: EvaluatorCostBehavior
    transmitted_fields: tuple[EvaluatorDataField, ...]
    max_cost_micro_usd_per_case: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "version", self.version.strip())
        object.__setattr__(self, "execution", EvaluatorExecution(self.execution))
        object.__setattr__(self, "cost_behavior", EvaluatorCostBehavior(self.cost_behavior))
        object.__setattr__(
            self,
            "transmitted_fields",
            tuple(EvaluatorDataField(field) for field in self.transmitted_fields),
        )
        self.validate()

    @property
    def requires_external_transfer(self) -> bool:
        return self.execution is EvaluatorExecution.EXTERNAL

    def validate(self) -> None:
        if not self.name:
            raise ValueError("evaluator name cannot be blank")
        if not self.version:
            raise ValueError("evaluator version cannot be blank")
        if isinstance(self.calls_per_case, bool) or not isinstance(self.calls_per_case, int):
            raise ValueError("calls_per_case must be a non-negative integer")
        if self.calls_per_case < 0:
            raise ValueError("calls_per_case must be a non-negative integer")
        if len(self.transmitted_fields) != len(set(self.transmitted_fields)):
            raise ValueError("transmitted_fields cannot contain duplicates")
        maximum_cost = self.max_cost_micro_usd_per_case
        if maximum_cost is not None and (
            isinstance(maximum_cost, bool) or not isinstance(maximum_cost, int) or maximum_cost < 0
        ):
            raise ValueError("max_cost_micro_usd_per_case must be a non-negative integer")

        if self.execution is EvaluatorExecution.OFFLINE:
            if self.calls_per_case != 0:
                raise ValueError("offline evaluators cannot declare external calls")
            if self.transmitted_fields:
                raise ValueError("offline evaluators cannot transmit fields")
            if self.cost_behavior is not EvaluatorCostBehavior.NONE or maximum_cost is not None:
                raise ValueError("offline evaluators must declare no external cost")
        else:
            if self.calls_per_case < 1:
                raise ValueError("external evaluators must declare at least one call per case")
            if not self.transmitted_fields:
                raise ValueError("external evaluators must declare transmitted fields")

        if self.cost_behavior is EvaluatorCostBehavior.BOUNDED and maximum_cost is None:
            raise ValueError("bounded evaluator cost requires max_cost_micro_usd_per_case")
        if self.cost_behavior is not EvaluatorCostBehavior.BOUNDED and maximum_cost is not None:
            raise ValueError("only bounded evaluator cost may declare a maximum")


@runtime_checkable
class AsyncEvaluator(Protocol):
    @property
    def declaration(self) -> EvaluatorDeclaration: ...

    async def evaluate(self, case: EvaluationCase) -> MetricResult: ...


async def run_evaluator(evaluator: AsyncEvaluator, case: EvaluationCase) -> MetricResult:
    """Execute one evaluator and enforce its declared metric identity."""

    declaration = evaluator.declaration
    if not isinstance(declaration, EvaluatorDeclaration):
        raise EvaluatorContractError("evaluator declaration has an invalid type")
    result = await evaluator.evaluate(case)
    if not isinstance(result, MetricResult):
        raise EvaluatorContractError("evaluator did not return a MetricResult")
    if result.name != declaration.name:
        raise EvaluatorContractError("evaluator result does not match its declared name")
    if result.version != declaration.version:
        raise EvaluatorContractError("evaluator result does not match its declared version")
    return result
