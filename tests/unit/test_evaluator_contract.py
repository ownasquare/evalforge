from __future__ import annotations

import pytest

from evalforge.evaluation.evaluators import (
    AsyncEvaluator,
    EvaluatorContractError,
    EvaluatorCostBehavior,
    EvaluatorDataField,
    EvaluatorDeclaration,
    EvaluatorExecution,
    run_evaluator,
)
from evalforge.evaluation.types import (
    EvaluationCase,
    MetricDirection,
    MetricResult,
    MetricStatus,
)


class OfflineLengthEvaluator:
    declaration = EvaluatorDeclaration(
        name="length_check",
        version="offline-v1",
        execution=EvaluatorExecution.OFFLINE,
        calls_per_case=0,
        cost_behavior=EvaluatorCostBehavior.NONE,
        transmitted_fields=(),
    )

    async def evaluate(self, case: EvaluationCase) -> MetricResult:
        score = 1.0 if len(case.output) <= 20 else 0.0
        return MetricResult(
            name="length_check",
            version="offline-v1",
            score=score,
            status=MetricStatus.APPLICABLE,
            threshold=1.0,
            passed=score == 1.0,
            reason="Checked output length locally.",
            direction=MetricDirection.HIGHER_IS_BETTER,
        )


class MismatchedEvaluator(OfflineLengthEvaluator):
    async def evaluate(self, case: EvaluationCase) -> MetricResult:
        result = await super().evaluate(case)
        return MetricResult(
            name="different_name",
            version=result.version,
            score=result.score,
            status=result.status,
            threshold=result.threshold,
            passed=result.passed,
            reason=result.reason,
            direction=result.direction,
        )


@pytest.mark.asyncio
async def test_async_evaluator_protocol_validates_declared_identity() -> None:
    evaluator = OfflineLengthEvaluator()
    case = EvaluationCase(input_text="Be concise", output="Short answer")

    assert isinstance(evaluator, AsyncEvaluator)
    result = await run_evaluator(evaluator, case)

    assert result.name == evaluator.declaration.name
    assert result.version == evaluator.declaration.version
    assert result.passed is True


@pytest.mark.asyncio
async def test_evaluator_result_cannot_silently_change_declared_identity() -> None:
    with pytest.raises(EvaluatorContractError, match="declared name"):
        await run_evaluator(
            MismatchedEvaluator(),
            EvaluationCase(input_text="Question", output="Answer"),
        )


def test_external_evaluator_declares_calls_cost_and_transmitted_fields() -> None:
    declaration = EvaluatorDeclaration(
        name="judge",
        version="rubric-sha256-v1",
        execution=EvaluatorExecution.EXTERNAL,
        calls_per_case=1,
        cost_behavior=EvaluatorCostBehavior.BOUNDED,
        max_cost_micro_usd_per_case=2_500,
        transmitted_fields=(
            EvaluatorDataField.INPUT,
            EvaluatorDataField.OUTPUT,
            EvaluatorDataField.REFERENCE,
        ),
    )

    assert declaration.requires_external_transfer is True
    assert declaration.max_cost_micro_usd_per_case == 2_500


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "name": "offline-with-call",
            "version": "v1",
            "execution": EvaluatorExecution.OFFLINE,
            "calls_per_case": 1,
            "cost_behavior": EvaluatorCostBehavior.NONE,
            "transmitted_fields": (),
        },
        {
            "name": "external-without-call",
            "version": "v1",
            "execution": EvaluatorExecution.EXTERNAL,
            "calls_per_case": 0,
            "cost_behavior": EvaluatorCostBehavior.UNKNOWN,
            "transmitted_fields": (EvaluatorDataField.OUTPUT,),
        },
        {
            "name": "bounded-without-bound",
            "version": "v1",
            "execution": EvaluatorExecution.EXTERNAL,
            "calls_per_case": 1,
            "cost_behavior": EvaluatorCostBehavior.BOUNDED,
            "transmitted_fields": (EvaluatorDataField.OUTPUT,),
        },
        {
            "name": "offline-transmission",
            "version": "v1",
            "execution": EvaluatorExecution.OFFLINE,
            "calls_per_case": 0,
            "cost_behavior": EvaluatorCostBehavior.NONE,
            "transmitted_fields": (EvaluatorDataField.OUTPUT,),
        },
    ],
)
def test_invalid_evaluator_declarations_fail_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        EvaluatorDeclaration(**kwargs)
