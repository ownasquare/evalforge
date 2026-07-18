from __future__ import annotations

import math

import pytest

from evalforge.evaluation.metrics import MetricRegistry, aggregate_metric_results
from evalforge.evaluation.text import split_sentences
from evalforge.evaluation.types import (
    EvaluationCase,
    MetricDirection,
    MetricStatus,
    OutputConstraints,
)


@pytest.fixture
def registry() -> MetricRegistry:
    return MetricRegistry()


def test_multiline_bullets_are_distinct_claims() -> None:
    assert split_sentences("- Paris is in France\n- Berlin is in Germany") == (
        "Paris is in France",
        "Berlin is in Germany",
    )


def test_groundedness_selects_and_reports_the_best_context_chunk(
    registry: MetricRegistry,
) -> None:
    result = registry.score_groundedness(
        output="Paris is the capital of France.",
        context=(
            "A deliberately unrelated chunk about ocean currents and weather.",
            "Paris is the capital of France.",
        ),
    )

    assert result.score == 1.0
    assert result.evidence["claim_support"][0]["best_context_chunk_index"] == 1


def test_correctness_exact_match_and_missing_reference(registry: MetricRegistry) -> None:
    exact = registry.score_correctness(output="  PARIS! ", reference="Paris")
    missing = registry.score_correctness(output="Paris", reference=None)

    assert exact.score == 1.0
    assert exact.status == MetricStatus.APPLICABLE
    assert exact.evidence["exact_match"] is True
    assert missing.status == MetricStatus.NOT_APPLICABLE
    assert missing.score is None
    assert missing.passed is None


def test_relevance_uses_keywords_before_fallback_targets(registry: MetricRegistry) -> None:
    relevant = registry.score_relevance(
        output="Refunds are available within thirty days.",
        input_text="What is the returns policy?",
        reference="A refund is available within 30 days.",
        relevance_keywords=("refund", "thirty days"),
    )
    irrelevant = registry.score_relevance(
        output="The weather is sunny.",
        input_text="What is the returns policy?",
        reference="A refund is available within 30 days.",
        relevance_keywords=("refund", "thirty days"),
    )

    assert relevant.score is not None
    assert irrelevant.score is not None
    assert relevant.score > irrelevant.score
    assert relevant.evidence["target_source"] == "relevance_keywords"


def test_groundedness_and_hallucination_are_not_applicable_without_context(
    registry: MetricRegistry,
) -> None:
    groundedness = registry.score_groundedness(output="Paris", context=None)
    hallucination = registry.score_hallucination_risk(output="Paris", context=None)

    assert groundedness.status == MetricStatus.NOT_APPLICABLE
    assert groundedness.score is None
    assert hallucination.status == MetricStatus.NOT_APPLICABLE
    assert hallucination.score is None


def test_hallucination_risk_distinctly_flags_unsupported_number(
    registry: MetricRegistry,
) -> None:
    supported_output = "Revenue grew 12 percent."
    unsupported_output = "Revenue grew 91 percent."
    context = "Revenue grew 12 percent."

    grounded = registry.score_groundedness(output=unsupported_output, context=context)
    supported_risk = registry.score_hallucination_risk(
        output=supported_output,
        context=context,
    )
    unsupported_risk = registry.score_hallucination_risk(
        output=unsupported_output,
        context=context,
    )

    assert grounded.score is not None and grounded.score < 0.5
    assert grounded.evidence["unsupported_numbers"] == ["91"]
    assert supported_risk.score == 0.0
    assert unsupported_risk.score is not None and unsupported_risk.score > 0.0
    assert unsupported_risk.evidence["unsupported_numbers"] == ["91"]
    assert unsupported_risk.direction == MetricDirection.LOWER_IS_BETTER
    assert unsupported_risk.score != pytest.approx(1.0 - grounded.score)


def test_hallucination_risk_flags_unsupported_urls(registry: MetricRegistry) -> None:
    result = registry.score_hallucination_risk(
        output="Read https://invented.example/report for details.",
        context="The report is available at https://trusted.example/report.",
    )

    assert result.score is not None and result.score > 0.0
    assert result.evidence["unsupported_urls"] == ["https://invented.example/report"]


def test_phrase_coverage_is_explainable_and_not_applicable_when_unconfigured(
    registry: MetricRegistry,
) -> None:
    partial = registry.score_phrase_coverage(
        output="You may request a refund.",
        required_phrases=("refund", "thirty days"),
    )
    missing = registry.score_phrase_coverage(output="Anything", required_phrases=())

    assert partial.score == 0.5
    assert partial.evidence["matched_phrases"] == ["refund"]
    assert partial.evidence["missing_phrases"] == ["thirty days"]
    assert missing.status == MetricStatus.NOT_APPLICABLE


def test_json_validity_supports_optional_schema(registry: MetricRegistry) -> None:
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }
    valid = registry.score_json_validity(
        output='{"answer": "Paris"}',
        required=True,
        schema=schema,
    )
    schema_failure = registry.score_json_validity(
        output='{"answer": 7}',
        required=True,
        schema=schema,
    )
    syntax_failure = registry.score_json_validity(output="{broken", required=True)
    unconfigured = registry.score_json_validity(output="{}")

    assert valid.score == 1.0
    assert valid.evidence["schema_valid"] is True
    assert schema_failure.score == 0.0
    assert schema_failure.evidence["schema_valid"] is False
    assert syntax_failure.score == 0.0
    assert syntax_failure.evidence["json_valid"] is False
    assert unconfigured.status == MetricStatus.NOT_APPLICABLE


def test_constraint_and_style_adherence_return_named_violations(
    registry: MetricRegistry,
) -> None:
    constraints = OutputConstraints(
        max_words=5,
        max_sentences=1,
        required_prefix="Answer:",
        forbidden_phrases=("guaranteed",),
    )
    constrained = registry.score_constraint_adherence(
        output="This response is guaranteed to be much too long. Another sentence.",
        constraints=constraints,
    )
    style = registry.score_style_adherence(
        output="Certainly! As an AI language model, very very useful advice follows."
    )

    assert constrained.score == 0.0
    assert set(constrained.evidence["violations"]) == {
        "max_words",
        "max_sentences",
        "required_prefix",
        "forbidden_phrase:guaranteed",
    }
    assert style.score is not None and style.score < 1.0
    assert "boilerplate" in style.evidence["violations"]
    assert "repeated_consecutive_word" in style.evidence["violations"]


def test_registry_evaluate_is_bounded_finite_and_versioned(registry: MetricRegistry) -> None:
    case = EvaluationCase(
        input_text="Where is Paris?",
        output="Paris is in France.",
        reference="Paris is in France.",
        context=("Paris is the capital of France.",),
        required_phrases=("France",),
        expects_json=False,
        constraints=OutputConstraints(max_words=12),
    )

    results = registry.evaluate(case)

    assert results
    assert len({result.name for result in results}) == len(results)
    for result in results:
        assert result.version
        if result.score is not None:
            assert 0.0 <= result.score <= 1.0
            assert math.isfinite(result.score)


def test_aggregate_uses_applicable_metrics_and_inverts_risk_once(
    registry: MetricRegistry,
) -> None:
    correctness = registry.score_correctness(output="Paris", reference="Paris")
    risk = registry.score_hallucination_risk(
        output="Paris is in France.",
        context="Paris is in France.",
    )
    not_applicable = registry.score_json_validity(output="plain text")

    aggregate = aggregate_metric_results(
        (correctness, risk, not_applicable),
        weights={"correctness": 2.0, "hallucination_risk": 1.0},
    )

    assert aggregate.score == 1.0
    assert aggregate.status == MetricStatus.APPLICABLE
    assert aggregate.evidence["effective_denominator"] == 3.0
    assert aggregate.evidence["included_metrics"] == [
        "correctness",
        "hallucination_risk",
    ]
    assert aggregate.evidence["excluded_metrics"] == ["json_validity"]
    assert aggregate.evidence["oriented_scores"]["hallucination_risk"] == 1.0


def test_aggregate_is_not_applicable_when_every_metric_is_unavailable(
    registry: MetricRegistry,
) -> None:
    aggregate = aggregate_metric_results(
        (
            registry.score_correctness(output="x", reference=None),
            registry.score_groundedness(output="x", context=None),
        )
    )

    assert aggregate.status == MetricStatus.NOT_APPLICABLE
    assert aggregate.score is None
    assert aggregate.evidence["effective_denominator"] == 0.0
