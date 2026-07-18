from __future__ import annotations

from evalforge.dashboard.pages.compare import _candidate_cost, _pricing_coverage
from evalforge.dashboard.pages.overview import _real_runs_enabled
from evalforge.dashboard.pages.run_detail import _detail_cost_help, _passed_label
from evalforge.dashboard.pages.settings import _count_or_list


def test_overview_reads_nested_real_provider_capability() -> None:
    assert _real_runs_enabled({"providers": {"real_runs_enabled": True}}) is True
    assert _real_runs_enabled({"providers": {"real_runs_enabled": False}}) is False


def test_comparison_separates_known_spend_from_pricing_coverage() -> None:
    priced = {
        "known_cost_micro_usd": 12_500,
        "known_cost_items": 2,
        "completed": 3,
    }
    unpriced = {
        "known_cost_micro_usd": 0,
        "known_cost_items": 0,
        "completed": 3,
    }

    assert _candidate_cost(priced) == "$0.0125"
    assert _pricing_coverage(priced) == "2/3 results"
    assert _candidate_cost(unpriced) == "—"
    assert _pricing_coverage(unpriced) == "0/3 results"


def test_run_detail_explains_partial_pricing_coverage() -> None:
    help_text = _detail_cost_help(
        {},
        {
            "known_cost_micro_usd": 12_500,
            "known_cost_items": 2,
            "result_count": 3,
        },
    )

    assert "2 of 3" in help_text
    assert "unpriced results are excluded" in help_text


def test_dataframe_display_values_keep_stable_string_types() -> None:
    assert [_passed_label(value) for value in (True, False, None)] == ["Yes", "No", "—"]
    assert _count_or_list(["one", "two"]) == "2"
    assert _count_or_list(3) == "3"
