"""Deterministic browser smoke through the real Streamlit-to-FastAPI boundary."""

import pytest


@pytest.mark.e2e
def test_dashboard_runs_a_seeded_evaluation(page) -> None:
    from playwright.sync_api import expect

    page.goto("http://127.0.0.1:8501/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="EvalForge overview")).to_be_visible(timeout=20_000)
    page.get_by_role("link", name="Run Evaluation", exact=False).click()
    expect(page.get_by_role("heading", name="Run evaluation")).to_be_visible()
    expect(page.get_by_text("API: Live", exact=True)).to_be_visible()
    page.get_by_role("button", name="Validate server preflight").click()
    expect(page.get_by_text("Server preflight passed", exact=False)).to_be_visible()
    page.get_by_role("button", name="Submit evaluation run").click()
    expect(
        page.get_by_text("Evaluation completed. Results are ready to inspect.", exact=True)
    ).to_be_visible(timeout=20_000)
    expect(page.get_by_role("heading", name="Run evaluation")).to_be_visible()
