from __future__ import annotations

from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

from evalforge.dashboard.client import ApiClient, ApiError
from evalforge.dashboard.pages.run_detail import (
    _candidate_labels,
    _candidate_options,
    _case_identity,
    _load_all_results,
    _metric_scorecard_rows,
)

RUN_DETAIL_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.run_detail import render
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge run detail test", layout="wide")
initialize_state()
render()
"""


class _PaginatedResultsApi:
    def __init__(self, *, fail_page: int | None = None) -> None:
        self.fail_page = fail_page
        self.calls: list[tuple[int, int]] = []

    def run_results(
        self,
        _: str,
        *,
        limit: int,
        page: int,
    ) -> dict[str, Any]:
        self.calls.append((page, limit))
        if page == self.fail_page:
            raise ApiError("result page unavailable", retryable=True)
        start = (page - 1) * limit
        count = max(0, min(limit, 1_001 - start))
        return {
            "items": [{"id": f"result-{start + index}"} for index in range(count)],
            "total": 1_001,
            "page": page,
        }


def test_load_all_results_follows_api_pagination_total() -> None:
    api = _PaginatedResultsApi()

    results, total, error = _load_all_results(api, "run-1")

    assert error is None
    assert total == 1_001
    assert len(results) == 1_001
    assert api.calls == [(1, 500), (2, 500), (3, 500)]


def test_load_all_results_reports_partial_page_failure() -> None:
    api = _PaginatedResultsApi(fail_page=3)

    results, total, error = _load_all_results(api, "run-1")

    assert isinstance(error, ApiError)
    assert total == 1_001
    assert len(results) == 1_000


def test_candidate_ids_join_to_immutable_run_candidate_labels() -> None:
    run = {
        "candidates": [
            {
                "id": "candidate-1",
                "label": "Grounded answer / Demo Reliable",
            }
        ]
    }
    results = [
        {
            "run_candidate_id": "candidate-1",
            "model_name": "Mutable fallback name",
        }
    ]

    labels = _candidate_labels(run)

    assert labels == {"candidate-1": "Grounded answer / Demo Reliable"}
    assert _candidate_options(results, labels) == {"candidate-1": "Grounded answer / Demo Reliable"}


def test_case_identity_prefers_the_immutable_input_snapshot() -> None:
    result = {
        "test_case_id": "database-case-id",
        "input_snapshot": {
            "external_id": "refund-policy",
            "input": "Can I request a refund?",
        },
    }

    assert _case_identity(result, fallback="Case 7") == "refund-policy"
    assert _case_identity({"test_case_id": "case-42"}, fallback="Case 7") == "case-42"
    assert _case_identity({}, fallback="Case 7") == "Case 7"


def test_metric_scorecard_keeps_direction_target_and_applicable_denominator() -> None:
    run = {
        "metric_configuration_snapshot": {
            "metrics": [
                {
                    "name": "correctness",
                    "direction": "higher_is_better",
                    "threshold": 0.8,
                },
                {
                    "name": "hallucination_risk",
                    "direction": "lower_is_better",
                    "threshold": 0.2,
                },
            ]
        }
    }
    results = [
        {
            "metric_results": {
                "correctness": {
                    "score": 0.9,
                    "direction": "higher_is_better",
                    "threshold": 0.8,
                    "applicability": "applicable",
                },
                "hallucination_risk": {
                    "score": 0.1,
                    "direction": "lower_is_better",
                    "threshold": 0.2,
                    "applicability": "applicable",
                },
                "aggregate_quality": {"score": 0.9},
            }
        },
        {
            "metric_results": {
                "correctness": {
                    "score": 0.8,
                    "direction": "higher_is_better",
                    "threshold": 0.8,
                    "applicability": "applicable",
                },
                "hallucination_risk": {
                    "score": None,
                    "direction": "lower_is_better",
                    "threshold": 0.2,
                    "applicability": "not_applicable",
                },
            }
        },
    ]

    rows = _metric_scorecard_rows(run, {}, results)

    assert rows == [
        {
            "Metric": "Correctness",
            "Mean score": "0.850",
            "Direction": "Higher is better",
            "Target": "at least 0.800",
            "Applicable results": "2 / 2",
        },
        {
            "Metric": "Hallucination risk",
            "Mean score": "0.100",
            "Direction": "Lower is better",
            "Target": "at most 0.200",
            "Applicable results": "1 / 2",
        },
    ]


def test_render_shows_directional_scorecard(
    monkeypatch,
) -> None:
    result = {
        "id": "result-1",
        "run_candidate_id": "candidate-1",
        "test_case_id": "case-1",
        "status": "completed",
        "input_snapshot": {
            "external_id": "refund-policy",
            "input": "Can I request a refund?",
            "expected_output": "Yes, within 30 days.",
            "context": "Refunds are accepted within 30 days.",
        },
        "output_text": "Yes, within 30 days.",
        "aggregate_score": 0.9,
        "aggregate_passed": True,
        "latency_ms": 25,
        "estimated_cost_micro_usd": 0,
        "metric_results": {
            "correctness": {
                "name": "correctness",
                "score": 0.9,
                "direction": "higher_is_better",
                "threshold": 0.8,
                "applicability": "applicable",
                "passed": True,
                "reason": "Matches the reference.",
                "evidence": {},
            },
            "hallucination_risk": {
                "name": "hallucination_risk",
                "score": 0.1,
                "direction": "lower_is_better",
                "threshold": 0.2,
                "applicability": "applicable",
                "passed": True,
                "reason": "All claims are supported.",
                "evidence": {},
            },
        },
    }
    routes: dict[str, Any] = {
        "/api/v1/runs": {
            "items": [
                {
                    "id": "run-1",
                    "name": "Refund regression",
                    "status": "completed",
                    "created_at": "2026-07-17T20:00:00Z",
                }
            ]
        },
        "/api/v1/runs/run-1": {
            "id": "run-1",
            "name": "Refund regression",
            "status": "completed",
            "created_at": "2026-07-17T20:00:00Z",
            "completed_items": 1,
            "total_items": 1,
            "candidates": [
                {
                    "id": "candidate-1",
                    "label": "Grounded answer / Demo Reliable",
                }
            ],
        },
        "/api/v1/runs/run-1/results": {"items": [result], "total": 1},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes))
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert "Metric scorecard" in [element.value for element in app.subheader]
    scorecard = app.dataframe[0].value.to_dict(orient="records")
    assert scorecard[1]["Direction"] == "Lower is better"
    assert scorecard[1]["Target"] == "at most 0.200"


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
