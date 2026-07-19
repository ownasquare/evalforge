from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import stat
from pathlib import Path

import pytest

from evalforge.evaluation.calibration_io import (
    LABEL_SCHEMA_VERSION,
    MAX_CALIBRATION_FILE_BYTES,
    MAX_CALIBRATION_LABELS,
    REPORT_SCHEMA_VERSION,
    CalibrationInputError,
    CalibrationLabelManifest,
    CalibrationTemplateRow,
    LocalCalibrationReportSink,
    build_calibration_report,
    canonical_manifest_bytes,
    load_calibration_manifest,
    load_calibration_manifest_bytes,
    manifest_sha256,
    render_calibration_template,
)

CSV_COLUMNS = (
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
)


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": LABEL_SCHEMA_VERSION,
        "dataset": {
            "id": "customer-support",
            "version": "1.0.0",
            "sha256": "8d8f0d74572749536603faef69fbc4862e117a4a6350c76e8e76f810e8492c70",
        },
        "metric": {
            "name": "correctness",
            "version": "lexical-correctness-v1",
            "direction": "higher_is_better",
        },
        "labels": [
            {
                "item_id": "refund-window",
                "score": 0.92,
                "human_passed": True,
                "reviewer_id": "reviewer-01",
            },
            {
                "item_id": "account-lockout",
                "score": 0.4,
                "human_passed": False,
                "reviewer_id": "reviewer-02",
            },
            {
                "item_id": "password-reset",
                "score": 0.81,
                "human_passed": True,
                "reviewer_id": "reviewer-01",
            },
        ],
    }


def _write_json(path: Path, payload: object | None = None) -> Path:
    path.write_text(
        json.dumps(_manifest_payload() if payload is None else payload),
        encoding="utf-8",
    )
    return path


def _write_csv(path: Path, payload: dict[str, object] | None = None) -> Path:
    source = _manifest_payload() if payload is None else payload
    dataset = source["dataset"]
    metric = source["metric"]
    labels = source["labels"]
    assert isinstance(dataset, dict)
    assert isinstance(metric, dict)
    assert isinstance(labels, list)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for label in reversed(labels):
            assert isinstance(label, dict)
            writer.writerow(
                {
                    "schema_version": source["schema_version"],
                    "dataset_id": dataset["id"],
                    "dataset_version": dataset["version"],
                    "dataset_sha256": dataset["sha256"],
                    "metric_name": metric["name"],
                    "metric_version": metric["version"],
                    "direction": metric["direction"],
                    "item_id": label["item_id"],
                    "score": label["score"],
                    "human_passed": str(label["human_passed"]).lower(),
                    "reviewer_id": label["reviewer_id"],
                }
            )
    return path


def _manifest() -> CalibrationLabelManifest:
    return CalibrationLabelManifest.model_validate(_manifest_payload())


def test_json_and_csv_load_to_equal_order_independent_canonical_manifests(
    tmp_path: Path,
) -> None:
    json_manifest = load_calibration_manifest(_write_json(tmp_path / "labels.json"))
    csv_manifest = load_calibration_manifest(_write_csv(tmp_path / "labels.csv"))
    reversed_payload = _manifest_payload()
    labels = reversed_payload["labels"]
    assert isinstance(labels, list)
    labels.reverse()
    reversed_manifest = load_calibration_manifest(
        _write_json(tmp_path / "reversed.json", reversed_payload)
    )

    assert json_manifest == csv_manifest
    assert canonical_manifest_bytes(json_manifest) == canonical_manifest_bytes(csv_manifest)
    assert canonical_manifest_bytes(json_manifest) == canonical_manifest_bytes(reversed_manifest)
    assert manifest_sha256(json_manifest) == manifest_sha256(csv_manifest)
    assert (
        manifest_sha256(json_manifest)
        == hashlib.sha256(canonical_manifest_bytes(json_manifest)).hexdigest()
    )


def test_signed_zero_scores_have_one_canonical_manifest_identity() -> None:
    signed_payload = _manifest_payload()
    unsigned_payload = _manifest_payload()
    signed_labels = signed_payload["labels"]
    unsigned_labels = unsigned_payload["labels"]
    assert isinstance(signed_labels, list)
    assert isinstance(unsigned_labels, list)
    assert isinstance(signed_labels[0], dict)
    assert isinstance(unsigned_labels[0], dict)
    signed_labels[0]["score"] = -0.0
    unsigned_labels[0]["score"] = 0.0

    signed = CalibrationLabelManifest.model_validate(signed_payload)
    unsigned = CalibrationLabelManifest.model_validate(unsigned_payload)

    assert canonical_manifest_bytes(signed) == canonical_manifest_bytes(unsigned)
    assert manifest_sha256(signed) == manifest_sha256(unsigned)


def test_bounded_upload_parser_matches_path_loader_and_rejects_unsafe_bytes(
    tmp_path: Path,
) -> None:
    json_path = _write_json(tmp_path / "labels.json")
    csv_path = _write_csv(tmp_path / "labels.csv")

    assert load_calibration_manifest_bytes(
        json_path.read_bytes(), filename="labels.json"
    ) == load_calibration_manifest(json_path)
    assert load_calibration_manifest_bytes(
        csv_path.read_bytes(), filename="labels.csv"
    ) == load_calibration_manifest(csv_path)

    with pytest.raises(CalibrationInputError, match="UTF-8"):
        load_calibration_manifest_bytes(b"\xff", filename="labels.json")
    with pytest.raises(CalibrationInputError, match="2 MiB"):
        load_calibration_manifest_bytes(
            b"x" * (MAX_CALIBRATION_FILE_BYTES + 1), filename="labels.csv"
        )
    with pytest.raises(CalibrationInputError, match="invalid"):
        load_calibration_manifest_bytes(
            b'{"schema_version":"first","schema_version":"second"}',
            filename="labels.json",
        )
    with pytest.raises(CalibrationInputError, match="JSON or CSV"):
        load_calibration_manifest_bytes(b"{}", filename="labels.txt")


def test_template_rendering_is_canonical_and_contains_empty_reviewer_fields() -> None:
    manifest = _manifest()
    positioned = [
        CalibrationTemplateRow(
            item_id=row.item_id,
            case_position=position,
            case_external_id=f"case-{position + 1}",
            score=row.score,
        )
        for position, row in enumerate(manifest.labels)
    ]
    rows = tuple(reversed(positioned))

    json_template = render_calibration_template(
        dataset=manifest.dataset,
        metric=manifest.metric,
        rows=rows,
        file_format="json",
    )
    repeated = render_calibration_template(
        dataset=manifest.dataset,
        metric=manifest.metric,
        rows=tuple(reversed(rows)),
        file_format="json",
    )
    csv_template = render_calibration_template(
        dataset=manifest.dataset,
        metric=manifest.metric,
        rows=rows,
        file_format="csv",
    )

    payload = json.loads(json_template)
    assert json_template == repeated
    assert [row["item_id"] for row in payload["labels"]] == [row.item_id for row in positioned]
    assert [row["case_external_id"] for row in payload["labels"]] == [
        row.case_external_id for row in positioned
    ]
    assert [row["case_position"] for row in payload["labels"]] == [0, 1, 2]
    assert all(row["human_passed"] is None for row in payload["labels"])
    assert all(row["reviewer_id"] == "" for row in payload["labels"])
    csv_rows = list(csv.DictReader(csv_template.decode("utf-8").splitlines()))
    assert [row["item_id"] for row in csv_rows] == [row.item_id for row in positioned]
    assert [row["case_external_id"] for row in csv_rows] == [
        row.case_external_id for row in positioned
    ]
    assert [row["case_position"] for row in csv_rows] == ["0", "1", "2"]
    assert all(row["human_passed"] == "" for row in csv_rows)
    assert all(row["reviewer_id"] == "" for row in csv_rows)


@pytest.mark.parametrize("dangerous_prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_template_formula_safes_case_labels(dangerous_prefix: str) -> None:
    manifest = _manifest()
    rendered = render_calibration_template(
        dataset=manifest.dataset,
        metric=manifest.metric,
        rows=(
            CalibrationTemplateRow(
                item_id="result-1",
                case_position=0,
                case_external_id=f"{dangerous_prefix}unsafe",
                score=0.5,
            ),
        ),
        file_format="csv",
    )
    rows = list(csv.DictReader(io.StringIO(rendered.decode("utf-8"), newline="")))

    assert rows[0]["case_external_id"] == f"'{dangerous_prefix}unsafe"
    assert not rows[0]["case_external_id"].startswith(("=", "+", "-", "@", "\t", "\r"))


def test_review_mapping_is_verified_metadata_not_a_format_dependent_manifest_hash() -> None:
    manifest = _manifest()
    template_rows = (
        CalibrationTemplateRow(
            item_id="result-1",
            case_position=0,
            case_external_id="+formula-shaped-case",
            score=0.5,
        ),
    )
    json_payload = json.loads(
        render_calibration_template(
            dataset=manifest.dataset,
            metric=manifest.metric,
            rows=template_rows,
            file_format="json",
        )
    )
    json_payload["labels"][0].update({"human_passed": True, "reviewer_id": "reviewer-01"})
    json_manifest = load_calibration_manifest_bytes(
        json.dumps(json_payload).encode("utf-8"),
        filename="labels.json",
    )

    csv_template = render_calibration_template(
        dataset=manifest.dataset,
        metric=manifest.metric,
        rows=template_rows,
        file_format="csv",
    )
    csv_rows = list(csv.DictReader(io.StringIO(csv_template.decode("utf-8"), newline="")))
    csv_rows[0].update({"human_passed": "true", "reviewer_id": "reviewer-01"})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=csv_rows[0].keys(), lineterminator="\n")
    writer.writeheader()
    writer.writerows(csv_rows)
    csv_manifest = load_calibration_manifest_bytes(
        stream.getvalue().encode("utf-8"),
        filename="labels.csv",
    )

    assert json_manifest.labels[0].case_external_id == "+formula-shaped-case"
    assert csv_manifest.labels[0].case_external_id == "'+formula-shaped-case"
    assert canonical_manifest_bytes(json_manifest) == canonical_manifest_bytes(csv_manifest)
    assert manifest_sha256(json_manifest) == manifest_sha256(csv_manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unknown": "private-input"}),
        lambda value: value.update({"schema_version": "evalforge.calibration-labels.v2"}),
        lambda value: value["dataset"].update({"sha256": "A" * 64}),
        lambda value: value["labels"][0].update({"score": math.nan}),
        lambda value: value["labels"][0].update({"score": 1.01}),
        lambda value: value["labels"][0].update({"human_passed": "true"}),
        lambda value: value["labels"][0].update({"reviewer_id": "person@example.com"}),
        lambda value: value["labels"][0].update({"reviewer_id": "reviewer 01"}),
        lambda value: value["labels"][0].update({"item_id": " refund-window"}),
        lambda value: value["labels"][0].update({"item_id": "refund\nwindow"}),
    ],
)
def test_json_loader_rejects_invalid_or_unsafe_contract_values(
    tmp_path: Path,
    mutation: object,
) -> None:
    payload = _manifest_payload()
    assert callable(mutation)
    mutation(payload)

    with pytest.raises(CalibrationInputError) as raised:
        load_calibration_manifest(_write_json(tmp_path / "labels.json", payload))

    assert "private-input" not in str(raised.value)
    assert "person@example.com" not in str(raised.value)


def test_loader_rejects_unsupported_suffix_invalid_utf8_and_oversized_file(
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "labels.txt"
    unsupported.write_text("{}", encoding="utf-8")
    invalid_utf8 = tmp_path / "labels.json"
    invalid_utf8.write_bytes(b"\xff\xfe")
    oversized = tmp_path / "large.csv"
    oversized.write_bytes(b"x" * (MAX_CALIBRATION_FILE_BYTES + 1))

    with pytest.raises(CalibrationInputError, match="JSON or CSV"):
        load_calibration_manifest(unsupported)
    with pytest.raises(CalibrationInputError, match="UTF-8"):
        load_calibration_manifest(invalid_utf8)
    with pytest.raises(CalibrationInputError, match="2 MiB"):
        load_calibration_manifest(oversized)


def test_loader_rejects_empty_duplicate_and_excessive_label_sets(tmp_path: Path) -> None:
    empty = _manifest_payload()
    empty["labels"] = []
    duplicate = _manifest_payload()
    labels = duplicate["labels"]
    assert isinstance(labels, list)
    labels[1]["item_id"] = labels[0]["item_id"]
    excessive = _manifest_payload()
    template = excessive["labels"][0]
    excessive["labels"] = [
        {**template, "item_id": f"case-{index:05d}"} for index in range(MAX_CALIBRATION_LABELS + 1)
    ]

    for name, payload in (
        ("empty.json", empty),
        ("duplicate.json", duplicate),
        ("excessive.json", excessive),
    ):
        with pytest.raises(CalibrationInputError):
            load_calibration_manifest(_write_json(tmp_path / name, payload))


def test_csv_loader_requires_exact_columns_uniform_metadata_and_lowercase_boolean(
    tmp_path: Path,
) -> None:
    valid = _write_csv(tmp_path / "labels.csv")
    rows = list(csv.DictReader(valid.read_text(encoding="utf-8").splitlines()))

    wrong_columns = tmp_path / "wrong-columns.csv"
    with wrong_columns.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS[:-1])
        writer.writeheader()
        writer.writerow({key: rows[0][key] for key in CSV_COLUMNS[:-1]})

    mixed = tmp_path / "mixed.csv"
    rows[1]["metric_version"] = "different-version"
    _write_raw_csv(mixed, rows)

    invalid_boolean = tmp_path / "invalid-boolean.csv"
    rows[1]["metric_version"] = rows[0]["metric_version"]
    rows[0]["human_passed"] = "True"
    _write_raw_csv(invalid_boolean, rows)

    for path in (wrong_columns, mixed, invalid_boolean):
        with pytest.raises(CalibrationInputError):
            load_calibration_manifest(path)


def _write_raw_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_report_preserves_threshold_result_without_disclosing_label_content() -> None:
    package = build_calibration_report(_manifest(), selected_threshold=0.7)
    payload = package.payload

    assert package.schema_version == REPORT_SCHEMA_VERSION
    assert package.payload_sha256 == hashlib.sha256(package.payload_bytes).hexdigest()
    assert package.label_manifest_sha256 == manifest_sha256(_manifest())
    assert payload == {
        "calibration_set_sha256": payload["calibration_set_sha256"],
        "confusion_matrix": {
            "false_negative": 0,
            "false_positive": 0,
            "true_negative": 1,
            "true_positive": 2,
        },
        "dataset": {
            "id": "customer-support",
            "sha256": "8d8f0d74572749536603faef69fbc4862e117a4a6350c76e8e76f810e8492c70",
            "version": "1.0.0",
        },
        "evidence_kind": "offline_statistical_evidence",
        "f1": 1.0,
        "human_fail_count": 1,
        "human_pass_count": 2,
        "label_manifest_sha256": manifest_sha256(_manifest()),
        "metric": {
            "direction": "higher_is_better",
            "name": "correctness",
            "version": "lexical-correctness-v1",
        },
        "precision": 1.0,
        "production_validated": False,
        "recall": 1.0,
        "reviewer_count": 2,
        "sample_size": 3,
        "schema_version": REPORT_SCHEMA_VERSION,
        "selected_threshold": 0.7,
    }
    serialized = package.payload_bytes.decode("utf-8")
    for private_value in (
        "refund-window",
        "account-lockout",
        "password-reset",
        "reviewer-01",
        "reviewer-02",
        "person@example.com",
    ):
        assert private_value not in serialized


def test_report_payload_is_deterministic_for_reordered_equivalent_inputs() -> None:
    source = _manifest_payload()
    labels = source["labels"]
    assert isinstance(labels, list)
    reversed_source = {**source, "labels": list(reversed(labels))}

    first = build_calibration_report(_manifest(), selected_threshold=0.7)
    second = build_calibration_report(
        CalibrationLabelManifest.model_validate(reversed_source),
        selected_threshold=0.7,
    )

    assert first.payload_bytes == second.payload_bytes
    assert first.payload_sha256 == second.payload_sha256
    assert first.label_manifest_sha256 == second.label_manifest_sha256
    assert b"\n" not in first.payload_bytes
    assert b": " not in first.payload_bytes


def test_local_sink_is_private_and_idempotent(tmp_path: Path) -> None:
    package = build_calibration_report(_manifest(), selected_threshold=0.7)
    sink = LocalCalibrationReportSink(tmp_path / "reports")

    first = sink.export(package)
    second = sink.export(package)
    destination = Path(first.location)

    assert first.status == "created"
    assert second.status == "already_exists"
    assert first.location == second.location
    assert first.payload_sha256 == second.payload_sha256 == package.payload_sha256
    assert first.label_manifest_sha256 == package.label_manifest_sha256
    assert destination.name == f"evalforge-calibration-{package.payload_sha256}.json"
    assert destination.read_bytes() == package.payload_bytes
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert list(destination.parent.glob(".*.tmp")) == []


def test_local_sink_rejects_symlink_and_tampered_destinations(tmp_path: Path) -> None:
    package = build_calibration_report(_manifest(), selected_threshold=0.7)

    symlink_root = tmp_path / "symlink-reports"
    symlink_root.mkdir()
    symlink_sink = LocalCalibrationReportSink(symlink_root)
    symlink_destination = symlink_sink.destination_for(package)
    target = tmp_path / "target.json"
    target.write_bytes(package.payload_bytes)
    symlink_destination.symlink_to(target)
    with pytest.raises(CalibrationInputError, match="integrity"):
        symlink_sink.export(package)

    tamper_sink = LocalCalibrationReportSink(tmp_path / "tamper-reports")
    receipt = tamper_sink.export(package)
    Path(receipt.location).write_text("tampered private content", encoding="utf-8")
    with pytest.raises(CalibrationInputError) as raised:
        tamper_sink.export(package)
    assert "tampered private content" not in str(raised.value)
    assert list(Path(receipt.location).parent.glob(".*.tmp")) == []


def test_local_sink_removes_temporary_file_after_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = build_calibration_report(_manifest(), selected_threshold=0.7)
    root = tmp_path / "reports"
    sink = LocalCalibrationReportSink(root)

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("private filesystem path")

    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(CalibrationInputError) as raised:
        sink.export(package)

    assert "private filesystem path" not in str(raised.value)
    assert list(root.glob(".*.tmp")) == []
