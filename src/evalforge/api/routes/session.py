"""Safe signed-in identity and workspace discovery routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from evalforge.api.dependencies import (
    ContainerDep,
    PrincipalDep,
    SessionDep,
    available_workspace_contexts,
)
from evalforge.schemas import SessionRead, WorkspaceAccessRead
from evalforge.security.permissions import AuthorizationError

router = APIRouter(tags=["session"])


def _workspace_models(session: SessionDep, principal: PrincipalDep) -> list[WorkspaceAccessRead]:
    return [
        WorkspaceAccessRead(
            id=context.workspace_id,
            name=context.workspace_name,
            role=context.role.value,
        )
        for context in available_workspace_contexts(session, principal)
    ]


@router.get("/session", response_model=SessionRead)
def get_session_identity(
    session: SessionDep,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, object]:
    if principal.user_id is None:
        raise AuthorizationError
    return SessionRead(
        user_id=principal.user_id,
        display_name=principal.display_name or "Workspace member",
        email=principal.email,
        auth_mode=container.settings.auth_mode,
        workspaces=_workspace_models(session, principal),
    ).model_dump(mode="json")


@router.get("/workspaces", response_model=list[WorkspaceAccessRead])
def list_workspaces(session: SessionDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    return [model.model_dump(mode="json") for model in _workspace_models(session, principal)]
