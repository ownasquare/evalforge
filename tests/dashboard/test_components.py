from __future__ import annotations

import math

from evalforge.dashboard.components import (
    as_float,
    format_currency,
    format_duration_ms,
    format_metric_target,
    format_micro_usd,
    format_percent,
    format_score,
    humanize_metric_name,
    is_demo_record,
    is_terminal_status,
    metric_direction_label,
    normalized_metric_rows,
    resource_label,
    status_color,
)
from evalforge.dashboard.theme import _STATIC_CSS, TOKENS


def test_formatters_preserve_unavailable_semantics() -> None:
    assert format_percent(None) == "—"
    assert format_score(float("nan")) == "—"
    assert format_currency(None) == "—"
    assert format_duration_ms(None) == "—"
    assert as_float(math.inf) is None


def test_formatters_render_valid_values() -> None:
    assert format_percent(0.875) == "87.5%"
    assert format_score(0.8754) == "0.875"
    assert format_currency(0.0012) == "$0.0012"
    assert format_micro_usd(12500) == "$0.0125"
    assert format_duration_ms(2500) == "2.50 s"


def test_status_helpers_include_terminal_and_partial_states() -> None:
    assert status_color("completed") == "green"
    assert status_color("partial") == "orange"
    assert status_color("unexpected") == "gray"
    assert is_terminal_status("interrupted") is True
    assert is_terminal_status("running") is False


def test_metric_rows_normalize_mapping_and_list_shapes() -> None:
    assert normalized_metric_rows({"correctness": {"score": 1.0}}) == [
        {"name": "correctness", "score": 1.0}
    ]
    assert normalized_metric_rows([{"name": "relevance", "score": 0.5}]) == [
        {"name": "relevance", "score": 0.5}
    ]


def test_resource_labels_and_demo_classification() -> None:
    assert resource_label({"name": "Support QA", "version": 3}) == "Support QA · v3"
    assert is_demo_record({"provider": "deterministic"}) is True
    assert is_demo_record({"provider": "openai"}) is False


def test_metric_names_are_human_readable_without_losing_common_acronyms() -> None:
    assert humanize_metric_name("hallucination_risk") == "Hallucination risk"
    assert humanize_metric_name("json_validity") == "JSON validity"
    assert humanize_metric_name("aggregate-quality") == "Aggregate quality"


def test_metric_direction_labels_preserve_scoring_semantics() -> None:
    assert metric_direction_label("higher_is_better") == "Higher is better"
    assert metric_direction_label("lower-is-better") == "Lower is better"
    assert metric_direction_label(None) == "Direction unavailable"


def test_metric_targets_use_directional_operators_and_unavailable_semantics() -> None:
    assert format_metric_target(0.65, "higher_is_better") == "≥ 0.65"
    assert format_metric_target(0.25, "lower_is_better") == "≤ 0.25"
    assert format_metric_target(0.5, None) == "Target 0.50"
    assert format_metric_target(None, "higher_is_better") == "—"


def test_visual_tokens_use_a_neutral_solid_interface_system() -> None:
    assert TOKENS["canvas"] == "#F5F6F4"
    assert TOKENS["surface"] == "#FFFFFF"
    assert TOKENS["ink"] == "#18201C"
    assert TOKENS["accent"] == "#255F7A"
    assert "gradient" not in _STATIC_CSS
    assert "#6558F5" not in _STATIC_CSS
    assert 'button[data-testid="stBaseButton-primary"] *' in _STATIC_CSS
    assert "color: #FFFFFF !important;" in _STATIC_CSS
