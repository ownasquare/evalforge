"""FastAPI dependencies with request-scoped SQLAlchemy sessions."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from evalforge.commercial import require_run_entitlement
from evalforge.container import AppContainer
from evalforge.evaluation.service import EvaluationService
from evalforge.models import RecordStatus, User, Workspace, WorkspaceMembership
from evalforge.security.auth import AuthenticatedPrincipal
from evalforge.security.permissions import (
    AuthorizationError,
    WorkspaceContext,
    WorkspaceRole,
    require_role,
)


def get_container(request: Request) -> AppContainer:
    return cast("AppContainer", request.app.state.container)


def get_session(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Generator[Session, None, None]:
    session = container.session_factory()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_evaluation_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> EvaluationService:
    return container.evaluation_service


def get_principal(
    container: Annotated[AppContainer, Depends(get_container)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthenticatedPrincipal:
    return container.authenticator.authenticate(authorization)


def resolve_principal_user(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> AuthenticatedPrincipal:
    statement = select(User).where(User.status == RecordStatus.ACTIVE)
    if principal.user_id is not None:
        statement = statement.where(User.id == principal.user_id)
    else:
        statement = statement.where(
            User.issuer == principal.issuer,
            User.subject == principal.subject,
        )
    user = session.scalar(statement)
    if user is None:
        raise AuthorizationError
    return replace(
        principal,
        user_id=user.id,
        display_name=user.display_name or principal.display_name,
        email=user.email or principal.email,
    )


def available_workspace_contexts(
    session: Session,
    principal: AuthenticatedPrincipal,
) -> list[WorkspaceContext]:
    if principal.user_id is None:
        raise AuthorizationError
    rows = session.execute(
        select(WorkspaceMembership, Workspace, User)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.user_id == principal.user_id,
            WorkspaceMembership.status == RecordStatus.ACTIVE,
            Workspace.status == RecordStatus.ACTIVE,
            User.status == RecordStatus.ACTIVE,
        )
        .order_by(Workspace.name, Workspace.id)
    ).all()
    return [
        WorkspaceContext(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole(membership.role),
            workspace_name=workspace.name,
            display_name=user.display_name or principal.display_name or "Workspace member",
        )
        for membership, workspace, user in rows
    ]


def get_workspace_context(
    principal: Annotated[AuthenticatedPrincipal, Depends(resolve_principal_user)],
    session: Annotated[Session, Depends(get_session)],
    workspace_id: Annotated[
        str | None, Header(alias="X-EvalForge-Workspace-ID", min_length=36, max_length=36)
    ] = None,
) -> WorkspaceContext:
    contexts = available_workspace_contexts(session, principal)
    if workspace_id is None:
        if len(contexts) == 1 and principal.is_local:
            return contexts[0]
        raise AuthorizationError
    selected = next((item for item in contexts if item.workspace_id == workspace_id), None)
    if selected is None:
        raise AuthorizationError
    return selected


def require_viewer(
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
) -> WorkspaceContext:
    return require_role(context, WorkspaceRole.VIEWER)


def require_editor(
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
) -> WorkspaceContext:
    return require_role(context, WorkspaceRole.EDITOR)


def require_run_entitled_editor(
    context: Annotated[WorkspaceContext, Depends(require_editor)],
    session: Annotated[Session, Depends(get_session)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> WorkspaceContext:
    """Authorize an editor and enforce access only for hosted pilot run starts."""

    return require_run_entitlement(session, context, container.settings)


def require_admin(
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
) -> WorkspaceContext:
    return require_role(context, WorkspaceRole.ADMIN)


def require_owner(
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
) -> WorkspaceContext:
    return require_role(context, WorkspaceRole.OWNER)


ContainerDep = Annotated[AppContainer, Depends(get_container)]
SessionDep = Annotated[Session, Depends(get_session)]
EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
PrincipalDep = Annotated[AuthenticatedPrincipal, Depends(resolve_principal_user)]
WorkspaceDep = Annotated[WorkspaceContext, Depends(get_workspace_context)]
ViewerWorkspaceDep = Annotated[WorkspaceContext, Depends(require_viewer)]
EditorWorkspaceDep = Annotated[WorkspaceContext, Depends(require_editor)]
RunEntitledEditorWorkspaceDep = Annotated[
    WorkspaceContext,
    Depends(require_run_entitled_editor),
]
AdminWorkspaceDep = Annotated[WorkspaceContext, Depends(require_admin)]
OwnerWorkspaceDep = Annotated[WorkspaceContext, Depends(require_owner)]
