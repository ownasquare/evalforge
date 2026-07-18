"""Server-approved model-profile routes without credential fields."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response, status

from evalforge.api.dependencies import (
    AdminWorkspaceDep,
    ContainerDep,
    SessionDep,
    ViewerWorkspaceDep,
)
from evalforge.audit import AuditRecorder
from evalforge.errors import CapabilityError
from evalforge.evaluation.adapters import resolve_demo_profile
from evalforge.models import ApiMode
from evalforge.observability import current_request_id
from evalforge.repositories import ModelProfileRepository
from evalforge.schemas import (
    ModelProfileCreate,
    ModelProfileRead,
    ModelProfileUpdate,
    Page,
    validate_generation_parameters,
)

router = APIRouter(tags=["models"])


@router.get("/models", response_model=Page[ModelProfileRead])
def list_models(
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows, total = ModelProfileRepository(session, workspace).list(page=page, limit=limit)
    return {
        "items": [ModelProfileRead.model_validate(row).model_dump(mode="json") for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/models", status_code=status.HTTP_201_CREATED, response_model=ModelProfileRead)
def create_model(
    data: ModelProfileCreate,
    session: SessionDep,
    container: ContainerDep,
    workspace: AdminWorkspaceDep,
) -> dict[str, Any]:
    _validate_profile(data, container)
    profile = ModelProfileRepository(session, workspace).create(data)
    AuditRecorder(session).record(
        workspace,
        action="model.create",
        resource_type="model_profile",
        resource_id=profile.id,
        outcome="success",
        request_id=current_request_id(),
    )
    session.commit()
    return ModelProfileRead.model_validate(profile).model_dump(mode="json")


@router.get("/models/{model_id}", response_model=ModelProfileRead)
def get_model(model_id: str, session: SessionDep, workspace: ViewerWorkspaceDep) -> dict[str, Any]:
    profile = ModelProfileRepository(session, workspace).get(model_id)
    return ModelProfileRead.model_validate(profile).model_dump(mode="json")


@router.patch("/models/{model_id}", response_model=ModelProfileRead)
def update_model(
    model_id: str,
    data: ModelProfileUpdate,
    session: SessionDep,
    container: ContainerDep,
    workspace: AdminWorkspaceDep,
) -> dict[str, Any]:
    repository = ModelProfileRepository(session, workspace)
    current = repository.get(model_id)
    parameters = (
        data.generation_parameters
        if data.generation_parameters is not None
        else current.generation_parameters
    )
    _validate_generation_parameters(parameters, current.api_mode, container)
    profile = repository.update(model_id, data)
    AuditRecorder(session).record(
        workspace,
        action="model.update",
        resource_type="model_profile",
        resource_id=profile.id,
        outcome="success",
        request_id=current_request_id(),
    )
    session.commit()
    return ModelProfileRead.model_validate(profile).model_dump(mode="json")


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: str, session: SessionDep, workspace: AdminWorkspaceDep) -> Response:
    ModelProfileRepository(session, workspace).delete(model_id)
    AuditRecorder(session).record(
        workspace,
        action="model.delete",
        resource_type="model_profile",
        resource_id=model_id,
        outcome="success",
        request_id=current_request_id(),
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _validate_profile(data: ModelProfileCreate, container: ContainerDep) -> None:
    _validate_generation_parameters(data.generation_parameters, data.api_mode, container)
    if data.api_mode is ApiMode.DETERMINISTIC:
        if data.provider != "demo" or not data.model_name.startswith("demo-"):
            raise CapabilityError(
                "Deterministic profiles must use the demo provider and a demo-* model name."
            )
        try:
            resolve_demo_profile(data.model_name)
        except ValueError as exc:
            raise CapabilityError("The deterministic demo model is not supported.") from exc
        return
    if data.provider == "openai":
        allowed = container.settings.openai_model_allowlist
    elif data.provider == "openai-compatible":
        allowed = container.settings.compatible_model_allowlist
    else:
        raise CapabilityError("Real profiles must use a configured provider identifier.")
    if data.model_name not in allowed:
        raise CapabilityError("The model is not in the server-side allowlist.")


def _validate_generation_parameters(
    parameters: dict[str, Any], api_mode: ApiMode, container: ContainerDep
) -> None:
    try:
        validate_generation_parameters(
            parameters,
            max_output_tokens=container.settings.max_output_tokens,
            allow_seed=api_mode is ApiMode.DETERMINISTIC,
        )
    except ValueError as exc:
        raise CapabilityError(str(exc)) from exc
