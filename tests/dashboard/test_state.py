from __future__ import annotations

from evalforge.config import Settings
from evalforge.dashboard import state
from evalforge.dashboard.auth import WorkspaceOption


def test_configured_api_url_uses_the_validated_settings_snapshot(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        api_url="https://api.example.test/root",
    )
    monkeypatch.setattr(state, "get_settings", lambda: settings)

    assert state.configured_api_url() == "https://api.example.test/root"


def test_identity_change_clears_workspace_resources_and_plaintext_tokens(monkeypatch) -> None:
    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    state.initialize_state()
    session.update(
        {
            "selected_run_id": "run-1",
            "active_run_id": "run-1",
            "_evalforge_run_preflight": {"idempotency_key": "attempt"},
            "_evalforge_run_export_json": {"data": b"private"},
            "export-data-dataset-1-json": b"private",
            "evaluation-scoring-policy": [{"name": "correctness", "enabled": True}],
            "model-enabled-model-1": False,
            "edit-case-case-1-input": "private case input",
            "result-evidence-page-all-all": 3,
            "run_filter": "failed",
            "result_page": 4,
            "_evalforge_flash": {"message": "old", "tone": "success"},
        }
    )
    state.sync_identity("fingerprint-one")
    state.select_workspace(WorkspaceOption("workspace-1", "Quality", "owner"))
    state.configure_client(
        identity_fingerprint="fingerprint-one",
        workspace_id="workspace-1",
        access_token_provider=lambda: "private-access-token",
    )

    state.sync_identity("fingerprint-two")

    assert state.selected_run_id() is None
    assert state.active_run_id() is None
    assert state.selected_workspace_id() is None
    assert session["run_filter"] == "all"
    assert session["result_page"] == 0
    assert "_evalforge_run_preflight" not in session
    assert "_evalforge_run_export_json" not in session
    assert "export-data-dataset-1-json" not in session
    assert "evaluation-scoring-policy" not in session
    assert "model-enabled-model-1" not in session
    assert "edit-case-case-1-input" not in session
    assert "result-evidence-page-all-all" not in session
    assert "private-access-token" not in repr(session)


def test_workspace_switch_clears_resource_state_and_partitions_client(monkeypatch) -> None:
    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    state.initialize_state()
    state.sync_identity("fingerprint")
    state.select_workspace(WorkspaceOption("workspace-1", "Quality", "editor"))
    first = state.configure_client(
        identity_fingerprint="fingerprint",
        workspace_id="workspace-1",
        access_token_provider=lambda: "token",
    )
    state.select_run("run-1", active=True)

    state.select_workspace(WorkspaceOption("workspace-2", "Safety", "viewer"))
    second = state.configure_client(
        identity_fingerprint="fingerprint",
        workspace_id="workspace-2",
        access_token_provider=lambda: "token",
    )

    assert first is not second
    assert state.selected_run_id() is None
    assert state.active_run_id() is None
    assert state.selected_workspace_id() == "workspace-2"
    assert state.workspace_context() == WorkspaceOption("workspace-2", "Safety", "viewer")


def test_unauthorized_state_requires_reauthentication_without_stale_evidence(monkeypatch) -> None:
    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    state.initialize_state()
    state.sync_identity("fingerprint")
    state.select_workspace(WorkspaceOption("workspace-1", "Quality", "owner"))
    state.select_run("run-1", active=True)

    state.mark_reauthentication_required()

    assert state.reauthentication_required() is True
    assert state.selected_workspace_id() is None
    assert state.selected_run_id() is None
    assert state.active_run_id() is None


def test_mutation_access_follows_local_and_workspace_roles(monkeypatch) -> None:
    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    state.initialize_state()

    assert state.can_edit() is True

    state.sync_identity("fingerprint")
    state.select_workspace(WorkspaceOption("workspace-1", "Quality", "viewer"))
    assert state.can_edit() is False

    for role in ("editor", "admin", "owner"):
        state.select_workspace(WorkspaceOption("workspace-1", "Quality", role))
        assert state.can_edit() is True


def test_commercial_event_keys_are_content_free_and_workspace_scoped(monkeypatch) -> None:
    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    state.initialize_state()
    state.sync_identity("private-identity-fingerprint")
    state.select_workspace(WorkspaceOption("workspace-1", "Quality", "owner"))

    first = state.commercial_event_key("upgrade-view")
    repeated = state.commercial_event_key("upgrade-view")
    state.mark_commercial_event_recorded(first)

    assert first == repeated
    assert "private-identity-fingerprint" not in first
    assert "workspace-1" not in first
    assert state.commercial_event_recorded(first) is True
    assert state.commercial_tracking_unavailable() is False

    state.mark_commercial_tracking_unavailable()
    assert state.commercial_tracking_unavailable() is True

    state.select_workspace(WorkspaceOption("workspace-2", "Safety", "owner"))
    second = state.commercial_event_key("upgrade-view")

    assert second != first
    assert state.commercial_event_recorded(first) is False


def test_signup_event_key_is_stable_for_an_identity(monkeypatch) -> None:
    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    state.initialize_state()
    state.sync_identity("fingerprint")
    state.select_workspace(WorkspaceOption("workspace-1", "Quality", "owner"))

    key = state.commercial_event_key("signup", once_per_identity=True)
    session["_evalforge_commercial_session_id"] = "another-session"

    assert state.commercial_event_key("signup", once_per_identity=True) == key


def test_commercial_acquisition_source_is_safe_and_first_touch(monkeypatch) -> None:
    class QueryParams:
        source = "github_launch"

        @classmethod
        def get_all(cls, _name: str) -> list[str]:
            return [cls.source]

    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    monkeypatch.setattr(state.st, "query_params", QueryParams())
    state.initialize_state()

    assert state.commercial_acquisition_source() == "github_launch"
    QueryParams.source = "later_campaign"
    assert state.commercial_acquisition_source() == "github_launch"
    assert session["_evalforge_commercial_acquisition_source"] == "github_launch"


def test_commercial_acquisition_source_discards_arbitrary_query_text(monkeypatch) -> None:
    unsafe_value = "Email=person@example.test&campaign=private"

    class QueryParams:
        @staticmethod
        def get_all(_name: str) -> list[str]:
            return [unsafe_value]

    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    monkeypatch.setattr(state.st, "query_params", QueryParams())
    state.initialize_state()

    assert state.commercial_acquisition_source() == "direct"
    assert unsafe_value not in repr(session)


def test_commercial_acquisition_source_rejects_repeated_values(monkeypatch) -> None:
    class RepeatedQueryParams:
        @staticmethod
        def get_all(_name: str) -> list[str]:
            return ["github", "newsletter"]

    session: dict[str, object] = {}
    monkeypatch.setattr(state.st, "session_state", session)
    monkeypatch.setattr(state.st, "query_params", RepeatedQueryParams())
    state.initialize_state()

    assert state.commercial_acquisition_source() == "direct"
