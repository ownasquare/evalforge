"""Liveness, readiness, metrics, and safe capability endpoints."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from evalforge.api.dependencies import ContainerDep, ViewerWorkspaceDep
from evalforge.container import container_summary
from evalforge.database import check_database_readiness
from evalforge.evaluation.service import default_metric_configurations
from evalforge.schemas import MetaRead

router = APIRouter(tags=["system"])


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
def ready(container: ContainerDep, response: Response) -> dict[str, object]:
    try:
        database_ready = check_database_readiness(container.engine)
    except Exception:
        database_ready = False
    executor_ready = container.executor.healthy
    service_ready = database_ready and executor_ready
    worker_observed = container.executor.worker_observed
    if not service_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if service_ready else "not_ready",
        "database": "ready" if database_ready else "unavailable",
        "worker": (
            "ready"
            if worker_observed and executor_ready
            else "unavailable"
            if worker_observed
            else "external_unobserved"
        ),
        "worker_observed": worker_observed,
        "executor_role": container.executor.role,
    }


@router.get("/metrics", include_in_schema=False)
def metrics(
    container: ContainerDep,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Response:
    configured = container.settings.metrics_bearer_token
    if configured is None and container.settings.auth_mode == "oidc":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics are unavailable until backend-only authentication is configured.",
        )
    if configured is not None:
        expected = f"Bearer {configured.get_secret_value()}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Metrics authentication is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/meta", response_model=MetaRead)
def meta(container: ContainerDep, _workspace: ViewerWorkspaceDep) -> dict[str, object]:
    """Return stable, non-secret build and execution metadata."""
    return container_summary(container)


@router.get("/api/v1/capabilities")
def capabilities(container: ContainerDep, _workspace: ViewerWorkspaceDep) -> dict[str, object]:
    settings = container.settings
    return {
        **container_summary(container),
        "providers": settings.provider_capabilities(),
        "metrics": [
            metric.model_dump(mode="json")
            for metric in default_metric_configurations(container.metrics)
        ],
        "limits": {
            "max_cases_per_dataset": settings.max_cases_per_dataset,
            "max_variants_per_run": settings.max_variants_per_run,
            "max_calls_per_run": settings.max_calls_per_run,
            "max_output_tokens": settings.max_output_tokens,
            "max_concurrent_generations": settings.max_concurrent_generations,
            "max_estimated_input_tokens_per_run": (settings.max_estimated_input_tokens_per_run),
            "input_token_overhead_per_request": settings.input_token_overhead_per_request,
            "max_rendered_prompt_chars_per_call": (settings.max_rendered_prompt_chars_per_call),
            "max_estimated_cost_micro_usd_per_run": (settings.max_estimated_cost_micro_usd_per_run),
        },
        "provider_safety": {
            "external_data_transfer_consent_required": True,
            "user_spend_limit_required": True,
            "spend_limit_basis": "known_price_preflight_estimate",
        },
        "commercial": {
            "pilot_enabled": settings.commercial_pilot_enabled,
            "hosted": settings.auth_mode == "oidc",
            "trial_days": settings.hosted_trial_days,
            "trial_seat_limit": settings.hosted_trial_seat_limit,
            "payment_path": "qualified_team_request",
            "live_money": False,
        },
        "proof": {
            "demo_mode": "deterministic_fixture_backed",
            "real_provider": "enabled" if settings.real_runs_enabled else "disabled",
            "production_validated": False,
        },
    }
