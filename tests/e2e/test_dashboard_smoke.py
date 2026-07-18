"""Deterministic browser smoke through the real Streamlit-to-FastAPI boundary."""

import os

import pytest

DASHBOARD_URL = os.environ.get("EVALFORGE_DASHBOARD_URL", "http://127.0.0.1:8501/")


@pytest.mark.e2e
def test_dashboard_runs_a_seeded_evaluation(page) -> None:
    from playwright.sync_api import expect

    page.goto(DASHBOARD_URL, wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="Evaluation workspace")).to_be_visible(timeout=20_000)
    page.get_by_role("link", name="New evaluation", exact=False).click()
    expect(page.get_by_role("heading", name="New evaluation")).to_be_visible()
    expect(page.get_by_text("API: Live", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Run name", exact=True).fill("E2E grounded answer review")
    page.get_by_role("button", name="Check setup").click()
    expect(page.get_by_text("Setup checked", exact=False)).to_be_visible()
    page.get_by_role("button", name="Start evaluation").click()
    expect(
        page.get_by_text("Evaluation completed. Results are ready to inspect.", exact=True)
    ).to_be_visible(timeout=20_000)
    expect(page.get_by_role("button", name="Review results")).to_be_visible()
    page.get_by_role("button", name="Review results").click()
    expect(page.get_by_role("heading", name="Runs")).to_be_visible()
    expect(page.get_by_text("E2E grounded answer review", exact=True)).to_be_visible()
