"""Prompt-library CRUD routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response, status

from evalforge.api.dependencies import SessionDep
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
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows, total = PromptTemplateRepository(session).list(page=page, limit=limit)
    return {
        "items": [PromptTemplateRead.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/prompts", status_code=status.HTTP_201_CREATED, response_model=PromptTemplateRead)
def create_prompt(data: PromptTemplateCreate, session: SessionDep) -> dict[str, Any]:
    prompt = PromptTemplateRepository(session).create(data)
    session.commit()
    return PromptTemplateRead.model_validate(prompt).model_dump(mode="json")


@router.get("/prompts/{prompt_id}", response_model=PromptTemplateRead)
def get_prompt(prompt_id: str, session: SessionDep) -> dict[str, Any]:
    prompt = PromptTemplateRepository(session).get(prompt_id)
    return PromptTemplateRead.model_validate(prompt).model_dump(mode="json")


@router.patch("/prompts/{prompt_id}", response_model=PromptTemplateRead)
def update_prompt(
    prompt_id: str, data: PromptTemplateUpdate, session: SessionDep
) -> dict[str, Any]:
    prompt = PromptTemplateRepository(session).update(prompt_id, data)
    session.commit()
    return PromptTemplateRead.model_validate(prompt).model_dump(mode="json")


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(prompt_id: str, session: SessionDep) -> Response:
    PromptTemplateRepository(session).delete(prompt_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
