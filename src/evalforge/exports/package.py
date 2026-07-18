"""Canonical run-evidence packages and a private, idempotent local sink."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from evalforge.exports.base import (
    DisclosureProfile,
    ExportIntegrityError,
    ExportReceipt,
)

SCHEMA_VERSION: Final[str] = "evalforge.run-export.v1"
_REDACTED: Final[str] = "[redacted]"
_HASH_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CODE_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9._+-]{0,127}\Z")
_SAFE_METRIC_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_RUN_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "queued",
        "running",
        "cancel_requested",
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
        "interrupted",
    }
)
_RESULT_STATUSES: Final[frozenset[str]] = frozenset(
    {"queued", "running", "completed", "error", "cancelled", "interrupted"}
)
_METRIC_STATUSES: Final[frozenset[str]] = frozenset({"applicable", "not_applicable", "error"})
_METRIC_DIRECTIONS: Final[frozenset[str]] = frozenset({"higher_is_better", "lower_is_better"})
_COST_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "billing_ambiguous",
        "not_incurred",
        "pricing_unavailable",
        "reported_usage",
        "synthetic",
        "usage_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class ExportPackage:
    schema_version: str
    disclosure_profile: DisclosureProfile
    application_version: str
    generated_at: datetime
    payload_bytes: bytes
    payload_sha256: str

    @property
    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_bytes)
        if not isinstance(value, dict):
            raise ExportIntegrityError("export payload integrity validation failed")
        return value

    @property
    def envelope(self) -> dict[str, Any]:
        return {
            "generated_at": _format_timestamp(self.generated_at),
            "payload": self.payload,
            "payload_sha256": self.payload_sha256,
        }

    @property
    def envelope_bytes(self) -> bytes:
        return _canonical_json_bytes(self.envelope)


def disclose_run_evidence(
    run_evidence: Mapping[str, object],
    disclosure_profile: DisclosureProfile,
) -> dict[str, Any]:
    """Return detached run evidence filtered by the selected disclosure contract."""

    resolved_profile = DisclosureProfile(disclosure_profile)
    safe_evidence = _json_value(run_evidence)
    if not isinstance(safe_evidence, dict):
        raise ValueError("run_evidence must be a mapping")
    if resolved_profile is DisclosureProfile.CONTENT_REDACTED:
        return _content_redacted_run(safe_evidence)
    return safe_evidence


def build_export_package(
    run_evidence: Mapping[str, object],
    *,
    application_version: str,
    metric_versions: Mapping[str, str],
    disclosure_profile: DisclosureProfile,
    generated_at: datetime | None = None,
) -> ExportPackage:
    """Create an immutable, versioned package without transmitting any data."""

    normalized_application_version = application_version.strip()
    if not normalized_application_version:
        raise ValueError("application_version cannot be blank")
    resolved_profile = DisclosureProfile(disclosure_profile)
    normalized_versions: dict[str, str] = {}
    for name, version in metric_versions.items():
        normalized_name = name.strip()
        normalized_version = version.strip()
        if not normalized_name or not normalized_version:
            raise ValueError("metric version names and values cannot be blank")
        normalized_versions[normalized_name] = normalized_version

    timestamp = generated_at or datetime.now(UTC)
    if not isinstance(timestamp, datetime):
        raise ValueError("generated_at must be a datetime")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    normalized_timestamp = timestamp.astimezone(UTC)

    safe_evidence = disclose_run_evidence(run_evidence, resolved_profile)
    if resolved_profile is DisclosureProfile.CONTENT_REDACTED:
        normalized_versions = _safe_metric_versions(normalized_versions)
    payload = {
        "application_version": normalized_application_version,
        "disclosure_profile": resolved_profile.value,
        "metric_versions": normalized_versions,
        "run": safe_evidence,
        "schema_version": SCHEMA_VERSION,
    }
    payload_bytes = _canonical_json_bytes(payload)
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    return ExportPackage(
        schema_version=SCHEMA_VERSION,
        disclosure_profile=resolved_profile,
        application_version=normalized_application_version,
        generated_at=normalized_timestamp,
        payload_bytes=payload_bytes,
        payload_sha256=payload_hash,
    )


class LocalFileSink:
    """Write one canonical package per payload hash with private file permissions."""

    name: Final[str] = "local_file"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def destination_for(self, package: ExportPackage) -> Path:
        return self.root / f"evalforge-{package.payload_sha256}.json"

    def export(self, package: ExportPackage) -> ExportReceipt:
        if not isinstance(package, ExportPackage):
            raise TypeError("package must be an ExportPackage")
        self._prepare_root()
        destination = self.destination_for(package)
        if destination.is_symlink():
            raise ExportIntegrityError("existing export destination is a symbolic link")
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
                stream.write(package.envelope_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                return self._existing_receipt(destination, package)
            self._sync_directory()
            return self._receipt(destination, package, package.generated_at, created=True)
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            with suppress(FileNotFoundError):
                temporary.unlink()

    def _prepare_root(self) -> None:
        if self.root.is_symlink():
            raise ExportIntegrityError("local export root cannot be a symbolic link")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ExportIntegrityError("local export root is not a directory")

    def _existing_receipt(self, destination: Path, package: ExportPackage) -> ExportReceipt:
        if destination.is_symlink():
            raise ExportIntegrityError("existing export destination is a symbolic link")
        try:
            raw = destination.read_bytes()
            envelope = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportIntegrityError("existing export failed integrity validation") from exc
        if not isinstance(envelope, dict) or set(envelope) != {
            "generated_at",
            "payload",
            "payload_sha256",
        }:
            raise ExportIntegrityError("existing export failed integrity validation")
        try:
            existing_payload_bytes = _canonical_json_bytes(envelope["payload"])
        except ValueError as exc:
            raise ExportIntegrityError("existing export failed integrity validation") from exc
        existing_hash = hashlib.sha256(existing_payload_bytes).hexdigest()
        if (
            envelope.get("payload_sha256") != existing_hash
            or existing_hash != package.payload_sha256
            or existing_payload_bytes != package.payload_bytes
        ):
            raise ExportIntegrityError("existing export failed integrity validation")
        exported_at = _parse_timestamp(envelope.get("generated_at"))
        return self._receipt(destination, package, exported_at, created=False)

    def _receipt(
        self,
        destination: Path,
        package: ExportPackage,
        exported_at: datetime,
        *,
        created: bool,
    ) -> ExportReceipt:
        return ExportReceipt(
            sink=self.name,
            package_sha256=package.payload_sha256,
            location=str(destination.absolute()),
            exported_at=exported_at,
            created=created,
        )

    def _sync_directory(self) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(self.root, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _canonical_json_bytes(value: object) -> bytes:
    normalized = _json_value(value)
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("export evidence must be finite JSON data") from exc


def _json_value(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("export evidence must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("export evidence object keys must be strings")
            normalized[key] = _json_value(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise ValueError("export evidence must contain only JSON-compatible values")


def _content_redacted_run(source: Mapping[str, Any]) -> dict[str, Any]:
    """Build the redacted profile from a strict schema allowlist.

    Content-bearing values are never copied. Unknown fields are discarded so a
    newly-added snapshot or provider field cannot silently bypass redaction.
    """

    result = _record_provenance(
        source,
        uuid_fields=("id", "workspace_id", "dataset_id"),
        hash_fields=("dataset_hash", "request_hash"),
        integer_fields=(
            "state_version",
            "total_items",
            "completed_items",
            "succeeded_items",
            "failed_items",
        ),
        boolean_fields=("acknowledge_real_cost", "acknowledge_unknown_cost"),
        timestamp_fields=(
            "queued_at",
            "started_at",
            "heartbeat_at",
            "cancel_requested_at",
            "finished_at",
            "created_at",
            "updated_at",
        ),
    )
    _copy_safe_code(source, result, "application_version")
    _copy_safe_code(source, result, "executor_type")
    _copy_enum(source, result, "status", _RUN_STATUSES)

    dataset_snapshot = source.get("dataset_snapshot")
    if isinstance(dataset_snapshot, Mapping):
        result["dataset_snapshot"] = _redacted_dataset_snapshot(dataset_snapshot)

    preflight_snapshot = source.get("preflight_snapshot")
    if isinstance(preflight_snapshot, Mapping):
        result["preflight_snapshot"] = _redacted_preflight(preflight_snapshot)

    candidates = source.get("candidates")
    if isinstance(candidates, list):
        result["candidates"] = [
            _redacted_candidate(item) for item in candidates if isinstance(item, Mapping)
        ]

    evaluation_results = source.get("results")
    if isinstance(evaluation_results, list):
        result["results"] = [
            _redacted_result(item) for item in evaluation_results if isinstance(item, Mapping)
        ]
    return result


def _redacted_dataset_snapshot(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _record_provenance(
        source,
        uuid_fields=("id",),
        hash_fields=("content_hash",),
    )
    _copy_safe_code(source, result, "version")
    cases = source.get("cases")
    if isinstance(cases, list):
        result["cases"] = [
            _redacted_case_snapshot(item) for item in cases if isinstance(item, Mapping)
        ]
    result["content_redacted"] = True
    return result


def _redacted_case_snapshot(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _record_provenance(
        source,
        uuid_fields=("id",),
        hash_fields=("case_hash",),
        integer_fields=("position",),
    )
    for field in (
        "input",
        "context",
        "context_chunks",
        "expected_output",
        "required_phrases",
        "constraints",
        "tags",
        "metadata",
        "reference",
    ):
        if field in source:
            result[field] = _REDACTED
    return result


def _redacted_preflight(source: Mapping[str, Any]) -> dict[str, Any]:
    return _record_provenance(
        source,
        integer_fields=(
            "case_count",
            "prompt_count",
            "model_count",
            "variant_count",
            "provider_call_count",
            "max_requested_output_tokens",
            "estimated_input_tokens",
            "estimated_known_cost_micro_usd",
            "spend_limit_micro_usd",
        ),
        boolean_fields=(
            "cost_estimate_complete",
            "real_provider",
            "external_data_transfer_acknowledged",
        ),
    )


def _redacted_candidate(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _record_provenance(
        source,
        uuid_fields=(
            "id",
            "workspace_id",
            "run_id",
            "prompt_template_id",
            "model_profile_id",
        ),
        hash_fields=("prompt_hash", "model_hash", "candidate_hash"),
        integer_fields=(
            "ordinal",
            "state_version",
            "total_items",
            "completed_items",
            "failed_items",
        ),
        timestamp_fields=(
            "started_at",
            "heartbeat_at",
            "finished_at",
            "created_at",
            "updated_at",
        ),
    )
    _copy_enum(source, result, "status", _RUN_STATUSES)
    return result


def _redacted_result(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _record_provenance(
        source,
        uuid_fields=("id", "workspace_id", "run_id", "run_candidate_id", "test_case_id"),
        hash_fields=("case_hash", "prompt_hash", "model_hash"),
        integer_fields=(
            "state_version",
            "retry_count",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_cost_micro_usd",
        ),
        boolean_fields=("aggregate_passed", "error_retryable"),
        number_fields=("aggregate_score", "effective_metric_weight"),
        timestamp_fields=(
            "queued_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ),
    )
    _copy_enum(source, result, "status", _RESULT_STATUSES)
    _copy_enum(source, result, "cost_source", _COST_SOURCES)

    input_snapshot = source.get("input_snapshot")
    if isinstance(input_snapshot, Mapping):
        result["input_snapshot"] = _redacted_case_snapshot(input_snapshot)
    for field in ("rendered_system_prompt", "rendered_user_prompt", "output_text"):
        if field in source:
            result[field] = _REDACTED

    metric_versions = source.get("metric_versions")
    if isinstance(metric_versions, Mapping):
        result["metric_versions"] = _safe_metric_versions(metric_versions)
    metric_directions = source.get("metric_directions")
    if isinstance(metric_directions, Mapping):
        result["metric_directions"] = _safe_metric_enums(metric_directions, _METRIC_DIRECTIONS)
    metric_applicability = source.get("metric_applicability")
    if isinstance(metric_applicability, Mapping):
        result["metric_applicability"] = _safe_metric_enums(metric_applicability, _METRIC_STATUSES)
    metric_results = source.get("metric_results")
    if isinstance(metric_results, Mapping):
        result["metric_results"] = _redacted_metric_results(metric_results)
    return result


def _redacted_metric_results(source: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, raw_metric in source.items():
        if not _safe_metric_name(name) or not isinstance(raw_metric, Mapping):
            continue
        metric = _record_provenance(
            raw_metric,
            boolean_fields=("passed",),
            number_fields=("score", "threshold"),
        )
        _copy_safe_code(raw_metric, metric, "version")
        _copy_enum(raw_metric, metric, "status", _METRIC_STATUSES)
        _copy_enum(raw_metric, metric, "direction", _METRIC_DIRECTIONS)
        if "evidence" in raw_metric:
            metric["evidence"] = _REDACTED
        result[name] = metric
    return result


def _record_provenance(
    source: Mapping[str, Any],
    *,
    uuid_fields: tuple[str, ...] = (),
    hash_fields: tuple[str, ...] = (),
    integer_fields: tuple[str, ...] = (),
    boolean_fields: tuple[str, ...] = (),
    number_fields: tuple[str, ...] = (),
    timestamp_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in uuid_fields:
        value = _safe_uuid(source.get(field))
        if value is not None:
            result[field] = value
    for field in hash_fields:
        value = source.get(field)
        if isinstance(value, str) and _HASH_RE.fullmatch(value) is not None:
            result[field] = value
    for field in integer_fields:
        value = source.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[field] = value
    for field in boolean_fields:
        value = source.get(field)
        if isinstance(value, bool):
            result[field] = value
    for field in number_fields:
        value = source.get(field)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            result[field] = value
    for field in timestamp_fields:
        value = _safe_timestamp(source.get(field))
        if value is not None:
            result[field] = value
    return result


def _copy_safe_code(source: Mapping[str, Any], result: dict[str, Any], field: str) -> None:
    value = source.get(field)
    if isinstance(value, str) and _SAFE_CODE_RE.fullmatch(value) is not None:
        result[field] = value


def _copy_enum(
    source: Mapping[str, Any],
    result: dict[str, Any],
    field: str,
    allowed: frozenset[str],
) -> None:
    value = source.get(field)
    if isinstance(value, str) and value in allowed:
        result[field] = value


def _safe_metric_versions(source: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, version in source.items():
        if (
            _safe_metric_name(name)
            and isinstance(version, str)
            and _SAFE_CODE_RE.fullmatch(version) is not None
        ):
            result[name] = version
    return result


def _safe_metric_enums(source: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, str]:
    return {
        name: value
        for name, value in source.items()
        if _safe_metric_name(name) and isinstance(value, str) and value in allowed
    }


def _safe_metric_name(value: object) -> bool:
    return isinstance(value, str) and _SAFE_METRIC_NAME_RE.fullmatch(value) is not None


def _safe_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return str(parsed)


def _safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return _format_timestamp(parsed)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ExportIntegrityError("existing export failed timestamp integrity validation")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportIntegrityError("existing export failed timestamp integrity validation") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExportIntegrityError("existing export failed timestamp integrity validation")
    return parsed.astimezone(UTC)
