from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from click import unstyle
from pydantic import SecretStr
from typer.testing import CliRunner

from evalforge import cli
from evalforge.evaluation.adapters.openai_compatible import OpenAICompatibleAdapter
from evalforge.evaluation.adapters.registry import AdapterRegistry

DATASET_SHA256 = "8d8f0d74572749536603faef69fbc4862e117a4a6350c76e8e76f810e8492c70"
LABEL_ROWS = (
    ("case-a", 0.9, True, "reviewer-alpha"),
    ("case-b", 0.8, False, "reviewer-bravo"),
    ("case-c", 0.6, True, "reviewer-alpha"),
    ("case-d", 0.2, False, "reviewer-charlie"),
)


def _write_json_labels(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "evalforge.calibration-labels.v1",
                "dataset": {
                    "id": "customer-support",
                    "version": "1.0.0",
                    "sha256": DATASET_SHA256,
                },
                "metric": {
                    "name": "correctness",
                    "version": "lexical-correctness-v1",
                    "direction": "higher_is_better",
                },
                "labels": [
                    {
                        "item_id": item_id,
                        "score": score,
                        "human_passed": human_passed,
                        "reviewer_id": reviewer_id,
                    }
                    for item_id, score, human_passed, reviewer_id in LABEL_ROWS
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_csv_labels(path: Path) -> None:
    fieldnames = [
        "schema_version",
        "dataset_id",
        "dataset_version",
        "dataset_sha256",
        "metric_name",
        "metric_version",
        "direction",
        "item_id",
        "score",
        "human_passed",
        "reviewer_id",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for item_id, score, human_passed, reviewer_id in reversed(LABEL_ROWS):
            writer.writerow(
                {
                    "schema_version": "evalforge.calibration-labels.v1",
                    "dataset_id": "customer-support",
                    "dataset_version": "1.0.0",
                    "dataset_sha256": DATASET_SHA256,
                    "metric_name": "correctness",
                    "metric_version": "lexical-correctness-v1",
                    "direction": "higher_is_better",
                    "item_id": item_id,
                    "score": score,
                    "human_passed": str(human_passed).lower(),
                    "reviewer_id": reviewer_id,
                }
            )


def _invoke_calibration(
    runner: CliRunner,
    labels_file: Path,
    output_directory: Path,
) -> tuple[Any, dict[str, object]]:
    result = runner.invoke(
        cli.app,
        [
            "calibrate",
            str(labels_file),
            "--threshold",
            "0.7",
            "--output-dir",
            str(output_directory),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return result, payload


def test_calibrate_help_explains_the_permanently_offline_workflow() -> None:
    result = CliRunner().invoke(cli.app, ["calibrate", "--help"])

    assert result.exit_code == 0
    output = " ".join(unstyle(result.output).split()).casefold()
    assert "permanently offline" in output
    assert "does not load settings or credentials" in output
    assert "does not" in output and "database" in output
    assert "model provider" in output
    assert "labels_file" in output
    assert "--threshold" in output
    assert "--output-dir" in output


def test_calibrate_json_and_csv_are_idempotent_and_content_minimized(tmp_path: Path) -> None:
    json_labels = tmp_path / "labels.json"
    csv_labels = tmp_path / "labels.csv"
    output_directory = tmp_path / "reports"
    _write_json_labels(json_labels)
    _write_csv_labels(csv_labels)

    first_result, first = _invoke_calibration(CliRunner(), json_labels, output_directory)
    second_result, second = _invoke_calibration(CliRunner(), csv_labels, output_directory)

    assert first["status"] == "created"
    assert second == {**first, "status": "already_exists"}
    assert set(first) == {
        "status",
        "schema_version",
        "dataset_id",
        "dataset_version",
        "metric_name",
        "metric_version",
        "sample_size",
        "human_pass_count",
        "human_fail_count",
        "selected_threshold",
        "direction",
        "precision",
        "recall",
        "f1",
        "payload_sha256",
        "label_manifest_sha256",
        "calibration_set_sha256",
        "production_validated",
        "location",
    }
    assert first == {
        **first,
        "schema_version": "evalforge.calibration-report.v1",
        "dataset_id": "customer-support",
        "dataset_version": "1.0.0",
        "metric_name": "correctness",
        "metric_version": "lexical-correctness-v1",
        "sample_size": 4,
        "human_pass_count": 2,
        "human_fail_count": 2,
        "selected_threshold": 0.7,
        "direction": "higher_is_better",
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "production_validated": False,
    }
    for key in ("payload_sha256", "label_manifest_sha256", "calibration_set_sha256"):
        assert len(str(first[key])) == 64

    report_path = Path(str(first["location"]))
    assert report_path.parent == output_directory
    assert len(list(output_directory.iterdir())) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["production_validated"] is False
    assert report["reviewer_count"] == 3

    combined_output = first_result.output + second_result.output
    assert "\n" not in first_result.output.rstrip("\n")
    for item_id, _score, _human_passed, reviewer_id in LABEL_ROWS:
        assert item_id not in combined_output
        assert reviewer_id not in combined_output


def test_calibrate_malformed_input_writes_nothing_and_reports_a_safe_error(
    tmp_path: Path,
) -> None:
    labels_file = tmp_path / "labels.json"
    output_directory = tmp_path / "reports"
    sensitive_value = "reviewer-private@example.test"
    labels_file.write_text(
        json.dumps({"schema_version": "wrong", "reviewer_id": sensitive_value}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "calibrate",
            str(labels_file),
            "--threshold",
            "0.7",
            "--output-dir",
            str(output_directory),
        ],
    )

    assert result.exit_code != 0
    assert sensitive_value not in unstyle(result.output)
    assert not output_directory.exists()


def test_calibrate_never_loads_settings_database_provider_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    labels_file = tmp_path / "labels.json"
    _write_json_labels(labels_file)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline calibration crossed a runtime boundary")

    for name in (
        "get_settings",
        "apply_migrations",
        "build_container",
        "check_database_readiness",
        "create_database_engine",
        "create_session_factory",
        "session_scope",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(AdapterRegistry, "get", forbidden)
    monkeypatch.setattr(OpenAICompatibleAdapter, "generate", forbidden)
    monkeypatch.setattr(SecretStr, "get_secret_value", forbidden)

    _result, payload = _invoke_calibration(
        CliRunner(),
        labels_file,
        tmp_path / "reports",
    )

    assert payload["status"] == "created"
    assert payload["production_validated"] is False
