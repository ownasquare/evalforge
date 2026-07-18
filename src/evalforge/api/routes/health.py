"""Liveness, readiness, metrics, and safe capability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from evalforge.api.dependencies import ContainerDep
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
    worker_ready = container.executor.healthy
    if not database_ready or not worker_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if database_ready and worker_ready else "not_ready",
        "database": "ready" if database_ready else "unavailable",
        "worker": "ready" if worker_ready else "unavailable",
    }


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/meta", response_model=MetaRead)
def meta(container: ContainerDep) -> dict[str, object]:
    """Return stable, non-secret build and execution metadata."""
    return container_summary(container)


@router.get("/api/v1/capabilities")
def capabilities(container: ContainerDep) -> dict[str, object]:
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
        "proof": {
            "demo_mode": "deterministic_fixture_backed",
            "real_provider": "enabled" if settings.real_runs_enabled else "disabled",
            "production_validated": False,
        },
    }
