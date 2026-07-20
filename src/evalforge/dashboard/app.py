"""Streamlit entry point for the EvalForge evaluation workbench."""

from __future__ import annotations

import streamlit as st

from evalforge.dashboard import state
from evalforge.dashboard.auth import (
    DashboardAuthConfig,
    MissingAccessTokenError,
    WorkspaceOption,
    configured_auth,
    current_auth_context,
    parse_account,
    parse_workspaces,
    safe_markdown_text,
)
from evalforge.dashboard.client import ApiError
from evalforge.dashboard.components import render_status_badge
from evalforge.dashboard.pages import (
    compare,
    models,
    overview,
    run_detail,
    run_evaluation,
    settings,
    test_cases,
)
from evalforge.dashboard.state import (
    configure_client,
    get_client,
    initialize_state,
    register_pages,
)
from evalforge.dashboard.theme import apply_theme


def main() -> None:
    st.set_page_config(
        page_title="EvalForge · Evaluation workspace",
        page_icon="✓",
        layout="wide",
        initial_sidebar_state="auto",
        menu_items={
            "Get Help": None,
            "Report a bug": None,
            "About": "EvalForge helps teams compare model and prompt quality before release.",
        },
    )
    apply_theme()
    initialize_state()
    state.commercial_acquisition_source()

    try:
        auth_config = configured_auth()
    except ValueError:
        _render_auth_configuration_error()
        return

    if auth_config.mode == "oidc":
        if not _prepare_authenticated_workspace(auth_config):
            return
    else:
        context = current_auth_context(auth_config)
        if context is None:  # pragma: no cover - local mode always resolves
            return
        state.sync_identity(context.identity_fingerprint)
        configure_client(identity_fingerprint=context.identity_fingerprint)

    _record_commercial_entry_events()
    _render_workspace(auth_config)


def _render_workspace(auth_config: DashboardAuthConfig) -> None:
    """Render the workbench only after its identity boundary is ready."""

    pages = {
        "overview": st.Page(
            overview.render,
            title="Home",
            icon=":material/home:",
            url_path="overview",
            default=True,
        ),
        "run_evaluation": st.Page(
            run_evaluation.render,
            title="New evaluation",
            icon=":material/add_circle:",
            url_path="evaluate",
        ),
        "run_detail": st.Page(
            run_detail.render,
            title="Results",
            icon=":material/history:",
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
            title="Benchmarks",
            icon=":material/library_books:",
            url_path="assets",
        ),
        "models": st.Page(
            models.render,
            title="Models",
            icon=":material/model_training:",
            url_path="models",
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
        st.caption("Evaluation workspace")
        if auth_config.mode == "oidc":
            _render_sidebar_identity()
        st.divider()

    navigation_groups = {"Workspace": [pages["overview"]]}
    if state.can_edit():
        navigation_groups["Evaluate"] = [pages["run_evaluation"]]
    navigation_groups["Review"] = [pages["run_detail"], pages["compare"]]
    navigation_groups["Library"] = [pages["test_cases"], pages["models"]]
    navigation_groups["System"] = [pages["settings"]]
    navigation = st.navigation(
        navigation_groups,
        position="sidebar",
    )
    with st.sidebar:
        st.divider()
        _render_api_health()
    navigation.run()


def _prepare_authenticated_workspace(auth_config: DashboardAuthConfig) -> bool:
    if state.reauthentication_required():
        _render_reauthentication_gate()
        return False

    try:
        context = current_auth_context(auth_config)
    except MissingAccessTokenError:
        _render_missing_token_gate()
        return False
    if context is None:
        state.clear_identity()
        _render_sign_in_gate(auth_config)
        return False

    state.sync_identity(context.identity_fingerprint)
    workspace_id = state.selected_workspace_id()
    api = configure_client(
        identity_fingerprint=context.identity_fingerprint,
        workspace_id=workspace_id,
        access_token_provider=lambda: context.access_token,
    )
    try:
        session_payload = api.session()
        workspace_payload = api.workspaces()
    except ApiError as error:
        if error.status_code == 401:
            state.mark_reauthentication_required()
            _render_reauthentication_gate()
        elif error.status_code == 403:
            state.select_workspace(None)
            _render_workspace_access_error()
        else:
            _render_workspace_service_error(error)
        return False

    state.set_account_context(parse_account(session_payload))
    workspaces = parse_workspaces(workspace_payload)
    state.set_available_workspaces(workspaces)
    selected = state.workspace_context()
    if selected is None:
        _render_workspace_gate(workspaces)
        return False

    configure_client(
        identity_fingerprint=context.identity_fingerprint,
        workspace_id=selected.id,
        access_token_provider=lambda: context.access_token,
    )
    return True


def _render_sign_in_gate(auth_config: DashboardAuthConfig) -> None:
    columns = st.columns([1, 1.2, 1])
    with columns[1]:
        st.title("Welcome to EvalForge")
        st.write("Sign in to open your evaluation workspace.")
        st.caption("Your organization manages access and workspace membership.")
        if st.button("Sign in", type="primary", width="stretch"):
            st.login(auth_config.provider)


def _render_workspace_gate(workspaces: list[WorkspaceOption]) -> None:
    columns = st.columns([1, 1.35, 1])
    with columns[1]:
        st.title("Choose a workspace")
        account = state.account_context()
        if account is not None:
            st.caption(f"Signed in as {safe_markdown_text(account.display_name)}")
        if not workspaces:
            st.warning(
                "You do not have an active EvalForge workspace yet. Ask a workspace owner "
                "to add you, then try again.",
                icon=":material/group_off:",
            )
            _render_sign_out_button()
            return
        choice = st.selectbox(
            "Workspace",
            options=workspaces,
            index=None,
            placeholder="Select a workspace",
            format_func=lambda workspace: workspace.name,
        )
        if st.button(
            "Open workspace",
            type="primary",
            width="stretch",
            disabled=choice is None,
        ):
            state.select_workspace(choice)
            st.rerun()
        _render_sign_out_button()


def _render_sidebar_identity() -> None:
    account = state.account_context()
    workspace = state.workspace_context()
    if account is not None:
        st.caption(safe_markdown_text(account.display_name))
    if workspace is not None:
        st.markdown(f"**{safe_markdown_text(workspace.name)}**")
        st.caption(workspace.role.title())


def _render_missing_token_gate() -> None:
    st.title("Sign-in setup needs attention")
    st.warning(
        "Your sign-in succeeded, but this app did not receive the access token needed to "
        "open EvalForge. Ask an administrator to expose the access token for the Streamlit "
        "OIDC provider.",
        icon=":material/key_off:",
    )
    _render_sign_out_button()


def _render_reauthentication_gate() -> None:
    st.title("Your session has ended")
    st.info("Sign in again to return to your workspace.", icon=":material/lock_clock:")
    if st.button("Sign in again", type="primary"):
        state.clear_identity()
        st.logout()


def _render_workspace_access_error() -> None:
    st.title("Workspace access changed")
    st.warning(
        "You are still signed in, but this workspace is no longer available to your account. "
        "Try another workspace or contact a workspace owner.",
        icon=":material/admin_panel_settings:",
    )
    if st.button("Choose another workspace", type="primary"):
        st.rerun()
    _render_sign_out_button()


def _render_workspace_service_error(error: ApiError) -> None:
    st.title("We could not open your workspace")
    st.error(str(error), icon=":material/cloud_off:")
    st.caption("Your evaluation data has not been changed. Try again when the API is available.")
    if st.button("Try again", type="primary"):
        st.rerun()
    _render_sign_out_button()


def _render_auth_configuration_error() -> None:
    st.title("EvalForge needs an identity configuration update")
    st.error(
        "The dashboard auth settings are invalid. Ask the application administrator to review "
        "the configured auth mode and OIDC provider name.",
        icon=":material/settings_alert:",
    )


def _render_sign_out_button() -> None:
    if st.button("Sign out", width="stretch"):
        state.clear_identity()
        st.logout()


def _render_api_health() -> None:
    try:
        get_client().health_live()
    except ApiError:
        st.caption("Connection needs attention")
        render_status_badge("offline", prefix="API")


def _record_commercial_entry_events() -> None:
    """Capture content-free pilot entry events without blocking the workbench."""

    api = get_client()
    try:
        capabilities = api.capabilities()
    except ApiError:
        return
    commercial = capabilities.get("commercial")
    if not isinstance(commercial, dict):
        return
    if commercial.get("pilot_enabled") is not True or commercial.get("hosted") is not True:
        return
    acquisition_source = state.commercial_acquisition_source()
    for name, once_per_identity in (("landing", False), ("signup", True)):
        idempotency_key = state.commercial_event_key(
            name,
            once_per_identity=once_per_identity,
        )
        if state.commercial_event_recorded(idempotency_key):
            continue
        try:
            api.record_activation_event(
                name,
                source=acquisition_source,
                surface="dashboard",
                idempotency_key=idempotency_key,
            )
        except ApiError:
            state.mark_commercial_tracking_unavailable()
            return
        state.mark_commercial_event_recorded(idempotency_key)


if __name__ == "__main__":
    main()
