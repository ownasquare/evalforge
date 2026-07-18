from __future__ import annotations

import re
from pathlib import Path

from evalforge.api.routes.datasets import _case_from_mapping, _decode_import

ROOT = Path(__file__).parents[2]
IGNORED_PARTS = {
    ".data",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "playwright-report",
    "test-results",
}
INTERNAL_ARTIFACT_PREFIXES = {
    ("docs", "handoffs"),
    ("docs", "superpowers", "plans"),
}


def _ignored(relative_path: Path) -> bool:
    parts = relative_path.parts
    if any(part in IGNORED_PARTS for part in parts):
        return True
    return any(parts[: len(prefix)] == prefix for prefix in INTERNAL_ARTIFACT_PREFIXES)


def _public_text_files() -> list[Path]:
    paths: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or _ignored(path.relative_to(ROOT)):
            continue
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        paths.append(path)
    return paths


def test_public_adoption_files_and_templates_exist() -> None:
    required = {
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "SUPPORT.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "docs/extending.md",
        "docs/getting-started.md",
        "docs/troubleshooting.md",
        "examples/extensions/custom_adapter.py",
        "examples/extensions/custom_evaluator.py",
        "examples/extensions/custom_export_sink.py",
        "examples/customer-support.csv",
    }

    assert not {path for path in required if not (ROOT / path).is_file()}


def test_internal_execution_artifacts_are_ignored_from_public_tree() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docs/handoffs/" in ignore
    assert "docs/superpowers/plans/" in ignore
    assert "*.handoff.mdc" in ignore


def test_public_text_has_no_machine_specific_or_sibling_project_references() -> None:
    banned = (
        "/" + "Users" + "/",
        "/" + "private" + "/" + "tmp" + "/",
        "bel" + "aded",
        "personal" + "-rag-system",
        "codebase" + "-intelligence",
    )
    violations: list[str] = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in banned):
            violations.append(str(path.relative_to(ROOT)))

    assert not violations


def test_public_markdown_relative_links_resolve() -> None:
    broken: list[str] = []
    link_pattern = re.compile(r"\[[^]]+\]\((?!https?://|mailto:)([^)#]+)")
    for path in ROOT.rglob("*.md"):
        if _ignored(path.relative_to(ROOT)):
            continue
        for target in link_pattern.findall(path.read_text(encoding="utf-8")):
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")

    assert not broken


def test_copyable_csv_benchmark_example_matches_the_import_contract() -> None:
    example = ROOT / "examples/customer-support.csv"
    rows = _decode_import(example.name, example.read_text(encoding="utf-8"))

    cases = [_case_from_mapping(row, position=index) for index, row in enumerate(rows)]

    assert [case.external_id for case in cases] == ["refund-window", "password-reset"]
    assert cases[0].required_phrases == ["30 days"]
    assert cases[1].metadata_json["relevance_keywords"] == ["account email", "reset link"]
