import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


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
