from __future__ import annotations

from typing import Any

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from evalforge.dashboard.client import ApiClient, ApiError
from evalforge.dashboard.pages.common import load_all_runs
from evalforge.dashboard.pages.run_detail import (
    _candidate_labels,
    _candidate_options,
    _case_identity,
    _has_target_miss,
    _load_all_results,
    _metric_scorecard_rows,
    _needs_attention,
    _result_conclusion,
    _result_page,
)

RUN_DETAIL_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.run_detail import render
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge run detail test", layout="wide")
initialize_state()
render()
"""

VIEWER_RUN_DETAIL_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.auth import WorkspaceOption
from evalforge.dashboard.pages.run_detail import render
from evalforge.dashboard.state import (
    configure_client,
    initialize_state,
    select_workspace,
    sync_identity,
)

st.set_page_config(page_title="EvalForge viewer run detail test", layout="wide")
initialize_state()
sync_identity("viewer-fingerprint")
workspace = WorkspaceOption("workspace-1", "Quality", "viewer")
select_workspace(workspace)
configure_client(identity_fingerprint="viewer-fingerprint", workspace_id=workspace.id)
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


class _PaginatedRunsApi:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def runs(self, *, limit: int, page: int) -> dict[str, Any]:
        self.calls.append((page, limit))
        start = (page - 1) * limit
        count = max(0, min(limit, 401 - start))
        return {
            "items": [{"id": f"run-{start + index}"} for index in range(count)],
            "total": 401,
        }


def test_load_all_results_follows_api_pagination_total() -> None:
    api = _PaginatedResultsApi()

    results, total, error = _load_all_results(api, "run-1")

    assert error is None
    assert total == 1_001
    assert len(results) == 1_001
    assert api.calls == [(1, 500), (2, 500), (3, 500)]


def test_load_all_runs_keeps_older_history_selectable() -> None:
    api = _PaginatedRunsApi()

    runs, total, error = load_all_runs(api)

    assert error is None
    assert total == 401
    assert len(runs) == 401
    assert runs[-1]["id"] == "run-400"
    assert api.calls == [(1, 200), (2, 200), (3, 200)]


def test_load_all_results_reports_partial_page_failure() -> None:
    api = _PaginatedResultsApi(fail_page=3)

    results, total, error = _load_all_results(api, "run-1")

    assert isinstance(error, ApiError)
    assert total == 1_001
    assert len(results) == 1_000


def test_result_evidence_pagination_keeps_later_cases_reachable() -> None:
    results = [{"id": f"result-{index}"} for index in range(121)]

    page, first_position, last_position = _result_page(results, 3)

    assert [result["id"] for result in page] == [f"result-{index}" for index in range(100, 121)]
    assert (first_position, last_position) == (101, 121)


def test_result_evidence_pagination_clamps_stale_page_state() -> None:
    results = [{"id": f"result-{index}"} for index in range(7)]

    page, first_position, last_position = _result_page(results, 99)

    assert page == results
    assert (first_position, last_position) == (1, 7)


def test_needs_attention_includes_target_misses_and_execution_errors() -> None:
    assert _needs_attention({"status": "completed", "aggregate_passed": False}) is True
    assert (
        _needs_attention(
            {
                "status": "completed",
                "aggregate_passed": True,
                "metric_results": {"correctness": {"passed": False, "applicability": "applicable"}},
            }
        )
        is True
    )
    assert (
        _needs_attention(
            {
                "status": "completed",
                "aggregate_passed": True,
                "metric_results": {"correctness": {"passed": None, "status": "error"}},
            }
        )
        is True
    )
    assert _needs_attention({"status": "error", "aggregate_passed": None}) is True
    assert _needs_attention({"status": "completed", "error_message": "timeout"}) is True
    assert _needs_attention({"status": "completed", "aggregate_passed": True}) is False


def test_target_miss_ignores_intentionally_non_applicable_metrics() -> None:
    result = {
        "aggregate_passed": True,
        "metric_results": {
            "groundedness": {
                "passed": None,
                "applicability": "not_applicable",
            }
        },
    }

    assert _has_target_miss(result) is False


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


def test_completed_multi_candidate_run_is_ready_for_comparison() -> None:
    run = {
        "id": "run-1",
        "status": "completed",
        "candidates": [
            {"id": "candidate-1", "label": "Baseline"},
            {"id": "candidate-2", "label": "Challenger"},
        ],
    }

    conclusion = _result_conclusion(run, [{"aggregate_passed": True}])

    assert conclusion == (
        "success",
        "Ready to compare candidates",
        "The evaluation finished. Compare the candidates on the same cases before choosing one.",
    )


def test_completed_run_warns_when_a_metric_misses_despite_passing_aggregate() -> None:
    run = {"id": "run-1", "status": "completed", "candidates": []}
    results = [
        {
            "aggregate_passed": True,
            "metric_results": {
                "hallucination_risk": {
                    "passed": False,
                    "applicability": "applicable",
                }
            },
        }
    ]

    assert _result_conclusion(run, results) == (
        "warning",
        "Review target misses",
        "1 scored result missed at least one target. Review those cases before deciding.",
    )


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
        "/api/v1/runs/run-1/calibrations": {"items": [], "total": 0},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes))
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert "Metric scorecard" in [element.value for element in app.subheader]
    scorecard = app.dataframe[0].value.to_dict(orient="records")
    assert scorecard[1]["Direction"] == "Lower is better"
    assert scorecard[1]["Target"] == "at most 0.200"


def test_render_explains_completed_result_and_preserves_run_for_compare(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/runs": {
            "items": [
                {
                    "id": "run-compare",
                    "name": "Release choice",
                    "status": "completed",
                    "created_at": "2026-07-18T20:00:00Z",
                }
            ]
        },
        "/api/v1/runs/run-compare": {
            "id": "run-compare",
            "name": "Release choice",
            "status": "completed",
            "completed_items": 2,
            "total_items": 2,
            "candidates": [
                {"id": "baseline", "label": "Baseline"},
                {"id": "challenger", "label": "Challenger"},
            ],
        },
        "/api/v1/runs/run-compare/results": {
            "items": [
                {
                    "id": "result-1",
                    "run_candidate_id": "baseline",
                    "status": "completed",
                    "aggregate_passed": True,
                },
                {
                    "id": "result-2",
                    "run_candidate_id": "challenger",
                    "status": "completed",
                    "aggregate_passed": True,
                },
            ],
            "total": 2,
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes))
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert any("Ready to compare candidates" in str(element.value) for element in app.success)
    compare = {button.label: button for button in app.button}["Compare candidates"]
    compare.click().run()
    assert not app.exception
    assert app.session_state["selected_run_id"] == "run-compare"

    export_selector = next(
        element for element in app.selectbox if element.label == "Export contents"
    )
    prepare_labels = {"Prepare evidence package", "Prepare JSON", "Prepare CSV"}
    prepare_buttons = [button for button in app.button if button.label in prepare_labels]
    assert len(prepare_buttons) == 3
    assert all(not button.disabled for button in prepare_buttons)

    export_selector.select("full_evidence").run()

    prepare_buttons = [button for button in app.button if button.label in prepare_labels]
    assert len(prepare_buttons) == 3
    assert all(button.disabled for button in prepare_buttons)
    confirmation = next(
        element
        for element in app.checkbox
        if element.label == "I understand that these exports include stored evaluation content."
    )
    confirmation.check().run()

    prepare_buttons = [button for button in app.button if button.label in prepare_labels]
    assert len(prepare_buttons) == 3
    assert all(not button.disabled for button in prepare_buttons)


def test_human_calibration_does_not_load_until_an_editor_opens_tools(monkeypatch) -> None:
    routes = _calibration_page_routes()
    requests: list[tuple[str, str]] = []
    history_params: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _calibration_transport(
            routes,
            requests=requests,
            history_params=history_params,
        ),
    )
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert (
        next(toggle for toggle in app.toggle if toggle.label == "Show calibration tools").value
        is False
    )
    assert all("/calibrations" not in path for _, path in requests)
    assert app.get("file_uploader") == []
    assert app.get("download_button") == []
    captions = [str(element.value) for element in app.caption]
    assert any("sent to this EvalForge server" in value for value in captions)
    assert any("not stored" in value for value in captions)

    _open_calibration_tools(app)

    assert not app.exception
    assert {selectbox.label for selectbox in app.selectbox} >= {"Candidate", "Metric"}
    assert [uploader.type for uploader in app.get("file_uploader")] == ["file_uploader"]
    assert [button.label for button in app.get("download_button")] == ["Download label template"]
    assert all(button.label != "Prepare CSV template" for button in app.button)
    import_button = next(button for button in app.button if button.label == "Import calibration")
    assert import_button.disabled is True
    assert any("No calibration reports yet" in str(element.value) for element in app.info)
    assert ("GET", "/api/v1/runs/run-1/calibrations/template") in requests
    assert ("GET", "/api/v1/runs/run-1/calibrations") in requests
    assert history_params == [
        {
            "candidate_id": "candidate-1",
            "metric_name": "correctness",
            "limit": 100,
            "page": 1,
        }
    ]
    captions = [str(element.value) for element in app.caption]
    assert any("both human_passed and reviewer_id" in value for value in captions)
    assert any("anonymous reviewer code" in value for value in captions)


def test_viewer_can_download_template_but_has_no_import_action(monkeypatch) -> None:
    routes = _calibration_page_routes()
    monkeypatch.setattr(ApiClient, "_request_response", _calibration_transport(routes))
    app = AppTest.from_string(VIEWER_RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert app.get("file_uploader") == []
    assert app.get("download_button") == []

    _open_calibration_tools(app)

    assert app.get("file_uploader") == []
    assert all(button.label != "Import calibration" for button in app.button)
    assert any("Read-only" in str(element.value) for element in app.caption)
    assert [button.label for button in app.get("download_button")] == ["Download label template"]


@pytest.mark.parametrize(
    ("status", "message_fragment", "message_kind"),
    [
        ("created", "Calibration evidence saved", "success"),
        ("already_exists", "already exists", "info"),
    ],
)
def test_editor_import_handles_created_and_idempotent_responses(
    monkeypatch,
    status: str,
    message_fragment: str,
    message_kind: str,
) -> None:
    routes = _calibration_page_routes()
    requests: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _calibration_transport(routes, import_status=status, requests=requests),
    )
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()
    _open_calibration_tools(app)
    uploader = app.get("file_uploader")[0]
    uploader.upload(
        "labels.csv",
        (b"item_id,score,human_passed,reviewer_id\nresult-1,0.9,true,reviewer-1\n"),
        "text/csv",
    ).run()
    import_button = next(button for button in app.button if button.label == "Import calibration")
    assert import_button.disabled is False
    import_button.click().run()

    assert not app.exception
    messages = getattr(app, message_kind)
    assert any(message_fragment in str(element.value) for element in messages)
    assert requests.count(("POST", "/api/v1/runs/run-1/calibrations")) == 1


def test_editor_import_surfaces_safe_api_error(monkeypatch) -> None:
    routes = _calibration_page_routes()
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _calibration_transport(routes, import_error=ApiError("Stored scores do not match")),
    )
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()
    _open_calibration_tools(app)
    app.get("file_uploader")[0].upload(
        "labels.csv",
        (b"item_id,score,human_passed,reviewer_id\nresult-1,0.1,true,reviewer-1\n"),
        "text/csv",
    ).run()
    next(button for button in app.button if button.label == "Import calibration").click().run()

    assert not app.exception
    assert any("Stored scores do not match" in str(element.value) for element in app.text)


def test_calibration_latest_report_shows_summary_and_evidence_boundaries(monkeypatch) -> None:
    report = {
        "id": "calibration-1",
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "dataset": {"id": "dataset-1", "version": 2, "sha256": "dataset-hash"},
        "metric": {
            "name": "correctness",
            "version": "1.0.0",
            "direction": "higher_is_better",
        },
        "selected_threshold": 0.7,
        "label_manifest_sha256": "manifest-hash",
        "report_sha256": "report-hash",
        "evidence_kind": "offline_statistical_evidence",
        "production_validated": False,
        "sample_size": 10,
        "human_pass_count": 8,
        "human_fail_count": 2,
        "reviewer_count": 2,
        "precision": 0.8,
        "recall": 0.75,
        "f1": 0.774194,
        "confusion_matrix": {
            "true_positive": 6,
            "true_negative": 1,
            "false_positive": 1,
            "false_negative": 2,
        },
        "created_at": "2026-07-19T03:00:00Z",
    }
    routes = _calibration_page_routes(reports=[report])
    monkeypatch.setattr(ApiClient, "_request_response", _calibration_transport(routes))
    app = AppTest.from_string(RUN_DETAIL_PAGE_SOURCE, default_timeout=15)

    app.run()
    _open_calibration_tools(app)

    assert not app.exception
    metric_values = {metric.label: metric.value for metric in app.metric}
    assert metric_values["Sample size"] == "10"
    assert metric_values["Precision"] == "80.0%"
    assert metric_values["Recall"] == "75.0%"
    assert metric_values["F1"] == "77.4%"
    markdown = [str(element.value) for element in app.markdown]
    assert any("Offline evidence" in value for value in markdown)
    assert any("Not production validated" in value for value in markdown)
    technical = next(status for status in app.status if status.label == "Technical details")
    technical_text = "\n".join(str(element.value) for element in technical.code)
    assert "manifest-hash" in technical_text
    assert "report-hash" in technical_text


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


def _calibration_page_routes(
    *,
    reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "id": "result-1",
        "run_candidate_id": "candidate-1",
        "status": "completed",
        "aggregate_score": 0.9,
        "aggregate_passed": True,
        "metric_results": {
            "correctness": {
                "name": "correctness",
                "score": 0.9,
                "direction": "higher_is_better",
                "threshold": 0.8,
                "applicability": "applicable",
                "passed": True,
            }
        },
    }
    return {
        "/api/v1/runs": {
            "items": [
                {
                    "id": "run-1",
                    "name": "Calibration review",
                    "status": "completed",
                    "created_at": "2026-07-19T02:00:00Z",
                }
            ]
        },
        "/api/v1/runs/run-1": {
            "id": "run-1",
            "name": "Calibration review",
            "status": "completed",
            "created_at": "2026-07-19T02:00:00Z",
            "completed_items": 1,
            "total_items": 1,
            "candidates": [{"id": "candidate-1", "label": "Baseline"}],
            "metric_configuration_snapshot": {
                "metrics": [
                    {
                        "name": "correctness",
                        "version": "1.0.0",
                        "direction": "higher_is_better",
                        "threshold": 0.8,
                    }
                ]
            },
        },
        "/api/v1/runs/run-1/results": {"items": [result], "total": 1},
        "/api/v1/runs/run-1/calibrations": {
            "items": reports or [],
            "total": len(reports or []),
            "page": 1,
            "limit": 100,
        },
    }


def _calibration_transport(
    routes: dict[str, Any],
    *,
    import_status: str = "created",
    import_error: ApiError | None = None,
    requests: list[tuple[str, str]] | None = None,
    history_params: list[dict[str, Any]] | None = None,
):
    def request_response(
        _: ApiClient,
        method: str,
        path: str,
        **_kwargs: Any,
    ) -> httpx.Response:
        if requests is not None:
            requests.append((method, path))
        if history_params is not None and method == "GET" and path.endswith("/calibrations"):
            history_params.append(dict(_kwargs.get("params", {})))
        if path.endswith("/calibrations/template"):
            return httpx.Response(200, content=b"item_id,score,human_passed\nresult-1,0.9,\n")
        if method == "POST" and path.endswith("/calibrations"):
            if import_error is not None:
                raise import_error
            return httpx.Response(
                201 if import_status == "created" else 200,
                json={
                    "status": import_status,
                    "report": {"id": "calibration-1"},
                },
            )
        payload = routes.get(path)
        if payload is None:
            raise ApiError("not found", status_code=404)
        return httpx.Response(200, json=payload)

    return request_response


def _open_calibration_tools(app: AppTest) -> None:
    toggle = next(toggle for toggle in app.toggle if toggle.label == "Show calibration tools")
    toggle.set_value(True).run()
