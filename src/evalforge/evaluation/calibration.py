"""Pure offline threshold calibration over explicitly human-labeled scores."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from evalforge.evaluation.types import MetricDirection

_EVIDENCE_KIND: Final[str] = "offline_statistical_evidence"


@dataclass(frozen=True, slots=True)
class CalibrationLabel:
    """One human decision paired with one previously computed metric score."""

    item_id: str
    score: float
    human_passed: bool

    def __post_init__(self) -> None:
        normalized_id = self.item_id.strip()
        if not normalized_id:
            raise ValueError("item_id cannot be blank")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("score must be a finite number between 0 and 1")
        normalized_score = float(self.score)
        if not math.isfinite(normalized_score) or not 0.0 <= normalized_score <= 1.0:
            raise ValueError("score must be a finite number between 0 and 1")
        if not isinstance(self.human_passed, bool):
            raise ValueError("human_passed must be a boolean")
        object.__setattr__(self, "item_id", normalized_id)
        object.__setattr__(self, "score", 0.0 if normalized_score == 0.0 else normalized_score)


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int

    def as_dict(self) -> dict[str, int]:
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "true_negative": self.true_negative,
            "false_negative": self.false_negative,
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Deterministic offline evidence for one declared threshold."""

    metric_name: str
    metric_version: str
    direction: MetricDirection
    selected_threshold: float
    sample_size: int
    confusion_matrix: ConfusionMatrix
    precision: float
    recall: float
    f1: float
    calibration_set_sha256: str
    evidence_kind: str = _EVIDENCE_KIND
    production_validated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "metric_version": self.metric_version,
            "direction": self.direction.value,
            "selected_threshold": self.selected_threshold,
            "sample_size": self.sample_size,
            "confusion_matrix": self.confusion_matrix.as_dict(),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "calibration_set_sha256": self.calibration_set_sha256,
            "evidence_kind": self.evidence_kind,
            "production_validated": self.production_validated,
        }


def canonical_calibration_bytes(labels: Sequence[CalibrationLabel]) -> bytes:
    """Return order-independent canonical bytes for a labeled calibration set."""

    rows = _normalized_labels(labels)
    payload = [
        {
            "human_passed": label.human_passed,
            "item_id": label.item_id,
            "score": label.score,
        }
        for label in rows
    ]
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def evaluate_threshold(
    labels: Sequence[CalibrationLabel],
    *,
    metric_name: str,
    metric_version: str,
    selected_threshold: float,
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
) -> CalibrationReport:
    """Compare one selected threshold with human labels without calling a model."""

    normalized_name = metric_name.strip()
    normalized_version = metric_version.strip()
    if not normalized_name:
        raise ValueError("metric_name cannot be blank")
    if not normalized_version:
        raise ValueError("metric_version cannot be blank")
    if isinstance(selected_threshold, bool) or not isinstance(selected_threshold, (int, float)):
        raise ValueError("selected_threshold must be a finite number between 0 and 1")
    threshold = float(selected_threshold)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("selected_threshold must be a finite number between 0 and 1")
    threshold = 0.0 if threshold == 0.0 else threshold
    resolved_direction = MetricDirection(direction)
    rows = _normalized_labels(labels)

    true_positive = false_positive = true_negative = false_negative = 0
    for label in rows:
        predicted_passed = (
            label.score <= threshold
            if resolved_direction is MetricDirection.LOWER_IS_BETTER
            else label.score >= threshold
        )
        if predicted_passed and label.human_passed:
            true_positive += 1
        elif predicted_passed:
            false_positive += 1
        elif label.human_passed:
            false_negative += 1
        else:
            true_negative += 1

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2.0 * precision * recall, precision + recall)
    matrix = ConfusionMatrix(
        true_positive=true_positive,
        false_positive=false_positive,
        true_negative=true_negative,
        false_negative=false_negative,
    )
    calibration_hash = hashlib.sha256(canonical_calibration_bytes(rows)).hexdigest()
    return CalibrationReport(
        metric_name=normalized_name,
        metric_version=normalized_version,
        direction=resolved_direction,
        selected_threshold=threshold,
        sample_size=len(rows),
        confusion_matrix=matrix,
        precision=precision,
        recall=recall,
        f1=f1,
        calibration_set_sha256=calibration_hash,
    )


def _normalized_labels(labels: Sequence[CalibrationLabel]) -> tuple[CalibrationLabel, ...]:
    rows = tuple(labels)
    if not rows:
        raise ValueError("calibration requires at least one labeled item")
    if not all(isinstance(label, CalibrationLabel) for label in rows):
        raise ValueError("calibration rows must be CalibrationLabel instances")
    ordered = tuple(sorted(rows, key=lambda label: label.item_id))
    identifiers = [label.item_id for label in ordered]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("calibration item_id values must be unique")
    return ordered


def _ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)
