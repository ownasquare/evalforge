from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

from evalforge.dashboard.client import ApiClient, ApiError

APP_PATH = Path(__file__).parents[2] / "src" / "evalforge" / "streamlit_app.py"

RUN_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.run_evaluation import render
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge test", layout="wide")
initialize_state()
render()
"""

ASSET_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.test_cases import render
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge assets test", layout="wide")
initialize_state()
render()
"""

DATASET_CREATE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.test_cases import _render_datasets
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge dataset form test", layout="wide")
initialize_state()
_render_datasets([], editable=True)
"""

PROMPT_CREATE_SOURCE = """
import streamlit as st
from evalforge.dashboard.pages.test_cases import _render_prompts
from evalforge.dashboard.state import initialize_state

st.set_page_config(page_title="EvalForge prompt form test", layout="wide")
initialize_state()
_render_prompts([], editable=True)
"""

VIEWER_RUN_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.auth import WorkspaceOption
from evalforge.dashboard.pages.run_evaluation import render
from evalforge.dashboard.state import initialize_state, select_workspace, sync_identity

st.set_page_config(page_title="EvalForge viewer run test", layout="wide")
initialize_state()
sync_identity("viewer-fingerprint")
select_workspace(WorkspaceOption("workspace-1", "Quality", "viewer"))
render()
"""

VIEWER_ASSET_PAGE_SOURCE = """
import streamlit as st
from evalforge.dashboard.auth import WorkspaceOption
from evalforge.dashboard.pages.test_cases import render
from evalforge.dashboard.state import (
    configure_client,
    initialize_state,
    select_workspace,
    sync_identity,
)

st.set_page_config(page_title="EvalForge viewer assets test", layout="wide")
initialize_state()
sync_identity("viewer-fingerprint")
workspace = WorkspaceOption("workspace-1", "Quality", "viewer")
select_workspace(workspace)
configure_client(identity_fingerprint="viewer-fingerprint", workspace_id=workspace.id)
render()
"""


def test_overview_renders_product_identity_when_api_is_offline(monkeypatch) -> None:
    monkeypatch.setattr(ApiClient, "_request_response", _offline_transport)
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    assert not app.exception
    assert any("EvalForge" in title.value for title in app.title)
    assert any("unavailable" in str(element.value).lower() for element in app.error)


def test_overview_has_deterministic_demo_recovery_copy(monkeypatch) -> None:
    monkeypatch.setattr(ApiClient, "_request_response", _offline_transport)
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    messages = [str(element.value) for element in [*app.info, *app.caption, *app.subheader]]
    assert any("deterministic demo" in message.lower() for message in messages)


def test_empty_overview_focuses_on_the_three_step_first_run(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/health/live": {"status": "healthy"},
        "/api/v1/overview": {
            "totals": {
                "runs": 0,
                "completed_runs": 0,
                "results": 0,
                "evaluated_results": 0,
                "mean_quality": None,
            },
            "recent_runs": [],
        },
        "/api/v1/capabilities": {"demo_available": True, "real_runs_enabled": False},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    assert not app.exception
    assert [subheader.value for subheader in app.subheader].count(
        "Start your first comparison"
    ) == 1
    visible = [str(element.value) for element in [*app.markdown, *app.caption]]
    assert any("Choose a benchmark" in value for value in visible)
    assert any("Pick candidates" in value for value in visible)
    assert any("Review results" in value for value in visible)
    assert not app.metric
    assert [button.label for button in app.button].count("New evaluation") == 1


def test_overview_renders_populated_api_summary(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/health/live": {"status": "healthy"},
        "/api/v1/overview": {
            "totals": {
                "runs": 7,
                "completed_runs": 6,
                "results": 80,
                "evaluated_results": 72,
                "result_success_rate": 0.875,
                "mean_quality": 0.82,
                "known_cost_micro_usd": 12500,
                "known_cost_items": 64,
                "billing_ambiguous_results": 2,
                "unavailable_cost_results": 6,
            },
            "recent_runs": [
                {
                    "id": "run-1",
                    "name": "Support release review",
                    "status": "completed",
                    "completed_items": 10,
                    "total_items": 10,
                    "created_at": "2026-07-18T03:49:00+00:00",
                }
            ],
        },
        "/api/v1/capabilities": {
            "demo_available": True,
            "real_runs_enabled": False,
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Runs"] == "7"
    assert metrics["Completed"] == "6"
    assert metrics["Results checked"] == "72"
    assert metrics["Average quality"] == "0.820"
    visible = [str(element.value) for element in [*app.text, *app.caption, *app.info]]
    assert any("$0.0125" in value for value in visible)
    assert any("64 of 80" in value for value in visible)
    assert not any(
        heading in [element.value for element in app.subheader]
        for heading in ("Quality trend", "Candidate leaderboard", "Failure categories")
    )


def test_overview_never_presents_missing_pricing_as_zero_spend(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/health/live": {"status": "healthy"},
        "/api/v1/overview": {
            "totals": {
                "runs": 1,
                "completed_runs": 1,
                "results": 5,
                "evaluated_results": 5,
                "result_success_rate": 1.0,
                "mean_quality": 0.8,
                "known_cost_micro_usd": None,
                "known_cost_items": 0,
                "billing_ambiguous_results": 0,
                "unavailable_cost_results": 5,
            },
            "recent_runs": [],
        },
        "/api/v1/capabilities": {
            "demo_available": True,
            "real_runs_enabled": False,
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_file(str(APP_PATH), default_timeout=15)

    app.run()

    visible = [str(element.value) for element in [*app.text, *app.caption, *app.info]]
    assert any("pricing is unavailable" in value.lower() for value in visible)
    assert not any("$0.00" in value for value in visible)


def test_run_page_submits_confirmed_demo_matrix(monkeypatch) -> None:
    submitted: list[dict[str, Any]] = []
    idempotency_keys: list[str] = []
    preflight_payloads: list[dict[str, Any]] = []
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA", "version": 1}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "version": 1,
            "cases": [
                {"id": "case-1", "external_id": "refund"},
                {"id": "case-2", "external_id": "shipping"},
            ],
        },
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful", "version": 1}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-1",
                    "name": "Deterministic balanced",
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                }
            ]
        },
        "/api/v1/capabilities": {
            "demo_available": True,
            "real_runs_enabled": False,
            "limits": {"max_calls_per_run": 100},
        },
        "/api/v1/runs/run-1": {
            "id": "run-1",
            "status": "completed",
            "completed_items": 2,
            "total_items": 2,
            "failed_items": 0,
        },
    }
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _fake_transport(routes, submitted, idempotency_keys, preflight_payloads),
    )
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)
    app.run()
    run_name = {field.label: field for field in app.text_input}["Run name"]
    run_name.set_value("Grounded support answers — July 18").run()
    assert not app.checkbox
    buttons = {button.label: button for button in app.button}
    assert "Check setup" not in buttons
    buttons["Start evaluation"].click().run()

    assert not app.exception
    assert submitted == [
        {
            "name": "Grounded support answers — July 18",
            "dataset_id": "dataset-1",
            "prompt_ids": ["prompt-1"],
            "model_ids": ["model-1"],
            "acknowledge_real_cost": False,
            "acknowledge_unknown_cost": False,
            "acknowledge_external_data_transfer": False,
            "spend_limit_micro_usd": None,
        }
    ]
    assert preflight_payloads == [submitted[0]]
    assert len(idempotency_keys) == 1
    assert idempotency_keys[0]
    assert "_evalforge_run_preflight" not in app.session_state
    assert app.session_state["selected_run_id"] == "run-1"
    assert app.session_state["active_run_id"] is None


def test_run_page_routes_an_all_paused_model_state_to_models(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful"}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-paused",
                    "name": "Paused profile",
                    "enabled": False,
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                }
            ]
        },
        "/api/v1/capabilities": {"demo_available": True, "real_runs_enabled": False},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    labels = {button.label for button in app.button}
    assert "Open models" in labels
    assert "Open benchmarks" not in labels
    visible = [str(element.value) for element in [*app.subheader, *app.caption]]
    assert any("available model" in value.lower() for value in visible)


def test_run_page_routes_missing_benchmark_resources_to_benchmarks(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": []},
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful"}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-1",
                    "name": "Deterministic balanced",
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                }
            ]
        },
        "/api/v1/capabilities": {"demo_available": True, "real_runs_enabled": False},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    labels = {button.label for button in app.button}
    assert "Open benchmarks" in labels
    assert "Open models" not in labels


def test_run_page_blocks_an_invalid_scoring_policy(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [{"id": "case-1", "external_id": "refund"}],
        },
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful"}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-1",
                    "name": "Deterministic balanced",
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                }
            ]
        },
        "/api/v1/capabilities": {
            "demo_available": True,
            "real_runs_enabled": False,
            "metrics": [
                {
                    "name": "correctness",
                    "version": "correctness-v1",
                    "direction": "higher_is_better",
                    "weight": 1.0,
                    "threshold": 0.7,
                    "enabled": False,
                }
            ],
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert any("at least one scoring check" in str(item.value).lower() for item in app.warning)
    assert {button.label: button for button in app.button}["Start evaluation"].disabled is True


def test_run_page_submits_explicit_baseline_and_scoring_policy(monkeypatch) -> None:
    submitted: list[dict[str, Any]] = []
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [{"id": "case-1", "external_id": "refund"}],
        },
        "/api/v1/prompts": {
            "items": [
                {"id": "prompt-1", "name": "Current"},
                {"id": "prompt-2", "name": "Challenger"},
            ]
        },
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-1",
                    "name": "Balanced",
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                },
                {
                    "id": "model-2",
                    "name": "Concise",
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                },
            ]
        },
        "/api/v1/capabilities": {
            "demo_available": True,
            "real_runs_enabled": False,
            "limits": {"max_calls_per_run": 100},
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
            ],
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, submitted))
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.multiselect}["Prompt versions"].set_value(
        ["prompt-1", "prompt-2"]
    ).run()
    selectboxes = {field.label: field for field in app.selectbox}
    selectboxes["Baseline prompt"].set_value("prompt-2").run()
    selectboxes = {field.label: field for field in app.selectbox}
    selectboxes["Baseline model"].set_value("model-2").run()
    {field.label: field for field in app.text_input}["Run name"].set_value(
        "Explicit policy review"
    ).run()
    {button.label: button for button in app.button}["Start evaluation"].click().run()

    assert not app.exception
    assert submitted[0]["prompt_ids"] == ["prompt-2", "prompt-1"]
    assert submitted[0]["model_ids"] == ["model-2", "model-1"]
    assert submitted[0]["metrics"] == [
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


def test_real_run_requires_separate_unknown_pricing_acknowledgment(monkeypatch) -> None:
    submitted: list[dict[str, Any]] = []
    preflight_payloads: list[dict[str, Any]] = []
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA", "version": 1}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "version": 1,
            "cases": [{"id": "case-1", "external_id": "refund"}],
        },
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful", "version": 1}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-real",
                    "name": "Unpriced Partner Model",
                    "provider": "openai_compatible",
                    "api_mode": "openai_compatible",
                }
            ]
        },
        "/api/v1/capabilities": {
            "providers": {"real_runs_enabled": True},
            "limits": {
                "max_calls_per_run": 100,
                "max_estimated_input_tokens_per_run": 100_000,
                "max_estimated_cost_micro_usd_per_run": 1_000_000,
            },
        },
        "/api/v1/runs/preflight": {
            "case_count": 1,
            "variant_count": 1,
            "provider_call_count": 1,
            "real_provider": True,
            "unknown_pricing_models": ["Unpriced Partner Model"],
            "estimated_input_tokens": 4_096,
            "input_token_estimate_method": "conservative_utf8_byte_upper_bound_v1",
            "estimated_known_cost_micro_usd": 12_500,
            "cost_estimate_complete": False,
        },
        "/api/v1/runs/run-1": {
            "id": "run-1",
            "status": "completed",
            "completed_items": 1,
            "total_items": 1,
            "failed_items": 0,
        },
    }
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _fake_transport(routes, submitted, preflight_payloads=preflight_payloads),
    )
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.text_input}["Run name"].set_value(
        "Partner model pricing review"
    ).run()
    checkboxes = {checkbox.label: checkbox for checkbox in app.checkbox}
    assert len(checkboxes) == 2
    app.number_input[0].set_value(1.0).run()
    checkboxes = {checkbox.label: checkbox for checkbox in app.checkbox}
    checkboxes[
        "I approve sending this benchmark's prompts, inputs, and context to the selected "
        "external providers."
    ].check().run()
    checkboxes = {checkbox.label: checkbox for checkbox in app.checkbox}
    checkboxes["I understand external provider use may incur charges."].check().run()
    buttons = {button.label: button for button in app.button}
    buttons["Check setup"].click().run()

    assert not app.exception
    checkboxes = {checkbox.label: checkbox for checkbox in app.checkbox}
    unknown_cost_ack = checkboxes[
        "I understand some selected models have unknown pricing and actual charges may be higher."
    ]
    buttons = {button.label: button for button in app.button}
    assert buttons["Start evaluation"].disabled is True
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Estimated input size"] == "4,096"
    assert metrics["Partial known-cost estimate"] == "$0.0125"
    visible_text = [str(element.value) for element in [*app.warning, *app.text, *app.caption]]
    assert any("Unpriced Partner Model" in value for value in visible_text)

    unknown_cost_ack.check().run()
    buttons = {button.label: button for button in app.button}
    assert buttons["Start evaluation"].disabled is False
    buttons["Start evaluation"].click().run()

    assert preflight_payloads == [
        {
            "name": "Partner model pricing review",
            "dataset_id": "dataset-1",
            "prompt_ids": ["prompt-1"],
            "model_ids": ["model-real"],
            "acknowledge_real_cost": True,
            "acknowledge_unknown_cost": False,
            "acknowledge_external_data_transfer": True,
            "spend_limit_micro_usd": 1_000_000,
        }
    ]
    assert submitted == [
        {
            **preflight_payloads[0],
            "acknowledge_unknown_cost": True,
        }
    ]


def test_real_run_with_complete_pricing_needs_only_general_cost_ack(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [{"id": "case-1", "external_id": "refund"}],
        },
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful"}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-real",
                    "name": "Priced Partner Model",
                    "provider": "openai_compatible",
                    "api_mode": "openai_compatible",
                }
            ]
        },
        "/api/v1/capabilities": {"providers": {"real_runs_enabled": True}},
        "/api/v1/runs/preflight": {
            "unknown_pricing_models": [],
            "estimated_input_tokens": 2_048,
            "estimated_known_cost_micro_usd": 25_000,
            "cost_estimate_complete": True,
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.text_input}["Run name"].set_value(
        "Priced partner review"
    ).run()
    app.number_input[0].set_value(1.0).run()
    app.checkbox[0].check().run()
    app.checkbox[1].check().run()
    buttons = {button.label: button for button in app.button}
    buttons["Check setup"].click().run()

    assert not app.exception
    assert [checkbox.label for checkbox in app.checkbox] == [
        "I approve sending this benchmark's prompts, inputs, and context to the selected "
        "external providers.",
        "I understand external provider use may incur charges.",
    ]
    buttons = {button.label: button for button in app.button}
    assert buttons["Start evaluation"].disabled is False
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Known-cost estimate"] == "$0.0250"


def test_demo_run_uses_current_name_in_single_submit(monkeypatch) -> None:
    preflight_payloads: list[dict[str, Any]] = []
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [{"id": "case-1", "external_id": "refund"}],
        },
        "/api/v1/prompts": {"items": [{"id": "prompt-1", "name": "Helpful"}]},
        "/api/v1/models": {
            "items": [
                {
                    "id": "model-1",
                    "name": "Deterministic balanced",
                    "provider": "deterministic",
                    "api_mode": "deterministic",
                }
            ]
        },
        "/api/v1/capabilities": {
            "demo_available": True,
            "real_runs_enabled": False,
            "limits": {"max_calls_per_run": 100},
        },
    }
    monkeypatch.setattr(
        ApiClient,
        "_request_response",
        _fake_transport(routes, [], preflight_payloads=preflight_payloads),
    )
    app = AppTest.from_string(RUN_PAGE_SOURCE, default_timeout=15)

    app.run()
    name_field = {field.label: field for field in app.text_input}["Run name"]
    name_field.set_value("First review name").run()
    {field.label: field for field in app.text_input}["Run name"].set_value(
        "Changed review name"
    ).run()
    buttons = {button.label: button for button in app.button}
    assert "Check setup" not in buttons
    assert buttons["Start evaluation"].disabled is False
    buttons["Start evaluation"].click().run()

    assert preflight_payloads == [
        {
            "name": "Changed review name",
            "dataset_id": "dataset-1",
            "prompt_ids": ["prompt-1"],
            "model_ids": ["model-1"],
            "acknowledge_real_cost": False,
            "acknowledge_unknown_cost": False,
            "acknowledge_external_data_transfer": False,
            "spend_limit_micro_usd": None,
        }
    ]


def test_asset_page_uses_truthful_mutation_and_hash_copy(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA", "version": 1}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [
                {
                    "id": "case-1",
                    "external_id": "refund",
                    "input_text": "Can I get a refund?",
                }
            ],
        },
        "/api/v1/prompts": {
            "items": [
                {
                    "id": "prompt-1",
                    "name": "Helpful",
                    "version": 1,
                    "system_template": "Be helpful.",
                    "user_template": "{input}",
                    "template_hash": "a" * 64,
                }
            ]
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(ASSET_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    button_labels = [button.label for button in app.button]
    assert button_labels.count("Save changes") == 2
    assert not any("new version" in label.lower() for label in button_labels)
    code_values = [str(element.value) for element in app.code]
    assert any("template_hash" in value for value in code_values)
    assert not any("content_hash" in value for value in code_values)


def test_invalid_case_criteria_keeps_the_authors_form_values(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [],
        },
        "/api/v1/prompts": {"items": []},
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(ASSET_PAGE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.text_input}["Case name"].set_value("Refund policy").run()
    {field.label: field for field in app.text_area}["Input"].set_value(
        "Can I request a refund?"
    ).run()
    {field.label: field for field in app.text_area}["JSON Schema (optional)"].set_value("{").run()
    {button.label: button for button in app.button}["Add test case"].click().run()

    assert not app.exception
    assert any("JSON Schema must be valid JSON" in str(message.value) for message in app.warning)
    assert {field.label: field.value for field in app.text_input}["Case name"] == "Refund policy"
    assert {field.label: field.value for field in app.text_area}["Input"] == (
        "Can I request a refund?"
    )


def test_invalid_dataset_keeps_the_authors_description_draft() -> None:
    app = AppTest.from_string(DATASET_CREATE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.text_area}["Description"].set_value(
        "A detailed release benchmark draft."
    ).run()
    {button.label: button for button in app.button}["Create dataset"].click().run()

    assert not app.exception
    assert any("Dataset name is required" in str(message.value) for message in app.warning)
    assert {field.label: field.value for field in app.text_area}["Description"] == (
        "A detailed release benchmark draft."
    )


def test_invalid_prompt_keeps_the_authors_template_draft() -> None:
    app = AppTest.from_string(PROMPT_CREATE_SOURCE, default_timeout=15)

    app.run()
    {field.label: field for field in app.text_area}["System template"].set_value(
        "Answer only from the supplied context."
    ).run()
    {button.label: button for button in app.button}["Create prompt"].click().run()

    assert not app.exception
    assert any(
        "Prompt name and user template are required" in str(message.value)
        for message in app.warning
    )
    assert {field.label: field.value for field in app.text_area}["System template"] == (
        "Answer only from the supplied context."
    )


def test_viewer_deep_link_keeps_evaluation_builder_read_only() -> None:
    app = AppTest.from_string(VIEWER_RUN_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert any("read-only" in str(message.value).lower() for message in app.info)
    assert not any(button.label in {"Check setup", "Start evaluation"} for button in app.button)


def test_viewer_benchmark_page_hides_mutations_but_keeps_export(monkeypatch) -> None:
    routes: dict[str, Any] = {
        "/api/v1/datasets": {"items": [{"id": "dataset-1", "name": "Support QA"}]},
        "/api/v1/datasets/dataset-1": {
            "id": "dataset-1",
            "name": "Support QA",
            "cases": [{"id": "case-1", "external_id": "refund", "input_text": "Refund?"}],
        },
        "/api/v1/prompts": {
            "items": [
                {
                    "id": "prompt-1",
                    "name": "Helpful",
                    "system_template": "Be helpful.",
                    "user_template": "{input}",
                }
            ]
        },
    }
    monkeypatch.setattr(ApiClient, "_request_response", _fake_transport(routes, []))
    app = AppTest.from_string(VIEWER_ASSET_PAGE_SOURCE, default_timeout=15)

    app.run()

    assert not app.exception
    assert any("read-only" in str(message.value).lower() for message in app.info)
    labels = {button.label for button in app.button}
    assert "Prepare export" in labels
    assert labels.isdisjoint(
        {"Create dataset", "Add test case", "Save changes", "Import into dataset", "Create prompt"}
    )


def _fake_transport(
    routes: dict[str, Any],
    submitted: list[dict[str, Any]],
    idempotency_keys: list[str] | None = None,
    preflight_payloads: list[dict[str, Any]] | None = None,
):
    def request_response(
        _: ApiClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if method == "POST" and path == "/api/v1/runs/preflight":
            payload = kwargs.get("json_payload")
            if isinstance(payload, dict) and preflight_payloads is not None:
                preflight_payloads.append(payload)
            return httpx.Response(
                200,
                json=routes.get(
                    path,
                    {
                        "case_count": 2,
                        "variant_count": 1,
                        "provider_call_count": 2,
                        "real_provider": False,
                        "unknown_pricing_models": [],
                        "estimated_input_tokens": 128,
                        "input_token_estimate_method": ("conservative_utf8_byte_upper_bound_v1"),
                        "estimated_known_cost_micro_usd": 0,
                        "cost_estimate_complete": True,
                    },
                ),
            )
        if method == "POST" and path == "/api/v1/runs":
            payload = kwargs.get("json_payload")
            if isinstance(payload, dict):
                submitted.append(payload)
            headers = kwargs.get("headers")
            if isinstance(headers, dict) and idempotency_keys is not None:
                key = headers.get("Idempotency-Key")
                if isinstance(key, str):
                    idempotency_keys.append(key)
            return httpx.Response(202, json={"id": "run-1", "status": "queued"})
        payload = routes.get(path)
        if payload is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=payload)

    return request_response


def _offline_transport(
    _client: ApiClient,
    method: str,
    path: str,
    **_kwargs: Any,
) -> httpx.Response:
    raise ApiError(f"offline fixture for {method} {path}")
