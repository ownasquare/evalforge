from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from evalforge.api.routes.datasets import _case_from_mapping, _decode_import
from evalforge.evaluation.calibration_io import (
    build_calibration_report,
    canonical_manifest_bytes,
    load_calibration_manifest,
    manifest_sha256,
)

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
        ".github/ISSUE_TEMPLATE/question.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "docs/README.md",
        "docs/extending.md",
        "docs/getting-started.md",
        "docs/troubleshooting.md",
        "examples/calibration-labels.csv",
        "examples/calibration-labels.json",
        "examples/extensions/custom_adapter.py",
        "examples/extensions/custom_evaluator.py",
        "examples/extensions/custom_export_sink.py",
        "examples/customer-support.csv",
    }

    assert not {path for path in required if not (ROOT / path).is_file()}


def test_first_run_instructions_start_from_a_fresh_clone() -> None:
    for relative_path in ("README.md", "docs/getting-started.md"):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "git clone https://github.com/ownasquare/evalforge.git" in text
        assert "cd evalforge" in text
        assert text.index("git clone") < text.index("uv sync --frozen")


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


def test_copyable_calibration_examples_are_equivalent_offline_evidence() -> None:
    json_path = ROOT / "examples/calibration-labels.json"
    csv_path = ROOT / "examples/calibration-labels.csv"

    json_manifest = load_calibration_manifest(json_path)
    csv_manifest = load_calibration_manifest(csv_path)

    assert json_manifest == csv_manifest
    assert canonical_manifest_bytes(json_manifest) == canonical_manifest_bytes(csv_manifest)
    assert manifest_sha256(json_manifest) == manifest_sha256(csv_manifest)
    assert len(json_manifest.labels) == 5
    source_dataset_sha256 = hashlib.sha256(
        (ROOT / "examples/customer-support.json").read_bytes()
    ).hexdigest()
    assert json_manifest.dataset.sha256 == source_dataset_sha256

    payload = json.loads(
        build_calibration_report(json_manifest, selected_threshold=0.7).payload_bytes
    )
    assert payload["production_validated"] is False
    assert payload["evidence_kind"] == "offline_statistical_evidence"

    json_document = json.loads(json_path.read_text(encoding="utf-8"))
    public_field_names = set(csv_path.read_text(encoding="utf-8").splitlines()[0].split(","))
    pending_nodes = [json_document]
    while pending_nodes:
        node = pending_nodes.pop()
        if isinstance(node, dict):
            public_field_names.update(node)
            pending_nodes.extend(node.values())
        elif isinstance(node, list):
            pending_nodes.extend(node)

    forbidden_fields = {
        "email",
        "api_key",
        "credential",
        "provider_api_key",
        "provider_credential",
        "provider_secret",
        "secret",
    }
    assert public_field_names.isdisjoint(forbidden_fields)
