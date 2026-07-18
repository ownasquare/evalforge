"""Append-only, content-minimized workspace audit events."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from evalforge.models import AuditEvent
from evalforge.security.permissions import WorkspaceContext

_FORBIDDEN_METADATA_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "prompt",
    "context",
    "output",
    "email",
    "subject",
)


class AuditRecorder:
    """Record safe mutation facts in the caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        context: WorkspaceContext,
        *,
        action: str,
        resource_type: str,
        outcome: str,
        resource_id: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        safe_metadata = dict(metadata or {})
        for key in safe_metadata:
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if any(fragment in normalized for fragment in _FORBIDDEN_METADATA_FRAGMENTS):
                raise ValueError("audit metadata contains a disallowed content or identity field")
        event = AuditEvent(
            workspace_id=context.workspace_id,
            actor_user_id=context.user_id,
            action=action[:100],
            resource_type=resource_type[:100],
            resource_id=resource_id[:100] if resource_id else None,
            outcome=outcome[:30],
            request_id=request_id[:255] if request_id else None,
            metadata_json=safe_metadata,
        )
        self.session.add(event)
        self.session.flush()
        return event
