from __future__ import annotations

import re
import tomllib
from pathlib import Path

from evalforge import __version__
from evalforge.config import Settings

ROOT = Path(__file__).parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
DEPENDABOT_CONFIG = ROOT / ".github/dependabot.yml"
ENV_EXAMPLE = ROOT / ".env.example"
RELEASE_NOTES = ROOT / "docs/releases/v0.1.0.md"

APPROVED_ACTION_PINS = {
    "actions/checkout": (
        "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "v7.0.0",
    ),
    "astral-sh/setup-uv": (
        "11f9893b081a58869d3b5fccaea48c9e9e46f990",
        "v8.3.2",
    ),
    "docker/setup-buildx-action": (
        "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
        "v4.2.0",
    ),
    "docker/build-push-action": (
        "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
        "v7.3.0",
    ),
}
EXPECTED_WORKFLOW_ACTIONS = {
    CI_WORKFLOW: set(APPROVED_ACTION_PINS),
    RELEASE_WORKFLOW: {"actions/checkout", "astral-sh/setup-uv"},
}
SHIPPED_CHANGELOG_ITEMS = {
    "- Simplified first-run guidance and public project documentation.",
    "- Added tested source-extension examples and community templates.",
}
ACTION_LINE = re.compile(
    r"^\s*-\s+uses:\s+(?P<action>[^@\s]+)@(?P<ref>[0-9a-f]{40})"
    r"\s+#\s+(?P<tag>v\d+\.\d+\.\d+)\s*$"
)


def _changelog_section(text: str, version: str) -> str:
    match = re.search(
        rf"^## \[{re.escape(version)}\](?: - [^\n]+)?\n(?P<body>.*?)(?=^## \[|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"Missing changelog section for {version}"
    return match.group("body")


def _changed_subsection(section: str) -> str:
    match = re.search(
        r"^### Changed\n(?P<body>.*?)(?=^### |\Z)",
        section,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "Changelog section must contain a Changed subsection"
    return match.group("body")


def test_release_version_is_consistent_across_runtime_and_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = CHANGELOG.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert project["project"]["version"] == __version__
    assert Settings.model_fields["application_version"].default == __version__
    assert re.search(
        rf"^EVALFORGE_APPLICATION_VERSION={re.escape(__version__)}$",
        env_example,
        flags=re.MULTILINE,
    )
    assert re.search(
        rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        changelog,
        flags=re.MULTILINE,
    )


def test_changelog_has_a_clean_unreleased_section_and_shipped_release_notes() -> None:
    changelog = CHANGELOG.read_text(encoding="utf-8")
    unreleased_changed = _changed_subsection(_changelog_section(changelog, "Unreleased"))
    released_changed = _changed_subsection(_changelog_section(changelog, __version__))

    assert unreleased_changed.strip() == "- No changes yet."
    assert not SHIPPED_CHANGELOG_ITEMS.intersection(unreleased_changed.splitlines())
    assert set(released_changed.splitlines()) >= SHIPPED_CHANGELOG_ITEMS


def test_ci_and_release_actions_use_only_exact_approved_sha_pins() -> None:
    for workflow, expected_actions in EXPECTED_WORKFLOW_ACTIONS.items():
        assert workflow.is_file(), f"Missing workflow: {workflow.relative_to(ROOT)}"
        uses_lines = [
            line
            for line in workflow.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*-\s+uses:", line)
        ]
        assert uses_lines, f"No external actions found in {workflow.relative_to(ROOT)}"

        observed_actions: set[str] = set()
        for line in uses_lines:
            match = ACTION_LINE.fullmatch(line)
            assert match is not None, f"Action must use an approved SHA and tag comment: {line}"
            action = match.group("action")
            observed_actions.add(action)
            assert action in APPROVED_ACTION_PINS, f"Unapproved action: {action}"
            assert (match.group("ref"), match.group("tag")) == APPROVED_ACTION_PINS[action]

        assert observed_actions == expected_actions


def test_dependabot_refreshes_action_pins_on_a_low_noise_schedule() -> None:
    assert DEPENDABOT_CONFIG.is_file()
    config = DEPENDABOT_CONFIG.read_text(encoding="utf-8")

    assert re.search(r"^version:\s*2$", config, flags=re.MULTILINE)
    assert config.count("package-ecosystem:") == 1
    assert re.search(r'^\s*- package-ecosystem:\s*"github-actions"$', config, re.MULTILINE)
    assert re.search(r'^\s+directory:\s*"/"$', config, re.MULTILINE)
    assert re.search(r'^\s+interval:\s*"monthly"$', config, re.MULTILINE)
    assert re.search(r"^\s+open-pull-requests-limit:\s*3$", config, re.MULTILINE)


def test_release_workflow_builds_smokes_checksums_and_publishes_verified_tags() -> None:
    assert RELEASE_WORKFLOW.is_file()
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r'^\s+tags:\s*\n\s+-\s*["\']v\*\.\*\.\*["\']$', workflow, re.MULTILINE)
    assert re.search(r"^permissions:\s*\n\s+contents:\s*write$", workflow, re.MULTILINE)
    assert "uv build" in workflow
    assert "uv python install 3.11 3.12" in workflow
    assert "for python_version in 3.11 3.12" in workflow
    assert "uv pip install" in workflow
    assert 'bin/evalforge" --help' in workflow
    assert re.search(r"(?:sha256sum|shasum\s+-a\s+256).*SHA256SUMS", workflow)
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert "GH_TOKEN:" in workflow
    assert "${{ github.token }}" in workflow


def test_versioned_release_notes_preserve_the_public_beta_boundary() -> None:
    assert RELEASE_NOTES.is_file()
    notes = RELEASE_NOTES.read_text(encoding="utf-8")

    assert "local-first public beta" in notes
    assert "key-free and works offline" in notes
    assert "does not certify hosted OIDC" in notes
    assert "paid-provider calibration" in notes
    assert "not published to PyPI or a container registry" in notes
