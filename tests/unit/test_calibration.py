from __future__ import annotations

import hashlib
import math

import pytest

from evalforge.evaluation.calibration import (
    CalibrationLabel,
    canonical_calibration_bytes,
    evaluate_threshold,
)
from evalforge.evaluation.types import MetricDirection


def _labels() -> list[CalibrationLabel]:
    return [
        CalibrationLabel(item_id="case-a", score=0.9, human_passed=True),
        CalibrationLabel(item_id="case-b", score=0.8, human_passed=False),
        CalibrationLabel(item_id="case-c", score=0.6, human_passed=True),
        CalibrationLabel(item_id="case-d", score=0.2, human_passed=False),
    ]


def test_calibration_report_is_offline_evidence_with_declared_confusion_metrics() -> None:
    report = evaluate_threshold(
        _labels(),
        metric_name="correctness",
        metric_version="lexical-correctness-v1",
        selected_threshold=0.7,
        direction=MetricDirection.HIGHER_IS_BETTER,
    )

    assert report.sample_size == 4
    assert report.confusion_matrix.as_dict() == {
        "true_positive": 1,
        "false_positive": 1,
        "true_negative": 1,
        "false_negative": 1,
    }
    assert report.precision == report.recall == report.f1 == 0.5
    assert report.selected_threshold == 0.7
    assert report.evidence_kind == "offline_statistical_evidence"
    assert report.production_validated is False
    assert (
        report.calibration_set_sha256
        == hashlib.sha256(canonical_calibration_bytes(_labels())).hexdigest()
    )


def test_calibration_hash_is_independent_of_input_order_and_direction_is_respected() -> None:
    labels = [
        CalibrationLabel(item_id="safe", score=0.1, human_passed=True),
        CalibrationLabel(item_id="risky", score=0.8, human_passed=False),
    ]

    forward = canonical_calibration_bytes(labels)
    reverse = canonical_calibration_bytes(list(reversed(labels)))
    report = evaluate_threshold(
        labels,
        metric_name="hallucination_risk",
        metric_version="unsupported-anchor-risk-v2",
        selected_threshold=0.2,
        direction=MetricDirection.LOWER_IS_BETTER,
    )

    assert forward == reverse
    assert report.confusion_matrix.true_positive == 1
    assert report.confusion_matrix.true_negative == 1
    assert report.f1 == 1.0


def test_calibration_uses_zero_for_undefined_rates_without_inventing_evidence() -> None:
    report = evaluate_threshold(
        [CalibrationLabel(item_id="only-negative", score=0.1, human_passed=False)],
        metric_name="correctness",
        metric_version="v1",
        selected_threshold=0.9,
    )

    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0


@pytest.mark.parametrize(
    ("item_id", "score"),
    [
        ("", 0.5),
        ("case", -0.1),
        ("case", 1.1),
        ("case", math.nan),
        ("case", math.inf),
    ],
)
def test_calibration_labels_reject_ambiguous_or_non_finite_input(
    item_id: str, score: float
) -> None:
    with pytest.raises(ValueError):
        CalibrationLabel(item_id=item_id, score=score, human_passed=True)


def test_calibration_rejects_empty_duplicate_or_malformed_configuration() -> None:
    duplicate = [
        CalibrationLabel(item_id="same", score=0.9, human_passed=True),
        CalibrationLabel(item_id="same", score=0.1, human_passed=False),
    ]

    with pytest.raises(ValueError, match="at least one"):
        evaluate_threshold(
            [], metric_name="correctness", metric_version="v1", selected_threshold=0.5
        )
    with pytest.raises(ValueError, match="unique"):
        canonical_calibration_bytes(duplicate)
    with pytest.raises(ValueError, match="metric_name"):
        evaluate_threshold(_labels(), metric_name=" ", metric_version="v1", selected_threshold=0.5)
    with pytest.raises(ValueError, match="selected_threshold"):
        evaluate_threshold(
            _labels(), metric_name="correctness", metric_version="v1", selected_threshold=1.5
        )
