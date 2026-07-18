from __future__ import annotations

import pytest

from evalforge.security.permissions import (
    LOCAL_ISSUER,
    LOCAL_MEMBERSHIP_ID,
    LOCAL_SUBJECT,
    LOCAL_USER_ID,
    LOCAL_WORKSPACE_ID,
    LOCAL_WORKSPACE_SLUG,
    AuthorizationError,
    WorkspaceContext,
    WorkspaceRole,
    local_workspace_context,
    require_role,
    role_allows,
)


def test_local_identity_constants_are_stable() -> None:
    assert LOCAL_WORKSPACE_ID == "00000000-0000-4000-8000-000000000001"
    assert LOCAL_USER_ID == "00000000-0000-4000-8000-000000000002"
    assert LOCAL_MEMBERSHIP_ID == "00000000-0000-4000-8000-000000000003"
    assert LOCAL_WORKSPACE_SLUG == "local"
    assert LOCAL_ISSUER == "urn:evalforge:local"
    assert LOCAL_SUBJECT == "local-owner"


def test_local_workspace_context_is_an_active_owner() -> None:
    context = local_workspace_context()

    assert context == WorkspaceContext(
        LOCAL_WORKSPACE_ID,
        LOCAL_USER_ID,
        WorkspaceRole.OWNER,
        "Local workspace",
        "Local owner",
    )
    assert context.active is True
    assert context.membership_active is True
    assert context.is_active is True


@pytest.mark.parametrize(
    ("actual", "required", "allowed"),
    [
        (WorkspaceRole.VIEWER, WorkspaceRole.VIEWER, True),
        (WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, False),
        (WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, True),
        (WorkspaceRole.EDITOR, WorkspaceRole.ADMIN, False),
        (WorkspaceRole.ADMIN, WorkspaceRole.EDITOR, True),
        (WorkspaceRole.ADMIN, WorkspaceRole.OWNER, False),
        (WorkspaceRole.OWNER, WorkspaceRole.ADMIN, True),
        (WorkspaceRole.OWNER, WorkspaceRole.OWNER, True),
    ],
)
def test_role_order_is_explicit(
    actual: WorkspaceRole,
    required: WorkspaceRole,
    allowed: bool,
) -> None:
    assert role_allows(actual, required) is allowed


def test_role_guard_returns_the_same_active_context() -> None:
    context = WorkspaceContext(
        "workspace-1",
        "user-1",
        WorkspaceRole.ADMIN,
        "Quality team",
        "Ada",
    )

    assert require_role(context, WorkspaceRole.EDITOR) is context


@pytest.mark.parametrize(
    "context",
    [
        WorkspaceContext(
            "workspace-1",
            "user-1",
            WorkspaceRole.OWNER,
            "Quality team",
            "Ada",
            active=False,
        ),
        WorkspaceContext(
            "workspace-1",
            "user-1",
            WorkspaceRole.OWNER,
            "Quality team",
            "Ada",
            membership_active=False,
        ),
        WorkspaceContext(
            "workspace-1",
            "user-1",
            WorkspaceRole.VIEWER,
            "Quality team",
            "Ada",
        ),
    ],
)
def test_role_guard_denies_inactive_or_insufficient_access(context: WorkspaceContext) -> None:
    with pytest.raises(AuthorizationError) as captured:
        require_role(context, WorkspaceRole.EDITOR)

    assert captured.value.status_code == 403
    assert captured.value.code == "forbidden"
    assert str(captured.value) == "You do not have permission to perform this action."
