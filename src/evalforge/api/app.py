"""FastAPI application factory for EvalForge."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from evalforge import __version__
from evalforge.api.routes import (
    analytics,
    calibrations,
    commercial,
    datasets,
    health,
    models,
    prompts,
    runs,
    session,
)
from evalforge.config import Settings, get_settings
from evalforge.container import AppContainer, build_container
from evalforge.errors import EvalForgeError
from evalforge.observability import (
    BodyLimitMiddleware,
    MetricsMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    current_request_id,
    get_logger,
)
from evalforge.repositories import (
    ConflictError as RepositoryConflictError,
)
from evalforge.repositories import (
    NotFoundError as RepositoryNotFoundError,
)
from evalforge.repositories import (
    ValidationError as RepositoryValidationError,
)
from evalforge.security.permissions import local_workspace_context
from evalforge.seed import seed_demo

ContainerFactory = Callable[[Settings], AppContainer]


def _error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "request_id": current_request_id(),
                "details": details or [],
            }
        },
    )


def create_app(
    settings: Settings | None = None,
    *,
    container: AppContainer | None = None,
    container_factory: ContainerFactory = build_container,
) -> FastAPI:
    """Create one independently testable API application."""
    resolved_settings = settings or get_settings()
    configure_logging(level=resolved_settings.log_level, json_logs=resolved_settings.json_logs)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        resources = container if container is not None else container_factory(resolved_settings)
        application.state.container = resources
        try:
            if resolved_settings.seed_demo:
                session = resources.session_factory()
                try:
                    seed_demo(session, local_workspace_context())
                    session.commit()
                finally:
                    session.close()
            await resources.executor.start()
            yield
        finally:
            if owned:
                await resources.close()
            else:
                await resources.executor.close()

    docs_enabled = resolved_settings.environment != "production"
    application = FastAPI(
        title="EvalForge API",
        description="Provider-neutral LLM evaluation with immutable evidence.",
        version=__version__,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    application.state.container = container

    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_strings,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-EvalForge-Workspace-ID",
            "X-Request-ID",
        ],
        expose_headers=[
            "Content-Disposition",
            "Location",
            "X-EvalForge-Payload-SHA256",
            "X-EvalForge-Sample-Size",
            "X-Request-ID",
        ],
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=resolved_settings.trusted_hosts,
    )
    application.add_middleware(BodyLimitMiddleware, max_bytes=10 * 1024 * 1024)
    application.add_middleware(MetricsMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(RequestContextMiddleware)

    @application.exception_handler(EvalForgeError)
    async def handle_expected_error(_request: Request, error: EvalForgeError) -> JSONResponse:
        return _error_response(
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            retryable=error.retryable,
            details=error.details,
            headers=getattr(error, "headers", None),
        )

    @application.exception_handler(RepositoryNotFoundError)
    async def handle_repository_not_found(
        _request: Request, _error: RepositoryNotFoundError
    ) -> JSONResponse:
        return _error_response(code="not_found", message="Resource not found.", status_code=404)

    @application.exception_handler(RepositoryConflictError)
    async def handle_repository_conflict(
        _request: Request, _error: RepositoryConflictError
    ) -> JSONResponse:
        return _error_response(
            code="conflict", message="The resource conflicts with existing data.", status_code=409
        )

    @application.exception_handler(RepositoryValidationError)
    async def handle_repository_validation(
        _request: Request, error: RepositoryValidationError
    ) -> JSONResponse:
        return _error_response(code="validation_error", message=str(error), status_code=422)

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            code="validation_error",
            message="The request did not match the API contract.",
            status_code=422,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, error: Exception) -> JSONResponse:
        get_logger("api").error(
            "unhandled_error",
            error_type=type(error).__name__,
            request_id=current_request_id(),
        )
        return _error_response(
            code="internal_error",
            message="An unexpected error occurred.",
            status_code=500,
        )

    application.include_router(health.router)
    application.include_router(session.router, prefix="/api/v1")
    application.include_router(commercial.router, prefix="/api/v1")
    application.include_router(datasets.router, prefix="/api/v1")
    application.include_router(prompts.router, prefix="/api/v1")
    application.include_router(models.router, prefix="/api/v1")
    application.include_router(runs.router, prefix="/api/v1")
    application.include_router(calibrations.router, prefix="/api/v1")
    application.include_router(analytics.router, prefix="/api/v1")

    @application.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"name": "EvalForge API", "version": __version__, "health": "/health/live"}

    return application


app = create_app()
