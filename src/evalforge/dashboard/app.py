"""Streamlit entry point for the EvalForge evaluation workbench."""

from __future__ import annotations

import streamlit as st

from evalforge.dashboard.client import ApiError
from evalforge.dashboard.components import render_status_badge
from evalforge.dashboard.pages import (
    compare,
    overview,
    run_detail,
    run_evaluation,
    settings,
    test_cases,
)
from evalforge.dashboard.state import (
    configured_api_url,
    get_client,
    initialize_state,
    register_pages,
)
from evalforge.dashboard.theme import apply_theme


def main() -> None:
    st.set_page_config(
        page_title="EvalForge · LLM Evaluation",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": None,
            "Report a bug": None,
            "About": "EvalForge makes LLM quality and reliability visible before production.",
        },
    )
    apply_theme()
    initialize_state()

    pages = {
        "overview": st.Page(
            overview.render,
            title="Overview",
            icon=":material/dashboard:",
            url_path="overview",
            default=True,
        ),
        "run_evaluation": st.Page(
            run_evaluation.render,
            title="Run Evaluation",
            icon=":material/science:",
            url_path="evaluate",
        ),
        "run_detail": st.Page(
            run_detail.render,
            title="Run Detail",
            icon=":material/analytics:",
            url_path="runs",
        ),
        "compare": st.Page(
            compare.render,
            title="Compare",
            icon=":material/compare_arrows:",
            url_path="compare",
        ),
        "test_cases": st.Page(
            test_cases.render,
            title="Test Cases & Prompts",
            icon=":material/dataset:",
            url_path="assets",
        ),
        "settings": st.Page(
            settings.render,
            title="Settings",
            icon=":material/settings:",
            url_path="settings",
        ),
    }
    register_pages(pages)

    with st.sidebar:
        st.title("EvalForge")
        st.caption("LLM quality, explained.")
        _render_api_health()
        st.divider()

    navigation = st.navigation(
        {
            "Monitor": [pages["overview"], pages["run_detail"], pages["compare"]],
            "Build": [pages["run_evaluation"], pages["test_cases"]],
            "Operate": [pages["settings"]],
        },
        position="sidebar",
    )
    navigation.run()


def _render_api_health() -> None:
    try:
        payload = get_client().health_live()
    except ApiError:
        render_status_badge("offline", prefix="API")
    else:
        status = str(payload.get("status", "healthy"))
        render_status_badge(status, prefix="API")
    st.caption("API origin")
    st.code(configured_api_url(), language=None)


if __name__ == "__main__":
    main()
