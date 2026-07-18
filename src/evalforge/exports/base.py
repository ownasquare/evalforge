"""Stable contracts shared by local and optional external export sinks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from evalforge.exports.package import ExportPackage


class DisclosureProfile(StrEnum):
    FULL_EVIDENCE = "full_evidence"
    CONTENT_REDACTED = "content_redacted"


class ExportError(RuntimeError):
    """Base error for local export packaging and persistence."""


class ExportIntegrityError(ExportError):
    """Raised when existing export bytes fail their declared hash contract."""


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    """Immutable idempotency evidence returned by an export sink."""

    sink: str
    package_sha256: str
    location: str
    exported_at: datetime
    created: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "sink", self.sink.strip())
        object.__setattr__(self, "location", self.location.strip())
        if not self.sink:
            raise ValueError("sink cannot be blank")
        if not self.location:
            raise ValueError("location cannot be blank")
        if len(self.package_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.package_sha256
        ):
            raise ValueError("package_sha256 must be a lowercase SHA-256 digest")
        if self.exported_at.tzinfo is None or self.exported_at.utcoffset() is None:
            raise ValueError("exported_at must be timezone-aware")
        if not isinstance(self.created, bool):
            raise ValueError("created must be a boolean")
        object.__setattr__(self, "exported_at", self.exported_at.astimezone(UTC))

    @property
    def idempotency_key(self) -> str:
        return f"{self.sink}:{self.package_sha256}"


@runtime_checkable
class ExportSink(Protocol):
    @property
    def name(self) -> str: ...

    def export(self, package: ExportPackage) -> ExportReceipt: ...
