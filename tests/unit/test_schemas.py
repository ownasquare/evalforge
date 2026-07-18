from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from evalforge.evaluation.metrics import MetricRegistry
from evalforge.schemas import (
    MetricConfiguration,
    PromptTemplateCreate,
)
from evalforge.schemas import TestCaseCreate as CaseCreate


def test_case_constraints_are_typed_and_normalized() -> None:
    case = CaseCreate(
        external_id="typed",
        input_text="Answer",
        constraints_json={
            "min_words": 2,
            "max_words": 5,
            "forbidden_phrases": [" guess ", "guess"],
        },
        metadata_json={"relevance_keywords": [" answer ", "answer"]},
    )

    assert case.constraints_json["forbidden_phrases"] == ["guess"]
    assert case.metadata_json["relevance_keywords"] == ["answer"]


@pytest.mark.parametrize(
    "constraints",
    [
        {"min_words": "many"},
        {"min_words": 10, "max_words": 2},
        {"forbidden_phrases": "not-a-list"},
        {"json_schema": {"$ref": "http://127.0.0.1/internal-schema"}},
    ],
)
def test_invalid_constraints_fail_before_execution(constraints: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CaseCreate(external_id="invalid", input_text="Answer", constraints_json=constraints)


def test_external_json_schema_reference_never_resolves() -> None:
    result = MetricRegistry().score_json_validity(
        output='{"value": 1}',
        required=True,
        schema={"$ref": "http://127.0.0.1/internal-schema"},
    )

    assert result.status.value == "error"
    assert result.evidence["error_category"] == "external_schema_reference"


def test_non_finite_metric_weights_are_rejected() -> None:
    with pytest.raises(ValidationError):
        MetricConfiguration(
            name="correctness",
            version="lexical-correctness-v1",
            weight=math.inf,
        )


def test_prompt_placeholder_expansion_is_bounded() -> None:
    template = " ".join("{context}" for _ in range(21))
    with pytest.raises(ValidationError):
        PromptTemplateCreate(name="Too many expansions", user_template=template)
