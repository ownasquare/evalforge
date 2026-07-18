"""Minimal declared offline evaluator example."""

from __future__ import annotations

from evalforge.evaluation.evaluators import (
    EvaluatorCostBehavior,
    EvaluatorDeclaration,
    EvaluatorExecution,
)
from evalforge.evaluation.types import (
    EvaluationCase,
    MetricDirection,
    MetricResult,
    MetricStatus,
)


class WordLimitEvaluator:
    """Pass outputs that stay within a configured word limit."""

    declaration = EvaluatorDeclaration(
        name="word_limit",
        version="word-count-v1",
        execution=EvaluatorExecution.OFFLINE,
        calls_per_case=0,
        cost_behavior=EvaluatorCostBehavior.NONE,
        transmitted_fields=(),
    )

    def __init__(self, *, maximum_words: int = 30) -> None:
        if maximum_words < 1:
            raise ValueError("maximum_words must be positive")
        self.maximum_words = maximum_words

    async def evaluate(self, case: EvaluationCase) -> MetricResult:
        word_count = len(case.output.split())
        passed = word_count <= self.maximum_words
        return MetricResult(
            name=self.declaration.name,
            version=self.declaration.version,
            score=1.0 if passed else 0.0,
            status=MetricStatus.APPLICABLE,
            threshold=1.0,
            passed=passed,
            reason="Counted output words locally.",
            evidence={"word_count": word_count, "maximum_words": self.maximum_words},
            direction=MetricDirection.HIGHER_IS_BETTER,
        )
