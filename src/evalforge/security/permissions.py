"""Immutable workspace context and denial-first role checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from evalforge.errors import EvalForgeError

LOCAL_WORKSPACE_ID: Final = "00000000-0000-4000-8000-000000000001"
LOCAL_USER_ID: Final = "00000000-0000-4000-8000-000000000002"
LOCAL_MEMBERSHIP_ID: Final = "00000000-0000-4000-8000-000000000003"
LOCAL_WORKSPACE_SLUG: Final = "local"
LOCAL_ISSUER: Final = "urn:evalforge:local"
LOCAL_SUBJECT: Final = "local-owner"


class WorkspaceRole(StrEnum):
    """Ordered workspace privileges from read-only through governance."""

    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"
    OWNER = "owner"


_ROLE_RANK: Final[dict[WorkspaceRole, int]] = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.EDITOR: 1,
    WorkspaceRole.ADMIN: 2,
    WorkspaceRole.OWNER: 3,
}


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """One authenticated user's active membership in one workspace."""

    workspace_id: str
    user_id: str
    role: WorkspaceRole
    workspace_name: str
    display_name: str
    active: bool = True
    membership_active: bool = True

    @property
    def is_active(self) -> bool:
        """Return the combined workspace and membership activation state."""

        return self.active and self.membership_active


class AuthorizationError(EvalForgeError):
    """A safe denial that reveals no object or membership details."""

    def __init__(self) -> None:
        super().__init__(
            "forbidden",
            "You do not have permission to perform this action.",
            status_code=403,
        )


def role_allows(actual: WorkspaceRole, required: WorkspaceRole) -> bool:
    """Return whether an active membership role meets a minimum role."""

    return _ROLE_RANK[WorkspaceRole(actual)] >= _ROLE_RANK[WorkspaceRole(required)]


def require_role(context: WorkspaceContext, required: WorkspaceRole) -> WorkspaceContext:
    """Return an authorized context or fail with one non-enumerating error."""

    if not context.is_active or not role_allows(context.role, required):
        raise AuthorizationError
    return context


def local_workspace_context() -> WorkspaceContext:
    """Return the stable owner context used by the offline local mode."""

    return WorkspaceContext(
        workspace_id=LOCAL_WORKSPACE_ID,
        user_id=LOCAL_USER_ID,
        role=WorkspaceRole.OWNER,
        workspace_name="Local workspace",
        display_name="Local owner",
    )
