"""Prompt-library CRUD routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response, status

from evalforge.api.dependencies import EditorWorkspaceDep, SessionDep, ViewerWorkspaceDep
from evalforge.audit import AuditRecorder
from evalforge.observability import current_request_id
from evalforge.repositories import PromptTemplateRepository
from evalforge.schemas import (
    Page,
    PromptTemplateCreate,
    PromptTemplateRead,
    PromptTemplateUpdate,
)

router = APIRouter(tags=["prompts"])


@router.get("/prompts", response_model=Page[PromptTemplateRead])
def list_prompts(
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows, total = PromptTemplateRepository(session, workspace).list(page=page, limit=limit)
    return {
        "items": [PromptTemplateRead.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/prompts", status_code=status.HTTP_201_CREATED, response_model=PromptTemplateRead)
def create_prompt(
    data: PromptTemplateCreate, session: SessionDep, workspace: EditorWorkspaceDep
) -> dict[str, Any]:
    prompt = PromptTemplateRepository(session, workspace).create(data)
    AuditRecorder(session).record(
        workspace,
        action="prompt.create",
        resource_type="prompt_template",
        resource_id=prompt.id,
        outcome="success",
        request_id=current_request_id(),
    )
    session.commit()
    return PromptTemplateRead.model_validate(prompt).model_dump(mode="json")


@router.get("/prompts/{prompt_id}", response_model=PromptTemplateRead)
def get_prompt(
    prompt_id: str, session: SessionDep, workspace: ViewerWorkspaceDep
) -> dict[str, Any]:
    prompt = PromptTemplateRepository(session, workspace).get(prompt_id)
    return PromptTemplateRead.model_validate(prompt).model_dump(mode="json")


@router.patch("/prompts/{prompt_id}", response_model=PromptTemplateRead)
def update_prompt(
    prompt_id: str,
    data: PromptTemplateUpdate,
    session: SessionDep,
    workspace: EditorWorkspaceDep,
) -> dict[str, Any]:
    prompt = PromptTemplateRepository(session, workspace).update(prompt_id, data)
    AuditRecorder(session).record(
        workspace,
        action="prompt.update",
        resource_type="prompt_template",
        resource_id=prompt.id,
        outcome="success",
        request_id=current_request_id(),
    )
    session.commit()
    return PromptTemplateRead.model_validate(prompt).model_dump(mode="json")


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(prompt_id: str, session: SessionDep, workspace: EditorWorkspaceDep) -> Response:
    PromptTemplateRepository(session, workspace).delete(prompt_id)
    AuditRecorder(session).record(
        workspace,
        action="prompt.delete",
        resource_type="prompt_template",
        resource_id=prompt_id,
        outcome="success",
        request_id=current_request_id(),
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
