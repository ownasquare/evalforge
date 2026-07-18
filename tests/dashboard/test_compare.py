from __future__ import annotations

from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

from evalforge.dashboard.client import ApiClient
from evalforge.dashboard.pages.compare import (
    _candidate_label_map,
    _case_evidence_rows,
    _pairwise_summary_rows,
)

COMPARE_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.compare import render
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge comparison test", layout="wide")
initialize_state()
render()
"""


def _comparison() -> dict[str, object]:
    return {
        "baseline_candidate_id": "baseline",
        "candidates": [
            {
                "candidate_id": "baseline",
                "label": "Control prompt / Demo Reliable",
            },
            {
                "candidate_id": "challenger",
                "label": "Concise prompt / Demo Fast",
            },
        ],
        "paired_comparisons": [
            {
                "baseline_candidate_id": "baseline",
                "challenger_candidate_id": "challenger",
                "paired_cases": 4,
                "mean_delta": 0.075,
                "wins": 2,
                "ties": 1,
                "losses": 1,
            }
        ],
    }


def test_pairwise_summary_uses_candidate_labels_and_explicit_denominators() -> None:
    comparison = _comparison()
    labels = _candidate_label_map(comparison["candidates"])

    assert _pairwise_summary_rows(comparison, labels) == [
        {
            "Baseline": "Control prompt / Demo Reliable",
            "Challenger": "Concise prompt / Demo Fast",
            "Paired cases": "4",
            "Mean quality delta": "+0.075",
            "Challenger wins": "2 / 4 (50.0%)",
            "Ties": "1 / 4 (25.0%)",
            "Challenger regressions": "1 / 4 (25.0%)",
        }
    ]


def test_case_evidence_places_regressions_first_and_resolves_candidate_labels() -> None:
    comparison = _comparison()
    comparison["paired_case_deltas"] = [
        {
            "case_name": "improvement",
            "baseline_candidate_id": "baseline",
            "challenger_candidate_id": "challenger",
            "baseline_score": 0.5,
            "challenger_score": 0.8,
            "delta": 0.3,
        },
        {
            "case_external_id": "regression",
            "baseline_candidate_id": "baseline",
            "challenger_candidate_id": "challenger",
            "baseline_score": 0.9,
            "challenger_score": 0.6,
            "delta": -0.3,
        },
        {
            "case_name": "tie",
            "baseline_candidate_id": "baseline",
            "challenger_candidate_id": "challenger",
            "baseline_score": 0.7,
            "challenger_score": 0.7,
            "delta": 0.0,
        },
    ]
    labels = _candidate_label_map(comparison["candidates"])

    rows = _case_evidence_rows(comparison, labels)

    assert [row["Case"] for row in rows] == ["regression", "improvement", "tie"]
    assert rows[0] == {
        "Case": "regression",
        "Baseline": "Control prompt / Demo Reliable",
        "Challenger": "Concise prompt / Demo Fast",
        "Baseline score": "0.900",
        "Challenger score": "0.600",
        "Delta": "-0.300",
        "Outcome": "Regression",
    }


def test_pairwise_aggregates_are_not_relabelled_as_case_evidence() -> None:
    comparison = _comparison()
    labels = _candidate_label_map(comparison["candidates"])

    assert _case_evidence_rows(comparison, labels) == []


def test_render_ignores_invented_winner_and_confidence_fields(monkeypatch) -> None:
    comparison = _comparison()
    comparison.update(
        {
            "winner_name": "Concise prompt / Demo Fast",
            "winner_confidence": 0.99,
            "paired_case_deltas": [
                {
                    "case_external_id": "refund-policy",
                    "baseline_candidate_id": "baseline",
                    "challenger_candidate_id": "challenger",
                    "baseline_score": 0.8,
                    "challenger_score": 0.7,
                    "delta": -0.1,
                    "outcome": "loss",
                }
            ],
        }
    )
    routes: dict[str, Any] = {
        "/api/v1/runs": {
            "items": [
                {
                    "id": "run-1",
                    "name": "Candidate review",
                    "status": "completed",
                    "created_at": "2026-07-17T20:00:00Z",
                }
            ]
        },
        "/api/v1/runs/run-1/comparison": comparison,
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes))
    app = AppTest.from_string(COMPARE_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert "Pairwise summary" in [element.value for element in app.subheader]
    assert "Case evidence" in [element.value for element in app.subheader]
    assert len(app.dataframe) == 2
    visible_copy = [
        str(element.value)
        for element in [*app.caption, *app.text, *app.info, *app.warning, *app.success]
    ]
    assert any("Baseline · Control prompt / Demo Reliable" in value for value in visible_copy)
    assert any("do not select or automatically promote" in value for value in visible_copy)
    assert not any("confidence" in value.lower() for value in visible_copy)
    assert not any("leading candidate" in value.lower() for value in visible_copy)


def _fake_transport(routes: dict[str, Any]):
    def request_response(
        _: ApiClient,
        _method: str,
        path: str,
        **_kwargs: Any,
    ) -> httpx.Response:
        payload = routes.get(path)
        if payload is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=payload)

    return request_response
