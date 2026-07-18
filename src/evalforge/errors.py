"""Stable application error taxonomy."""

from __future__ import annotations

from typing import Any


class EvalForgeError(Exception):
    """An expected, safe-to-render application failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or []


class NotFoundError(EvalForgeError):
    """A requested resource does not exist or is soft-deleted."""

    def __init__(self, resource: str) -> None:
        super().__init__("not_found", f"{resource} was not found.", status_code=404)


class ConflictError(EvalForgeError):
    """A state or idempotency conflict prevents the requested mutation."""

    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, status_code=409)


class CapabilityError(EvalForgeError):
    """A disabled or unconfigured capability was requested."""

    def __init__(self, message: str) -> None:
        super().__init__("capability_unavailable", message, status_code=403)


class LimitError(EvalForgeError):
    """A bounded execution or upload limit was exceeded."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__("limit_exceeded", message, status_code=status_code)
