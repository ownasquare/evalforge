import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
STREAMLIT_LAUNCHER = "src/evalforge/streamlit_app.py"
STREAMLIT_CONTAINER_WRAPPER = "scripts/start_dashboard.py"
STREAMLIT_LAUNCH_SURFACES = (
    ".github/workflows/ci.yml",
    "docs/operations.md",
    "tests/e2e/test_dashboard_smoke.py",
)


def test_required_project_surfaces_exist() -> None:
    required = {
        "README.md",
        "pyproject.toml",
        "uv.lock",
        ".env.example",
        "Dockerfile.api",
        "Dockerfile.dashboard",
        "compose.yaml",
        "docs/architecture.md",
        "docs/api.md",
        "docs/evaluation-methodology.md",
        "docs/operations.md",
        "docs/security.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "src/evalforge/migrations/env.py",
        "src/evalforge/migrations/versions/0001_initial_schema.py",
        "src/evalforge/migrations/versions/0002_preflight_context_cost_ack.py",
        STREAMLIT_LAUNCHER,
        STREAMLIT_CONTAINER_WRAPPER,
    }
    assert not {path for path in required if not (ROOT / path).exists()}


def test_readme_relative_links_resolve() -> None:
    readme = (ROOT / "README.md").read_text()
    targets = re.findall(r"\[[^]]+\]\((?!https?://)([^)#]+)", readme)
    assert targets
    assert not {target for target in targets if not (ROOT / target).exists()}


def test_environment_example_contains_no_populated_secret() -> None:
    lines = (ROOT / ".env.example").read_text().splitlines()
    secret_lines = [line for line in lines if "API_KEY=" in line]
    assert secret_lines
    assert all(line.endswith("=") for line in secret_lines)


def test_secret_files_and_runtime_data_are_ignored() -> None:
    ignore = set((ROOT / ".gitignore").read_text().splitlines())
    assert {".env", ".env.*", ".data/", "*.db", "*.db-wal"} <= ignore


def test_no_cypress_e2e_configuration_exists() -> None:
    files = {path.name.lower() for path in ROOT.rglob("*") if path.is_file()}
    assert "cypress.config.js" not in files
    assert "cypress.config.ts" not in files


def test_streamlit_launch_surfaces_use_the_neutral_launcher() -> None:
    legacy_launcher = "src/evalforge/dashboard/app.py"

    for path in STREAMLIT_LAUNCH_SURFACES:
        contents = (ROOT / path).read_text()
        assert STREAMLIT_LAUNCHER in contents, path
        assert legacy_launcher not in contents, path

    dashboard_dockerfile = (ROOT / "Dockerfile.dashboard").read_text()
    dashboard_wrapper = (ROOT / STREAMLIT_CONTAINER_WRAPPER).read_text()
    makefile = (ROOT / "Makefile").read_text()
    assert STREAMLIT_CONTAINER_WRAPPER in dashboard_dockerfile
    assert STREAMLIT_LAUNCHER in dashboard_wrapper
    assert "uv run evalforge demo" in makefile
    assert legacy_launcher not in makefile
    assert legacy_launcher not in dashboard_dockerfile
    assert legacy_launcher not in dashboard_wrapper
