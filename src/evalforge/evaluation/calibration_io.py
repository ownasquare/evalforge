"""Strict offline human-label manifests and private calibration reports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evalforge.evaluation.calibration import CalibrationLabel, evaluate_threshold
from evalforge.evaluation.types import MetricDirection

LABEL_SCHEMA_VERSION: Final[str] = "evalforge.calibration-labels.v1"
REPORT_SCHEMA_VERSION: Final[str] = "evalforge.calibration-report.v1"
MAX_CALIBRATION_FILE_BYTES: Final[int] = 2 * 1024 * 1024
MAX_CALIBRATION_LABELS: Final[int] = 10_000

_HASH_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}\Z")
_SAFE_REVIEWER_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CSV_COLUMNS: Final[tuple[str, ...]] = (
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
_CSV_METADATA_COLUMNS: Final[tuple[str, ...]] = _CSV_COLUMNS[:7]


class CalibrationInputError(ValueError):
    """Safe public failure for invalid calibration input or report integrity."""


class _StrictCalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetIdentity(_StrictCalibrationModel):
    """Immutable identity for the exact dataset reviewed by humans."""

    id: str
    version: str
    sha256: str

    @field_validator("id", "version", mode="before")
    @classmethod
    def validate_identifier(cls, value: object) -> str:
        return _safe_identifier(value)

    @field_validator("sha256", mode="before")
    @classmethod
    def validate_sha256(cls, value: object) -> str:
        return _sha256_value(value)


class MetricIdentity(_StrictCalibrationModel):
    """Versioned metric identity and comparison direction."""

    name: str
    version: str
    direction: MetricDirection

    @field_validator("name", "version", mode="before")
    @classmethod
    def validate_identifier(cls, value: object) -> str:
        return _safe_identifier(value)

    @field_validator("direction", mode="before")
    @classmethod
    def validate_direction(cls, value: object) -> MetricDirection:
        if isinstance(value, MetricDirection):
            return value
        if not isinstance(value, str):
            raise ValueError("metric direction is invalid")
        try:
            return MetricDirection(value)
        except ValueError:
            raise ValueError("metric direction is invalid") from None


class HumanCalibrationRow(_StrictCalibrationModel):
    """One metric score and one human decision from an opaque reviewer."""

    item_id: str
    score: float
    human_passed: bool
    reviewer_id: str

    @field_validator("item_id", mode="before")
    @classmethod
    def validate_item_id(cls, value: object) -> str:
        return _safe_identifier(value)

    @field_validator("reviewer_id", mode="before")
    @classmethod
    def validate_reviewer_id(cls, value: object) -> str:
        if not isinstance(value, str) or _SAFE_REVIEWER_ID_RE.fullmatch(value) is None:
            raise ValueError("reviewer_id must be an opaque identifier")
        if value != value.strip():
            raise ValueError("reviewer_id must be an opaque identifier")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def validate_score(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("score must be a finite number between 0 and 1")
        normalized = float(value)
        if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
            raise ValueError("score must be a finite number between 0 and 1")
        return normalized

    @field_validator("human_passed", mode="before")
    @classmethod
    def validate_human_passed(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise ValueError("human_passed must be a boolean")
        return value


class CalibrationLabelManifest(_StrictCalibrationModel):
    """Complete, versioned set of human labels for one metric and dataset."""

    schema_version: Literal["evalforge.calibration-labels.v1"]
    dataset: DatasetIdentity
    metric: MetricIdentity
    labels: tuple[HumanCalibrationRow, ...] = Field(
        min_length=1,
        max_length=MAX_CALIBRATION_LABELS,
    )

    @field_validator("labels")
    @classmethod
    def normalize_labels(
        cls,
        value: tuple[HumanCalibrationRow, ...],
    ) -> tuple[HumanCalibrationRow, ...]:
        ordered = tuple(sorted(value, key=lambda row: row.item_id))
        identifiers = [row.item_id for row in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("calibration item_id values must be unique")
        return ordered


@dataclass(frozen=True, slots=True)
class CalibrationReportPackage:
    """Canonical portable report bytes and their integrity identifiers."""

    payload_bytes: bytes
    payload_sha256: str
    label_manifest_sha256: str
    schema_version: str = REPORT_SCHEMA_VERSION

    @property
    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_bytes)
        if not isinstance(value, dict):
            raise CalibrationInputError("calibration report package failed integrity validation")
        return value


@dataclass(frozen=True, slots=True)
class CalibrationReportReceipt:
    """Idempotent readback from a private local report sink."""

    status: Literal["created", "already_exists"]
    location: str
    payload_sha256: str
    label_manifest_sha256: str


def load_calibration_manifest(path: Path) -> CalibrationLabelManifest:
    """Load one bounded, strict UTF-8 JSON or CSV calibration manifest."""

    try:
        source = Path(path)
    except TypeError:
        raise CalibrationInputError("calibration labels must be a JSON or CSV file") from None
    if source.suffix not in {".json", ".csv"}:
        raise CalibrationInputError("calibration labels must be a JSON or CSV file")
    raw = _bounded_file_bytes(source)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CalibrationInputError("calibration labels must use valid UTF-8") from None
    if source.suffix == ".json":
        return _load_json_manifest(text)
    return _load_csv_manifest(text)


def canonical_manifest_bytes(manifest: CalibrationLabelManifest) -> bytes:
    """Return order-independent canonical bytes for one validated manifest."""

    if not isinstance(manifest, CalibrationLabelManifest):
        raise TypeError("manifest must be a CalibrationLabelManifest")
    payload = manifest.model_dump(mode="json")
    labels = payload["labels"]
    if not isinstance(labels, list):  # pragma: no cover - guaranteed by the model
        raise CalibrationInputError("calibration label manifest is invalid")
    payload["labels"] = sorted(labels, key=lambda row: row["item_id"])
    return _canonical_json_bytes(payload)


def manifest_sha256(manifest: CalibrationLabelManifest) -> str:
    """Return the SHA-256 identity of a canonical human-label manifest."""

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def build_calibration_report(
    manifest: CalibrationLabelManifest,
    *,
    selected_threshold: float,
) -> CalibrationReportPackage:
    """Create deterministic offline threshold evidence without label disclosure."""

    if not isinstance(manifest, CalibrationLabelManifest):
        raise TypeError("manifest must be a CalibrationLabelManifest")
    labels = tuple(
        CalibrationLabel(
            item_id=row.item_id,
            score=row.score,
            human_passed=row.human_passed,
        )
        for row in manifest.labels
    )
    report = evaluate_threshold(
        labels,
        metric_name=manifest.metric.name,
        metric_version=manifest.metric.version,
        selected_threshold=selected_threshold,
        direction=manifest.metric.direction,
    )
    label_hash = manifest_sha256(manifest)
    human_pass_count = sum(row.human_passed for row in manifest.labels)
    payload = {
        "calibration_set_sha256": report.calibration_set_sha256,
        "confusion_matrix": report.confusion_matrix.as_dict(),
        "dataset": manifest.dataset.model_dump(mode="json"),
        "evidence_kind": report.evidence_kind,
        "f1": report.f1,
        "human_fail_count": report.sample_size - human_pass_count,
        "human_pass_count": human_pass_count,
        "label_manifest_sha256": label_hash,
        "metric": manifest.metric.model_dump(mode="json"),
        "precision": report.precision,
        "production_validated": report.production_validated,
        "recall": report.recall,
        "reviewer_count": len({row.reviewer_id for row in manifest.labels}),
        "sample_size": report.sample_size,
        "schema_version": REPORT_SCHEMA_VERSION,
        "selected_threshold": report.selected_threshold,
    }
    payload_bytes = _canonical_json_bytes(payload)
    return CalibrationReportPackage(
        payload_bytes=payload_bytes,
        payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        label_manifest_sha256=label_hash,
    )


class LocalCalibrationReportSink:
    """Publish canonical reports privately and idempotently by payload hash."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def destination_for(self, package: CalibrationReportPackage) -> Path:
        return self.root / f"evalforge-calibration-{package.payload_sha256}.json"

    def export(self, package: CalibrationReportPackage) -> CalibrationReportReceipt:
        if not isinstance(package, CalibrationReportPackage):
            raise TypeError("package must be a CalibrationReportPackage")
        self._validate_package(package)
        self._prepare_root()
        destination = self.destination_for(package)
        if destination.is_symlink():
            raise CalibrationInputError("existing calibration report failed integrity validation")
        if destination.exists():
            return self._existing_receipt(destination, package)

        temporary = self.root / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        file_descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_descriptor = os.open(temporary, flags, 0o600)
            os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "wb", closefd=True) as stream:
                file_descriptor = None
                stream.write(package.payload_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                return self._existing_receipt(destination, package)
            self._sync_directory()
            return self._receipt(destination, package, status="created")
        except CalibrationInputError:
            raise
        except OSError:
            raise CalibrationInputError("calibration report could not be written") from None
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                raise CalibrationInputError(
                    "calibration report temporary file cleanup failed"
                ) from None

    def _validate_package(self, package: CalibrationReportPackage) -> None:
        calculated_hash = hashlib.sha256(package.payload_bytes).hexdigest()
        try:
            payload = package.payload
        except (UnicodeDecodeError, json.JSONDecodeError, CalibrationInputError):
            raise CalibrationInputError(
                "calibration report package failed integrity validation"
            ) from None
        if (
            package.schema_version != REPORT_SCHEMA_VERSION
            or package.payload_sha256 != calculated_hash
            or _HASH_RE.fullmatch(package.payload_sha256) is None
            or _HASH_RE.fullmatch(package.label_manifest_sha256) is None
            or payload.get("schema_version") != REPORT_SCHEMA_VERSION
            or payload.get("label_manifest_sha256") != package.label_manifest_sha256
            or package.payload_bytes != _canonical_json_bytes(payload)
        ):
            raise CalibrationInputError("calibration report package failed integrity validation")

    def _prepare_root(self) -> None:
        try:
            if self.root.is_symlink():
                raise CalibrationInputError("calibration report destination is invalid")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.root.is_symlink() or not self.root.is_dir():
                raise CalibrationInputError("calibration report destination is invalid")
        except CalibrationInputError:
            raise
        except OSError:
            raise CalibrationInputError("calibration report destination is invalid") from None

    def _existing_receipt(
        self,
        destination: Path,
        package: CalibrationReportPackage,
    ) -> CalibrationReportReceipt:
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(destination, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_size != len(package.payload_bytes)
            ):
                raise CalibrationInputError(
                    "existing calibration report failed integrity validation"
                )
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = None
                existing_bytes = stream.read(len(package.payload_bytes) + 1)
        except CalibrationInputError:
            raise
        except OSError:
            raise CalibrationInputError(
                "existing calibration report failed integrity validation"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if (
            existing_bytes != package.payload_bytes
            or hashlib.sha256(existing_bytes).hexdigest() != package.payload_sha256
        ):
            raise CalibrationInputError("existing calibration report failed integrity validation")
        return self._receipt(destination, package, status="already_exists")

    def _receipt(
        self,
        destination: Path,
        package: CalibrationReportPackage,
        *,
        status: Literal["created", "already_exists"],
    ) -> CalibrationReportReceipt:
        return CalibrationReportReceipt(
            status=status,
            location=str(destination.absolute()),
            payload_sha256=package.payload_sha256,
            label_manifest_sha256=package.label_manifest_sha256,
        )

    def _sync_directory(self) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.root, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _bounded_file_bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CALIBRATION_FILE_BYTES + 1)
    except OSError:
        raise CalibrationInputError("calibration label file could not be read") from None
    if len(raw) > MAX_CALIBRATION_FILE_BYTES:
        raise CalibrationInputError("calibration label files may not exceed 2 MiB")
    return raw


def _load_json_manifest(text: str) -> CalibrationLabelManifest:
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError):
        raise CalibrationInputError("calibration label JSON is invalid") from None
    return _validated_manifest(payload)


def _load_csv_manifest(text: str) -> CalibrationLabelManifest:
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fieldnames = reader.fieldnames
        if (
            fieldnames is None
            or len(fieldnames) != len(_CSV_COLUMNS)
            or set(fieldnames) != set(_CSV_COLUMNS)
        ):
            raise CalibrationInputError("calibration label CSV columns are invalid")
        metadata: tuple[str, ...] | None = None
        labels: list[dict[str, object]] = []
        for row in reader:
            if None in row or any(row.get(column) is None for column in _CSV_COLUMNS):
                raise CalibrationInputError("calibration label CSV rows are invalid")
            values = {column: row[column] for column in _CSV_COLUMNS}
            if any(value != value.strip() for value in values.values()):
                raise CalibrationInputError("calibration label CSV rows are invalid")
            row_metadata = tuple(values[column] for column in _CSV_METADATA_COLUMNS)
            if metadata is None:
                metadata = row_metadata
            elif row_metadata != metadata:
                raise CalibrationInputError("calibration label CSV metadata must be uniform")
            decision = values["human_passed"]
            if decision not in {"true", "false"}:
                raise CalibrationInputError(
                    "calibration label CSV decisions must be lowercase true or false"
                )
            try:
                score = float(values["score"])
            except ValueError:
                raise CalibrationInputError("calibration label CSV scores are invalid") from None
            labels.append(
                {
                    "item_id": values["item_id"],
                    "score": score,
                    "human_passed": decision == "true",
                    "reviewer_id": values["reviewer_id"],
                }
            )
            if len(labels) > MAX_CALIBRATION_LABELS:
                raise CalibrationInputError(
                    "calibration manifests may contain at most 10000 labels"
                )
    except csv.Error:
        raise CalibrationInputError("calibration label CSV is invalid") from None
    if metadata is None:
        raise CalibrationInputError("calibration label CSV must contain labels")
    payload = {
        "schema_version": metadata[0],
        "dataset": {
            "id": metadata[1],
            "version": metadata[2],
            "sha256": metadata[3],
        },
        "metric": {
            "name": metadata[4],
            "version": metadata[5],
            "direction": metadata[6],
        },
        "labels": labels,
    }
    return _validated_manifest(payload)


def _validated_manifest(payload: object) -> CalibrationLabelManifest:
    try:
        return CalibrationLabelManifest.model_validate(payload)
    except ValidationError:
        raise CalibrationInputError("calibration label manifest is invalid") from None


def _safe_identifier(value: object) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError("value must be a safe identifier")
    if value != value.strip():
        raise ValueError("value must be a safe identifier")
    return value


def _sha256_value(value: object) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError("sha256 must be a lowercase hexadecimal digest")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise CalibrationInputError("calibration evidence is not finite JSON data") from None
