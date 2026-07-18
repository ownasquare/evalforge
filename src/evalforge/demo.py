"""Package-owned launchers for the API, dashboard, and offline demo."""

from __future__ import annotations

# Child processes use package-owned literal argv and never invoke a shell.
import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.engine import Engine

from evalforge.config import Settings
from evalforge.container import apply_migrations
from evalforge.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from evalforge.security.permissions import local_workspace_context
from evalforge.seed import seed_demo

READINESS_POLL_SECONDS = 0.2
DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0
DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0


class LauncherError(RuntimeError):
    """Raised when a local service cannot be started safely."""


class ResponseLike(Protocol):
    """Small HTTP response surface required by the readiness loop."""

    status_code: int


class ManagedProcess(Protocol):
    """Subprocess surface required for supervision and cleanup."""

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


HttpRequest = Callable[..., ResponseLike]
Clock = Callable[[], float]
Sleep = Callable[[float], None]
Emitter = Callable[[str], None]
Command = tuple[str, ...]

DASHBOARD_PROVIDER_SECRET_KEYS = (
    "EVALFORGE_OPENAI_API_KEY",
    "EVALFORGE_COMPATIBLE_API_KEY",
)
# Settings ignores truly empty environment values and would then fall back to `.env`.
# Whitespace is non-empty to the settings source and normalized to `None` by the secret validator.
_SETTINGS_NONE_OVERRIDE = " "


def api_command(settings: Settings) -> Command:
    """Build the API command with the active interpreter and validated settings."""

    return (
        sys.executable,
        "-m",
        "uvicorn",
        "evalforge.api.app:app",
        "--host",
        settings.api_host,
        "--port",
        str(settings.api_port),
        "--workers",
        "1",
    )


def ui_command(settings: Settings) -> Command:
    """Build the dashboard command using the packaged Streamlit entry point."""

    streamlit_app = Path(__file__).with_name("streamlit_app.py")
    return (
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(streamlit_app),
        "--server.address",
        settings.dashboard_host,
        "--server.port",
        str(settings.dashboard_port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
    )


def run_foreground(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run one package service in the foreground and return its exit status."""

    try:
        # Commands are package-owned argv tuples; no shell or user-provided executable is used.
        return subprocess.run(  # noqa: S603  # nosec B603
            tuple(command),
            check=False,
            env=None if environment is None else dict(environment),
        ).returncode
    except KeyboardInterrupt:
        return 130


def dispose_engine(engine: Engine) -> None:
    """Dispose a short-lived preparation engine."""

    engine.dispose()


def prepare_demo(settings: Settings) -> dict[str, int]:
    """Migrate and idempotently seed the zero-configuration local workspace."""

    if settings.auth_mode != "local":
        raise LauncherError(
            "The demo launcher is for the local workspace. "
            "Use the API and UI commands for a shared deployment."
        )

    apply_migrations(settings)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            return seed_demo(session, local_workspace_context())
    finally:
        dispose_engine(engine)


def wait_until_ready(
    process: ManagedProcess,
    url: str,
    *,
    timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
    request: HttpRequest = httpx.get,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> None:
    """Wait for one service health endpoint or raise a concise startup error."""

    deadline = clock() + timeout_seconds
    while clock() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise LauncherError(f"A service exited with status {returncode} before it was ready.")
        try:
            response = request(url, timeout=1.0)
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            return
        sleep(READINESS_POLL_SECONDS)
    raise LauncherError(f"A service did not become ready within {timeout_seconds:g} seconds.")


def supervise(
    services: Sequence[tuple[str, ManagedProcess]],
    *,
    sleep: Sleep = time.sleep,
) -> None:
    """Keep both local services alive and surface an unexpected exit."""

    while True:
        for name, process in services:
            returncode = process.poll()
            if returncode is not None:
                raise LauncherError(f"{name} exited with status {returncode}.")
        sleep(READINESS_POLL_SECONDS)


def stop_processes(
    processes: Iterable[ManagedProcess],
    *,
    grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
) -> None:
    """Terminate every running child, escalating only children that do not stop."""

    children = list(processes)
    running = [process for process in children if process.poll() is None]
    for process in running:
        process.terminate()
    for process in running:
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace_seconds)


def _service_url(host: str, port: int) -> str:
    normalized_host = host.strip()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    return f"http://{normalized_host}:{port}"


def _api_base_url(settings: Settings) -> str:
    """Return the origin for the exact loopback listener started by the demo."""

    return _service_url(settings.api_host, settings.api_port)


def _dashboard_url(settings: Settings) -> str:
    return _service_url(settings.dashboard_host, settings.dashboard_port)


def _demo_environment(
    settings: Settings,
    *,
    api_url: str,
    dashboard: bool,
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build consistent child settings without giving provider keys to Streamlit."""

    environment = dict(os.environ if base_environment is None else base_environment)
    environment.update(
        {
            "EVALFORGE_API_HOST": settings.api_host,
            "EVALFORGE_API_PORT": str(settings.api_port),
            "EVALFORGE_API_URL": api_url,
            "EVALFORGE_DASHBOARD_HOST": settings.dashboard_host,
            "EVALFORGE_DASHBOARD_PORT": str(settings.dashboard_port),
        }
    )
    if dashboard:
        for key in DASHBOARD_PROVIDER_SECRET_KEYS:
            environment[key] = _SETTINGS_NONE_OVERRIDE
    return environment


def dashboard_environment(
    settings: Settings,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the standalone dashboard environment without backend provider secrets."""

    return _demo_environment(
        settings,
        api_url=str(settings.api_url).rstrip("/"),
        dashboard=True,
        base_environment=base_environment,
    )


def run_demo(
    settings: Settings,
    *,
    emit: Emitter,
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> None:
    """Prepare, start, report, supervise, and cleanly stop the offline demo."""

    emit("Preparing the offline demo...")
    counts = prepare_demo(settings)
    emit(
        "Demo workspace ready "
        f"({counts['datasets']} benchmarks, {counts['prompts']} prompts, "
        f"{counts['models']} model profiles)."
    )

    api_url = _api_base_url(settings)
    dashboard_url = _dashboard_url(settings)
    api_environment = _demo_environment(settings, api_url=api_url, dashboard=False)
    dashboard_environment = _demo_environment(settings, api_url=api_url, dashboard=True)
    children: list[ManagedProcess] = []
    services: list[tuple[str, ManagedProcess]] = []
    try:
        api_process = subprocess.Popen(  # noqa: S603  # nosec B603
            api_command(settings),
            env=api_environment,
        )
        children.append(api_process)
        services.append(("API", api_process))
        wait_until_ready(
            api_process,
            f"{api_url}/health/ready",
            timeout_seconds=startup_timeout_seconds,
        )
        emit(f"API ready at {api_url}")

        ui_process = subprocess.Popen(  # noqa: S603  # nosec B603
            ui_command(settings),
            env=dashboard_environment,
        )
        children.append(ui_process)
        services.append(("Dashboard", ui_process))
        wait_until_ready(
            ui_process,
            f"{dashboard_url}/_stcore/health",
            timeout_seconds=startup_timeout_seconds,
        )
        emit(f"EvalForge is ready at {dashboard_url}")
        emit("Press Ctrl+C to stop.")
        supervise(services)
    except KeyboardInterrupt:
        emit("Stopping EvalForge...")
    finally:
        stop_processes(children)
