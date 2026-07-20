"""Server-authoritative hosted-pilot offer, access, request, and funnel routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Response, status

from evalforge.api.dependencies import (
    AdminWorkspaceDep,
    ContainerDep,
    SessionDep,
    ViewerWorkspaceDep,
)
from evalforge.commercial import CommercialPilotService, commercial_plan_catalog
from evalforge.observability import current_request_id
from evalforge.schemas import (
    ActivationEventRead,
    BillingEventRead,
    ClientActivationEventCreate,
    CommercialFunnelRead,
    CommercialPlanRead,
    TeamPilotRequestCreate,
    TeamPilotRequestRead,
    WorkspaceEntitlementRead,
)

router = APIRouter(prefix="/commercial", tags=["commercial"])

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


@router.get("/plans", response_model=list[CommercialPlanRead])
def list_commercial_plans(
    response: Response,
    container: ContainerDep,
    _workspace: ViewerWorkspaceDep,
) -> list[dict[str, object]]:
    _private_no_store(response)
    return commercial_plan_catalog(container.settings)


@router.get("/entitlement", response_model=WorkspaceEntitlementRead)
def get_workspace_entitlement(
    response: Response,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    container: ContainerDep,
) -> WorkspaceEntitlementRead:
    _private_no_store(response)
    return CommercialPilotService(session, workspace, container.settings).entitlement()


@router.post("/trial", response_model=WorkspaceEntitlementRead)
def start_hosted_trial(
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
    idempotency_key: IdempotencyKey,
) -> WorkspaceEntitlementRead:
    service = CommercialPilotService(session, workspace, container.settings)
    entitlement = service.start_trial(
        idempotency_key=idempotency_key,
        request_id=current_request_id(),
    )
    session.commit()
    _private_no_store(response)
    return entitlement


@router.post("/trial/cancel", response_model=WorkspaceEntitlementRead)
def cancel_hosted_trial(
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
    idempotency_key: IdempotencyKey,
) -> WorkspaceEntitlementRead:
    service = CommercialPilotService(session, workspace, container.settings)
    entitlement = service.cancel_trial(
        idempotency_key=idempotency_key,
        request_id=current_request_id(),
    )
    session.commit()
    _private_no_store(response)
    return entitlement


@router.get("/team-requests", response_model=list[TeamPilotRequestRead])
def list_team_pilot_requests(
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
) -> list[dict[str, Any]]:
    rows = CommercialPilotService(session, workspace, container.settings).list_team_requests()
    _private_no_store(response)
    return [TeamPilotRequestRead.model_validate(row).model_dump(mode="json") for row in rows]


@router.post(
    "/team-requests",
    response_model=TeamPilotRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def create_team_pilot_request(
    data: TeamPilotRequestCreate,
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    service = CommercialPilotService(session, workspace, container.settings)
    request, created = service.create_team_request(
        data,
        idempotency_key=idempotency_key,
        request_id=current_request_id(),
    )
    payload = TeamPilotRequestRead.model_validate(request).model_dump(mode="json")
    session.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    _private_no_store(response)
    return payload


@router.post(
    "/team-requests/{team_request_id}/cancel",
    response_model=TeamPilotRequestRead,
)
def cancel_team_pilot_request(
    team_request_id: str,
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    service = CommercialPilotService(session, workspace, container.settings)
    request = service.cancel_team_request(
        team_request_id,
        idempotency_key=idempotency_key,
        request_id=current_request_id(),
    )
    payload = TeamPilotRequestRead.model_validate(request).model_dump(mode="json")
    session.commit()
    _private_no_store(response)
    return payload


@router.get("/billing-events", response_model=list[BillingEventRead])
def list_billing_events(
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
) -> list[dict[str, Any]]:
    events = CommercialPilotService(session, workspace, container.settings).list_billing_events()
    _private_no_store(response)
    return [BillingEventRead.model_validate(event).model_dump(mode="json") for event in events]


@router.post(
    "/events",
    response_model=ActivationEventRead,
    status_code=status.HTTP_201_CREATED,
)
def record_activation_event(
    data: ClientActivationEventCreate,
    response: Response,
    session: SessionDep,
    workspace: ViewerWorkspaceDep,
    container: ContainerDep,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    service = CommercialPilotService(session, workspace, container.settings)
    event, created = service.record_client_event(data, idempotency_key=idempotency_key)
    payload = ActivationEventRead.model_validate(event).model_dump(mode="json")
    session.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    _private_no_store(response)
    return payload


@router.get("/events", response_model=list[ActivationEventRead])
def list_activation_events(
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
) -> list[dict[str, Any]]:
    events = CommercialPilotService(
        session,
        workspace,
        container.settings,
    ).list_activation_events()
    _private_no_store(response)
    return [ActivationEventRead.model_validate(event).model_dump(mode="json") for event in events]


@router.get("/funnel", response_model=CommercialFunnelRead)
def get_commercial_funnel(
    response: Response,
    session: SessionDep,
    workspace: AdminWorkspaceDep,
    container: ContainerDep,
) -> dict[str, object]:
    _private_no_store(response)
    return CommercialPilotService(session, workspace, container.settings).funnel()


def _private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
