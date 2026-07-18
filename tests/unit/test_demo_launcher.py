from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from evalforge import cli, demo
from evalforge.config import Settings


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Process:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'launcher.db'}")


def test_package_owned_commands_use_the_active_python_and_validated_settings(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    api = demo.api_command(settings)
    dashboard = demo.ui_command(settings)

    assert api == (
        sys.executable,
        "-m",
        "uvicorn",
        "evalforge.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--workers",
        "1",
    )
    assert dashboard[:4] == (
        sys.executable,
        "-m",
        "streamlit",
        "run",
    )
    assert Path(dashboard[4]).name == "streamlit_app.py"
    assert dashboard[5:] == (
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
    )


def test_prepare_demo_migrates_then_seeds_the_local_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    events: list[str] = []

    monkeypatch.setattr(demo, "apply_migrations", lambda received: events.append("migrate"))
    monkeypatch.setattr(
        demo,
        "create_database_engine",
        lambda received: events.append("engine") or object(),
    )
    monkeypatch.setattr(demo, "create_session_factory", lambda engine: "factory")

    class _SessionContext:
        def __enter__(self) -> object:
            events.append("session")
            return object()

        def __exit__(self, *args: object) -> None:
            events.append("commit")

    monkeypatch.setattr(demo, "session_scope", lambda factory: _SessionContext())
    monkeypatch.setattr(
        demo,
        "seed_demo",
        lambda session, context: (
            events.append("seed") or {"datasets": 2, "models": 3, "prompts": 2}
        ),
    )
    monkeypatch.setattr(demo, "local_workspace_context", lambda: object())
    monkeypatch.setattr(demo, "dispose_engine", lambda engine: events.append("dispose"))

    counts = demo.prepare_demo(settings)

    assert counts == {"datasets": 2, "models": 3, "prompts": 2}
    assert events == ["migrate", "engine", "session", "seed", "commit", "dispose"]


def test_prepare_demo_refuses_shared_identity_mode(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"auth_mode": "oidc"})

    with pytest.raises(demo.LauncherError, match="local workspace"):
        demo.prepare_demo(settings)


def test_demo_origin_uses_the_listener_instead_of_an_independent_api_url(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path).model_copy(
        update={
            "api_host": "localhost",
            "api_port": 18_321,
            "api_url": "http://127.0.0.1:8000",
        }
    )

    assert demo._api_base_url(settings) == "http://localhost:18321"


def test_demo_child_environments_share_the_bound_origin_and_hide_ui_secrets(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path).model_copy(update={"api_port": 18_322})
    base = {
        "KEEP_ME": "yes",
        "EVALFORGE_API_URL": "http://127.0.0.1:8000",
        "EVALFORGE_OPENAI_API_KEY": "backend-only-openai",
        "EVALFORGE_COMPATIBLE_API_KEY": "backend-only-compatible",
    }
    api_url = demo._api_base_url(settings)

    api_environment = demo._demo_environment(
        settings,
        api_url=api_url,
        dashboard=False,
        base_environment=base,
    )
    dashboard_environment = demo._demo_environment(
        settings,
        api_url=api_url,
        dashboard=True,
        base_environment=base,
    )

    assert api_environment["EVALFORGE_API_URL"] == "http://127.0.0.1:18322"
    assert api_environment["EVALFORGE_OPENAI_API_KEY"] == "backend-only-openai"
    assert dashboard_environment["EVALFORGE_API_URL"] == "http://127.0.0.1:18322"
    assert dashboard_environment["EVALFORGE_OPENAI_API_KEY"].strip() == ""
    assert dashboard_environment["EVALFORGE_COMPATIBLE_API_KEY"].strip() == ""
    assert dashboard_environment["KEEP_ME"] == "yes"


def test_standalone_dashboard_environment_hides_env_and_dotenv_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "EVALFORGE_OPENAI_API_KEY=dotenv-openai\nEVALFORGE_COMPATIBLE_API_KEY=dotenv-compatible\n",
        encoding="utf-8",
    )
    environment = demo.dashboard_environment(
        settings,
        base_environment={
            "EVALFORGE_OPENAI_API_KEY": "inherited-openai",
            "EVALFORGE_COMPATIBLE_API_KEY": "inherited-compatible",
        },
    )

    assert environment["EVALFORGE_OPENAI_API_KEY"].strip() == ""
    assert environment["EVALFORGE_COMPATIBLE_API_KEY"].strip() == ""
    monkeypatch.setenv("EVALFORGE_OPENAI_API_KEY", environment["EVALFORGE_OPENAI_API_KEY"])
    monkeypatch.setenv(
        "EVALFORGE_COMPATIBLE_API_KEY",
        environment["EVALFORGE_COMPATIBLE_API_KEY"],
    )
    sanitized = Settings(_env_file=dotenv)
    assert sanitized.openai_api_key is None
    assert sanitized.compatible_api_key is None


def test_wait_until_ready_retries_until_the_endpoint_is_healthy() -> None:
    process = _Process()
    responses = iter([_Response(503), _Response(200)])
    clock = iter([0.0, 0.1, 0.2])
    sleeps: list[float] = []

    demo.wait_until_ready(
        process,
        "http://127.0.0.1:8000/health/ready",
        timeout_seconds=2,
        request=lambda url, timeout: next(responses),
        clock=lambda: next(clock),
        sleep=sleeps.append,
    )

    assert sleeps == [demo.READINESS_POLL_SECONDS]


def test_wait_until_ready_reports_a_service_that_exits_early() -> None:
    process = _Process(returncode=3)

    with pytest.raises(demo.LauncherError, match="exited with status 3"):
        demo.wait_until_ready(
            process,
            "http://127.0.0.1:8000/health/ready",
            timeout_seconds=2,
        )


def test_stop_processes_terminates_running_children_and_kills_a_stuck_child() -> None:
    graceful = _Process()
    stuck = _Process()

    def stuck_wait(timeout: float | None = None) -> int:
        del timeout
        if not stuck.killed:
            raise subprocess.TimeoutExpired("ui", 1)
        return -9

    stuck.wait = stuck_wait  # type: ignore[method-assign]

    demo.stop_processes([graceful, stuck], grace_seconds=0.01)

    assert graceful.terminated is True
    assert graceful.killed is False
    assert stuck.terminated is True
    assert stuck.killed is True


def test_run_demo_prepares_starts_checks_reports_and_always_stops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    events: list[tuple[str, Any]] = []
    processes = [_Process(), _Process()]
    started_api_urls: list[str | None] = []

    monkeypatch.setattr(
        demo,
        "prepare_demo",
        lambda received: (
            events.append(("prepare", received)) or {"datasets": 2, "models": 3, "prompts": 2}
        ),
    )

    def start_process(command: tuple[str, ...], **kwargs: Any) -> _Process:
        environment = kwargs["env"]
        started_api_urls.append(environment.get("EVALFORGE_API_URL"))
        events.append(("start", tuple(command)))
        return processes.pop(0)

    monkeypatch.setattr(demo.subprocess, "Popen", start_process)
    monkeypatch.setattr(
        demo,
        "wait_until_ready",
        lambda process, url, timeout_seconds: events.append(("ready", url)),
    )
    monkeypatch.setattr(
        demo,
        "supervise",
        lambda services: events.append(("supervise", tuple(name for name, _ in services))),
    )
    stopped: list[object] = []
    monkeypatch.setattr(
        demo,
        "stop_processes",
        lambda children: stopped.extend(children),
    )
    messages: list[str] = []

    demo.run_demo(settings, emit=messages.append, startup_timeout_seconds=3)

    assert [event[0] for event in events] == [
        "prepare",
        "start",
        "ready",
        "start",
        "ready",
        "supervise",
    ]
    assert events[2] == ("ready", "http://127.0.0.1:8000/health/ready")
    assert events[4] == ("ready", "http://127.0.0.1:8501/_stcore/health")
    assert started_api_urls == ["http://127.0.0.1:8000", "http://127.0.0.1:8000"]
    assert len(stopped) == 2
    assert any("http://127.0.0.1:8501" in message for message in messages)
    assert messages[-1] == "Press Ctrl+C to stop."


def test_run_demo_stops_the_api_when_dashboard_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    api_process = _Process()
    stopped: list[object] = []

    monkeypatch.setattr(
        demo,
        "prepare_demo",
        lambda received: {"datasets": 2, "models": 3, "prompts": 2},
    )
    monkeypatch.setattr(demo.subprocess, "Popen", lambda command, **kwargs: api_process)
    monkeypatch.setattr(
        demo,
        "wait_until_ready",
        lambda process, url, timeout_seconds: (_ for _ in ()).throw(
            demo.LauncherError("API did not become ready")
        ),
    )
    monkeypatch.setattr(
        demo,
        "stop_processes",
        lambda children: stopped.extend(children),
    )

    with pytest.raises(demo.LauncherError, match="API did not become ready"):
        demo.run_demo(settings, emit=lambda message: None)

    assert stopped == [api_process]


def test_cli_commands_delegate_to_the_package_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    settings = _settings(tmp_path)
    foreground_commands: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "run_foreground",
        lambda command, **kwargs: (
            foreground_commands.append((command, kwargs.get("environment"))) or 0
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_demo",
        lambda received, emit: emit("EvalForge is ready at http://127.0.0.1:8501"),
    )

    assert runner.invoke(cli.app, ["api"]).exit_code == 0
    assert runner.invoke(cli.app, ["ui"]).exit_code == 0
    launched = runner.invoke(cli.app, ["demo"])

    assert launched.exit_code == 0
    assert foreground_commands[0] == (demo.api_command(settings), None)
    assert foreground_commands[1][0] == demo.ui_command(settings)
    ui_environment = foreground_commands[1][1]
    assert ui_environment is not None
    assert ui_environment["EVALFORGE_OPENAI_API_KEY"].strip() == ""
    assert ui_environment["EVALFORGE_COMPATIBLE_API_KEY"].strip() == ""
    assert "EvalForge is ready" in launched.stdout


def test_makefile_uses_the_same_public_launcher_commands() -> None:
    makefile = (Path(__file__).parents[2] / "Makefile").read_text(encoding="utf-8")

    assert "uv run evalforge api" in makefile
    assert "uv run evalforge ui" in makefile
    assert "uv run evalforge demo" in makefile
