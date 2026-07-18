"""Minimal idempotent export-sink example for tests and learning."""

from __future__ import annotations

from datetime import UTC, datetime

from evalforge.exports import ExportPackage, ExportReceipt


class InMemoryExportSink:
    """Store packages in memory; use a durable system for production exports."""

    name = "example_memory"

    def __init__(self) -> None:
        self.packages: dict[str, bytes] = {}
        self.receipts: dict[str, ExportReceipt] = {}

    def export(self, package: ExportPackage) -> ExportReceipt:
        if not isinstance(package, ExportPackage):
            raise TypeError("package must be an ExportPackage")
        existing = self.receipts.get(package.payload_sha256)
        if existing is not None:
            return ExportReceipt(
                sink=existing.sink,
                package_sha256=existing.package_sha256,
                location=existing.location,
                exported_at=existing.exported_at,
                created=False,
            )
        self.packages.setdefault(package.payload_sha256, package.envelope_bytes)
        receipt = ExportReceipt(
            sink=self.name,
            package_sha256=package.payload_sha256,
            location=f"memory://{package.payload_sha256}",
            exported_at=datetime.now(UTC),
            created=True,
        )
        self.receipts[package.payload_sha256] = receipt
        return receipt
