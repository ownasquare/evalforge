from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evalforge.exports import (
    DisclosureProfile,
    ExportIntegrityError,
    ExportSink,
    LocalFileSink,
    build_export_package,
)


def _package(*, minute: int = 0):
    return build_export_package(
        {"id": "run-1", "output_text": "sensitive evidence"},
        application_version="0.1.0",
        metric_versions={"correctness": "v1"},
        disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
        generated_at=datetime(2026, 7, 18, 12, minute, tzinfo=UTC),
    )


def test_local_file_sink_is_protocol_compatible_private_and_idempotent(tmp_path: Path) -> None:
    sink = LocalFileSink(tmp_path / "exports")
    package = _package()

    assert isinstance(sink, ExportSink)
    first = sink.export(package)
    second = sink.export(package)

    destination = Path(first.location)
    assert first.created is True
    assert second.created is False
    assert first.idempotency_key == second.idempotency_key
    assert first.package_sha256 == package.payload_sha256
    assert destination.read_bytes() == package.envelope_bytes
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_same_payload_with_new_generation_time_reuses_first_receipt(tmp_path: Path) -> None:
    sink = LocalFileSink(tmp_path / "exports")
    first = sink.export(_package(minute=0))
    second = sink.export(_package(minute=5))

    assert first.location == second.location
    assert first.exported_at == second.exported_at
    assert second.created is False
    assert len(list((tmp_path / "exports").glob("*.json"))) == 1


def test_sink_refuses_to_trust_a_corrupted_existing_package(tmp_path: Path) -> None:
    sink = LocalFileSink(tmp_path / "exports")
    receipt = sink.export(_package())
    destination = Path(receipt.location)
    envelope = json.loads(destination.read_text(encoding="utf-8"))
    envelope["payload"]["run"]["id"] = "tampered"
    destination.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ExportIntegrityError, match="integrity"):
        sink.export(_package())


def test_sink_rejects_existing_symbolic_link(tmp_path: Path) -> None:
    sink = LocalFileSink(tmp_path / "exports")
    package = _package()
    sink.root.mkdir(mode=0o700)
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("{}", encoding="utf-8")
    destination = sink.destination_for(package)
    destination.symlink_to(unrelated)

    with pytest.raises(ExportIntegrityError, match="symbolic link"):
        sink.export(package)
