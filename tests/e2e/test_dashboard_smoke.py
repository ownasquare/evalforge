"""Deterministic browser smoke through the real Streamlit-to-FastAPI boundary."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

DASHBOARD_URL = os.environ.get("EVALFORGE_DASHBOARD_URL", "http://127.0.0.1:8501/")
ROOT = Path(__file__).parents[2]
STREAMLIT_LAUNCHER = ROOT / "src/evalforge/streamlit_app.py"

COLD_ROUTES = (
    ("/", "Evaluation workspace"),
    ("/evaluate", "New evaluation"),
    ("/runs", "Results"),
    ("/compare", "Compare"),
    ("/assets", "Benchmarks"),
    ("/models", "Models"),
    ("/settings", "Settings"),
)


def _unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def cold_dashboard_url(tmp_path: Path) -> Iterator[str]:
    port = _unused_loopback_port()
    base_url = f"http://127.0.0.1:{port}/"
    environment = os.environ.copy()
    environment.setdefault("EVALFORGE_API_URL", "http://127.0.0.1:8000")
    log_path = tmp_path / "streamlit.log"

    with log_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(  # noqa: S603 - fixed local test command
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(STREAMLIT_LAUNCHER),
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(port),
                "--server.headless",
                "true",
            ],
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output.flush()
                    pytest.fail(f"Streamlit exited during startup:\n{log_path.read_text()}")
                try:
                    response = httpx.get(f"{base_url}_stcore/health", timeout=0.5)
                    if response.is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                output.flush()
                pytest.fail(f"Streamlit did not become healthy:\n{log_path.read_text()}")

            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _open_first_session(page: Any, base_url: str, path: str, heading: str) -> list[str]:
    from playwright.sync_api import expect

    errors: list[str] = []
    target = base_url if path == "/" else f"{base_url}{path.lstrip('/')}"
    known_streamlit_probe_urls = {
        f"{target}/_stcore/health",
        f"{target}/_stcore/host-config",
    }
    known_streamlit_probe_error = (
        "Failed to load resource: the server responded with a status of 404 (Not Found)"
    )

    def record_console_error(message: Any) -> None:
        if message.type != "error":
            return
        location_url = str(message.location.get("url", ""))
        # Streamlit sends two redundant relative probes on direct subpages.
        # Keep rejecting every other error while this upstream issue remains open:
        # https://github.com/streamlit/streamlit/issues/7074
        if (
            path != "/"
            and message.text == known_streamlit_probe_error
            and location_url in known_streamlit_probe_urls
        ):
            return
        errors.append(f"{message.text} @ {message.location}")

    page.on("console", record_console_error)
    page.on("pageerror", lambda error: errors.append(str(error)))

    page.goto(target, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible(timeout=20_000)
    expect(page.get_by_text("Page not found", exact=True)).to_have_count(0)
    assert page.url == target
    page.wait_for_timeout(250)
    assert errors == [], "\n".join(errors)
    return errors


@pytest.mark.e2e
def test_dashboard_runs_a_seeded_evaluation(page) -> None:
    from playwright.sync_api import expect

    page.goto(DASHBOARD_URL, wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="Evaluation workspace")).to_be_visible(timeout=20_000)
    page.get_by_role("link", name="New evaluation", exact=False).click()
    expect(page.get_by_role("heading", name="New evaluation")).to_be_visible()
    page.get_by_role("textbox", name="Run name", exact=True).fill("E2E grounded answer review")
    expect(page.get_by_role("button", name="Check setup")).to_have_count(0)
    page.get_by_role("button", name="Start evaluation").click()
    expect(
        page.get_by_text("Evaluation completed. Results are ready to inspect.", exact=True)
    ).to_be_visible(timeout=20_000)
    expect(page.get_by_role("button", name="Review results")).to_be_visible()
    page.get_by_role("button", name="Review results").click()
    expect(page.get_by_role("heading", name="Results", exact=True)).to_be_visible()
    expect(page.get_by_text("E2E grounded answer review", exact=True)).to_be_visible()


@pytest.mark.e2e
@pytest.mark.parametrize(("path", "heading"), COLD_ROUTES)
def test_dashboard_routes_load_as_the_first_cold_session(
    page, cold_dashboard_url: str, path: str, heading: str
) -> None:
    _open_first_session(page, cold_dashboard_url, path, heading)


@pytest.mark.e2e
def test_dashboard_warm_navigation_preserves_browser_history(page, cold_dashboard_url: str) -> None:
    from playwright.sync_api import expect

    errors = _open_first_session(page, cold_dashboard_url, "/", "Evaluation workspace")
    page.get_by_role("link", name="New evaluation", exact=False).click()
    expect(page.get_by_role("heading", name="New evaluation", exact=True)).to_be_visible()
    assert page.url == f"{cold_dashboard_url}evaluate"

    page.go_back(wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Evaluation workspace", exact=True)).to_be_visible()
    assert page.url == cold_dashboard_url

    page.go_forward(wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="New evaluation", exact=True)).to_be_visible()
    assert page.url == f"{cold_dashboard_url}evaluate"
    assert errors == []


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("path", "heading"),
    (
        ("/", "Evaluation workspace"),
        ("/evaluate", "New evaluation"),
        ("/runs", "Results"),
        ("/models", "Models"),
    ),
)
def test_dashboard_cold_routes_fit_mobile_viewport(
    page, cold_dashboard_url: str, path: str, heading: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _open_first_session(page, cold_dashboard_url, path, heading)
    width = page.evaluate(
        "() => ({ scrollWidth: document.documentElement.scrollWidth, "
        "clientWidth: document.documentElement.clientWidth })"
    )
    assert width["scrollWidth"] <= width["clientWidth"]
