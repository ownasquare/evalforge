from __future__ import annotations

from typing import Any

from evalforge.dashboard.client import ApiError
from evalforge.dashboard.pages.run_detail import _load_all_results


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
