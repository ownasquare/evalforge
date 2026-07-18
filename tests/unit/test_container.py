from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import evalforge.container as container_module
from evalforge.api.app import create_app
from evalforge.config import Settings
from evalforge.container import AppContainer, build_adapter_registry, build_container
from evalforge.database import check_database_readiness
from evalforge.evaluation.adapters import AdapterRegistry
from evalforge.evaluation.metrics import MetricRegistry
from evalforge.security.auth import LocalAuthenticator


class AsyncCloseClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class ClientOwningAdapter:
    def __init__(self, client: AsyncCloseClient) -> None:
        self._client = client

    async def generate(self, _request: Any) -> Any:
        raise AssertionError("generation is not part of this test")


class FakeExecutor:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FailingStartExecutor(FakeExecutor):
    async def start(self) -> None:
        raise RuntimeError("intentional executor start failure")


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_container_awaits_async_client_close() -> None:
    client = AsyncCloseClient()
    adapter_registry = AdapterRegistry()
    adapter_registry.register("async", ClientOwningAdapter(client))
    executor = FakeExecutor()
    engine = FakeEngine()
    container = AppContainer(
        settings=Settings(_env_file=None, environment="test"),
        engine=engine,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        metrics=MetricRegistry(),
        adapters=adapter_registry,
        authenticator=LocalAuthenticator(),
        evaluation_service=None,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
    )

    await container.close()

    assert client.closed is True
    assert executor.closed is True
    assert engine.disposed is True


def test_owned_container_is_closed_when_executor_startup_fails() -> None:
    executor = FailingStartExecutor()
    engine = FakeEngine()
    container = AppContainer(
        settings=Settings(_env_file=None, environment="test"),
        engine=engine,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        metrics=MetricRegistry(),
        adapters=AdapterRegistry(),
        authenticator=LocalAuthenticator(),
        evaluation_service=None,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
    )
    application = create_app(
        container.settings,
        container_factory=lambda _settings: container,
    )

    with (
        pytest.raises(RuntimeError, match="intentional executor start failure"),
        TestClient(application),
    ):
        pass

    assert executor.closed is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_in_memory_database_migrates_on_the_owned_engine() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
    )
    container = build_container(settings, migrate=True)
    try:
        assert check_database_readiness(container.engine) is True
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_auto_migrate_false_skips_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        auto_migrate=False,
    )
    migration_called = False

    def forbidden_migration(*_args: object, **_kwargs: object) -> None:
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(container_module, "apply_migrations", forbidden_migration)
    container = build_container(settings)
    try:
        assert migration_called is False
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_no_auth_compatible_client_sends_no_authorization_header() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        compatible_auth_mode="none",
        compatible_base_url="http://127.0.0.1:11434/v1",
        compatible_model_allowlist=["local-model"],
    )
    registry = build_adapter_registry(settings)
    adapter = registry.get("openai-compatible")
    client = adapter._client
    try:
        assert client.auth_headers == {}
    finally:
        await client.close()
