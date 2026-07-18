from __future__ import annotations

import pytest

from evalforge.dashboard.pages.run_evaluation import (
    _baseline_first,
    _scoring_payload,
    _scoring_rows,
    _selectable_models,
)
from evalforge.dashboard.pages.test_cases import _criteria_payload, _split_terms


def test_selected_baseline_is_first_without_losing_candidates() -> None:
    assert _baseline_first(["prompt-a", "prompt-b", "prompt-c"], "prompt-b") == [
        "prompt-b",
        "prompt-a",
        "prompt-c",
    ]
    assert _baseline_first(["prompt-a", "prompt-b"], "missing") == [
        "prompt-a",
        "prompt-b",
    ]


def test_scoring_rows_round_trip_visible_metric_policy() -> None:
    capabilities = {
        "metrics": [
            {
                "name": "correctness",
                "version": "correctness-v1",
                "direction": "higher_is_better",
                "weight": 1.0,
                "threshold": 0.7,
                "enabled": True,
            },
            {
                "name": "hallucination_risk",
                "version": "hallucination-v1",
                "direction": "lower_is_better",
                "weight": 1.0,
                "threshold": 0.25,
                "enabled": True,
            },
        ]
    }

    rows = _scoring_rows(capabilities)
    assert [row["Check"] for row in rows] == ["Correctness", "Hallucination risk"]
    rows[1]["Use"] = False
    rows[0]["Pass threshold"] = 0.8

    assert _scoring_payload(rows) == [
        {
            "name": "correctness",
            "version": "correctness-v1",
            "direction": "higher_is_better",
            "weight": 1.0,
            "threshold": 0.8,
            "enabled": True,
        }
    ]


def test_selectable_models_exclude_only_explicitly_paused_profiles() -> None:
    models = [
        {"id": "active", "name": "Active", "enabled": True},
        {"id": "legacy", "name": "Legacy response without availability"},
        {"id": "paused", "name": "Paused", "enabled": False},
    ]

    assert [model["id"] for model in _selectable_models(models)] == [
        "active",
        "legacy",
    ]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    "Use": False,
                    "_metric": "correctness",
                    "_version": "correctness-v1",
                    "_direction": "higher_is_better",
                    "Weight": 1.0,
                    "Pass threshold": 0.7,
                }
            ],
            "at least one scoring check",
        ),
        (
            [
                {
                    "Use": True,
                    "_metric": "correctness",
                    "_version": "correctness-v1",
                    "_direction": "higher_is_better",
                    "Weight": float("nan"),
                    "Pass threshold": 0.7,
                }
            ],
            "finite numbers",
        ),
        (
            [
                {
                    "Use": True,
                    "_metric": "correctness",
                    "_version": "correctness-v1",
                    "_direction": "higher_is_better",
                    "Weight": 1.0,
                    "Pass threshold": 1.1,
                }
            ],
            "between 0 and 1",
        ),
        (
            [
                {
                    "Use": True,
                    "_metric": "correctness",
                    "_version": "correctness-v1",
                    "_direction": "higher_is_better",
                    "Weight": 11.0,
                    "Pass threshold": 0.7,
                }
            ],
            "between 0 and 10",
        ),
        (
            [
                {
                    "Use": True,
                    "_metric": "correctness",
                    "_version": "correctness-v1",
                    "_direction": "higher_is_better",
                    "Weight": 0.0,
                    "Pass threshold": 0.7,
                }
            ],
            "positive total weight",
        ),
    ],
)
def test_scoring_payload_rejects_invalid_or_empty_policies(
    rows: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _scoring_payload(rows)


def test_case_criteria_are_compact_and_validated() -> None:
    assert _split_terms("refund, 30 days\nfull refund") == [
        "refund",
        "30 days",
        "full refund",
    ]
    assert _criteria_payload(
        required_phrases="30 days\nfull refund",
        relevance_keywords="refund, unopened",
        expects_json=True,
        json_schema_text='{"type": "object", "required": ["answer"]}',
    ) == {
        "required_phrases": ["30 days", "full refund"],
        "constraints_json": {
            "expects_json": True,
            "json_schema": {"type": "object", "required": ["answer"]},
        },
        "metadata_json": {"relevance_keywords": ["refund", "unopened"]},
    }


def test_case_criteria_reject_invalid_json_schema() -> None:
    with pytest.raises(ValueError, match="JSON Schema"):
        _criteria_payload(
            required_phrases="",
            relevance_keywords="",
            expects_json=True,
            json_schema_text="not-json",
        )
