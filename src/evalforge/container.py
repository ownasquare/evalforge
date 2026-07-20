"""Application resource ownership and dependency assembly."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from openai import AsyncOpenAI
from sqlalchemy import Connection, Engine

from evalforge.config import Settings
from evalforge.database import SessionFactory, create_database_engine, create_session_factory
from evalforge.evaluation.adapters import (
    AdapterRegistry,
    OpenAICompatibleAdapter,
    validate_backend_base_url,
)
from evalforge.evaluation.executor import ApiOnlyRunExecutor, LocalRunExecutor, RunExecutor
from evalforge.evaluation.metrics import MetricRegistry
from evalforge.evaluation.service import EvaluationService
from evalforge.security.auth import AuthBackend, LocalAuthenticator, OidcJwtAuthenticator


class _NoAuthAsyncOpenAI(AsyncOpenAI):
    """OpenAI-compatible client variant that deliberately sends no auth header."""

    @property
    def auth_headers(self) -> dict[str, str]:
        return {}


@dataclass(slots=True)
class AppContainer:
    """Resources owned by one FastAPI application instance."""

    settings: Settings
    engine: Engine
    session_factory: SessionFactory
    metrics: MetricRegistry
    adapters: AdapterRegistry
    authenticator: AuthBackend
    evaluation_service: EvaluationService
    executor: RunExecutor

    async def close(self) -> None:
        await self.executor.close()
        for name in self.adapters.names:
            adapter = self.adapters.get(name)
            client = getattr(adapter, "_client", None)
            async_closer = getattr(client, "aclose", None)
            closer = async_closer if callable(async_closer) else getattr(client, "close", None)
            if callable(closer):
                outcome = closer()
                if inspect.isawaitable(outcome):
                    await outcome
        self.engine.dispose()


def apply_migrations(settings: Settings, *, connection: Connection | None = None) -> None:
    """Apply Alembic migrations using the resolved runtime database URL."""
    configuration = Config()
    configuration.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    if connection is None:
        configuration.attributes["database_url"] = settings.database_url
    else:
        configuration.attributes["connection"] = connection
    command.upgrade(configuration, "head")
    if connection is not None and connection.dialect.name == "sqlite":
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
            raise RuntimeError("SQLite foreign-key enforcement could not be restored")


def build_adapter_registry(settings: Settings) -> AdapterRegistry:
    registry = AdapterRegistry()
    if settings.openai_api_key is not None:
        registry.register(
            "openai",
            OpenAICompatibleAdapter.from_backend_settings(settings, provider="openai"),
        )
    if settings.compatible_api_key is not None or settings.compatible_auth_mode == "none":
        validate_backend_base_url(
            str(settings.compatible_base_url),
            require_loopback=settings.compatible_auth_mode == "none",
        )
        secret = (
            settings.compatible_api_key.get_secret_value()
            if settings.compatible_api_key is not None
            else "local-no-auth"
        )
        client_class = (
            _NoAuthAsyncOpenAI if settings.compatible_auth_mode == "none" else AsyncOpenAI
        )
        client = client_class(
            api_key=secret,
            base_url=str(settings.compatible_base_url),
            timeout=settings.provider_timeout_seconds,
            max_retries=0,
        )
        registry.register(
            "openai-compatible",
            OpenAICompatibleAdapter(
                client=client,
                provider="openai-compatible",
            ),
        )
    return registry


def build_authenticator(settings: Settings) -> AuthBackend:
    if settings.auth_mode == "local":
        return LocalAuthenticator()
    if (
        settings.oidc_issuer is None
        or settings.oidc_audience is None
        or settings.oidc_jwks_url is None
    ):
        raise ValueError("OIDC configuration is incomplete")
    return OidcJwtAuthenticator(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_url=str(settings.oidc_jwks_url),
        algorithms=tuple(settings.oidc_algorithms),
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        jwks_cache_seconds=settings.oidc_jwks_cache_seconds,
        jwks_timeout_seconds=settings.oidc_jwks_timeout_seconds,
    )


def build_container(settings: Settings, *, migrate: bool | None = None) -> AppContainer:
    """Build independently testable application resources."""
    engine = create_database_engine(settings)
    try:
        should_migrate = settings.auto_migrate if migrate is None else migrate
        if should_migrate:
            with engine.connect() as connection:
                apply_migrations(settings, connection=connection)
        session_factory = create_session_factory(engine)
        metrics = MetricRegistry()
        adapters = build_adapter_registry(settings)
        authenticator = build_authenticator(settings)
        service = EvaluationService(
            settings=settings,
            session_factory=session_factory,
            adapters=adapters,
            metrics=metrics,
        )
        executor: RunExecutor
        if settings.executor_mode == "api_only":
            executor = ApiOnlyRunExecutor()
        else:
            executor = LocalRunExecutor(
                service,
                poll_interval_seconds=settings.worker_poll_interval_seconds,
                role=settings.executor_mode,
            )
        return AppContainer(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            metrics=metrics,
            adapters=adapters,
            authenticator=authenticator,
            evaluation_service=service,
            executor=executor,
        )
    except BaseException:
        engine.dispose()
        raise


def container_summary(container: AppContainer) -> dict[str, Any]:
    """Return safe capability data without credentials or provider URLs."""
    return {
        "version": container.settings.application_version,
        "environment": container.settings.environment,
        "auth_mode": container.settings.auth_mode,
        "database_backend": container.settings.database_backend,
        "executor": container.executor.role,
        "registered_adapters": ["deterministic", *container.adapters.names],
    }
