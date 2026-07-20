"""SQLAlchemy engine and request-scoped session lifecycle."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool, StaticPool

from evalforge.config import Settings


class Base(DeclarativeBase):
    """Declarative base shared by the domain mappings and Alembic."""


SessionFactory = sessionmaker[Session]
EXPECTED_SCHEMA_REVISION = "0006_commercial_pilot"
REQUIRED_SCHEMA_COLUMNS = {
    "evaluation_runs": {
        "preflight_snapshot",
        "acknowledge_unknown_cost",
        "workspace_id",
        "requested_by_user_id",
        "lease_owner",
        "lease_token",
        "lease_epoch",
        "lease_expires_at",
        "claim_attempts",
        "next_claim_at",
    },
    "test_cases": {"context_chunks", "workspace_id"},
    "datasets": {"workspace_id"},
    "prompt_templates": {"workspace_id"},
    "model_profiles": {"workspace_id"},
    "run_candidates": {"workspace_id"},
    "evaluation_results": {"workspace_id"},
    "execution_attempts": {
        "workspace_id",
        "run_id",
        "lease_owner",
        "lease_token",
        "lease_epoch",
        "heartbeat_at",
        "outcome",
    },
    "workspace_entitlements": {
        "workspace_id",
        "plan_code",
        "status",
        "seat_limit",
        "current_period_end",
    },
    "billing_events": {
        "workspace_id",
        "provider",
        "provider_event_id",
        "payload_sha256",
    },
    "team_pilot_requests": {
        "workspace_id",
        "requested_by_user_id",
        "status",
        "idempotency_key",
    },
    "activation_events": {
        "workspace_id",
        "name",
        "event_key",
        "run_id",
    },
}


def _is_file_backed_sqlite(url: URL) -> bool:
    if url.get_backend_name() != "sqlite":
        return False
    if url.database in {None, "", ":memory:"}:
        return False
    return url.query.get("mode") != "memory"


def _prepare_sqlite_directory(url: URL) -> None:
    if not _is_file_backed_sqlite(url) or url.database is None:
        return
    database_path = Path(url.database).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)


def _install_sqlite_pragmas(
    engine: Engine,
    *,
    busy_timeout_ms: int,
    file_backed: bool,
) -> None:
    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            if file_backed:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def create_database_engine(
    settings_or_url: Settings | str | URL,
    *,
    sqlite_busy_timeout_ms: int | None = None,
    poolclass: type[Pool] | None = None,
) -> Engine:
    """Create an engine with SQLite safety settings and PostgreSQL-safe options.

    No global engine or scoped session is created here. Each application instance
    owns an engine and a session factory, and each request/worker calls that factory
    for an independent ``Session``.
    """

    if isinstance(settings_or_url, Settings):
        url = make_url(settings_or_url.database_url)
        timeout_ms = settings_or_url.sqlite_busy_timeout_ms
    else:
        url = make_url(settings_or_url)
        timeout_ms = 5_000
    if sqlite_busy_timeout_ms is not None:
        timeout_ms = sqlite_busy_timeout_ms

    engine_options: dict[str, Any] = {
        "future": True,
        "pool_pre_ping": True,
    }
    if poolclass is not None:
        engine_options["poolclass"] = poolclass

    if url.get_backend_name() == "sqlite":
        file_backed = _is_file_backed_sqlite(url)
        _prepare_sqlite_directory(url)
        engine_options["connect_args"] = {
            "check_same_thread": False,
            "timeout": timeout_ms / 1_000,
        }
        if not file_backed and poolclass is None:
            engine_options["poolclass"] = StaticPool

        engine = create_engine(url, **engine_options)
        _install_sqlite_pragmas(
            engine,
            busy_timeout_ms=timeout_ms,
            file_backed=file_backed,
        )
        return engine

    return create_engine(url, **engine_options)


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(factory: SessionFactory) -> Iterator[Session]:
    """Own one short transaction and always release its connection."""

    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def session_dependency(factory: SessionFactory) -> Generator[Session, None, None]:
    """Yield one session for a FastAPI request without sharing thread-local state."""

    session = factory()
    try:
        yield session
    finally:
        session.close()


def check_database_connectivity(engine: Engine) -> bool:
    """Perform the smallest read-only connectivity check."""

    with engine.connect() as connection:
        return bool(connection.execute(text("SELECT 1")).scalar_one() == 1)


def check_database_readiness(engine: Engine) -> bool:
    """Require connectivity, every domain table, and the expected migration revision."""

    with engine.connect() as connection:
        if connection.execute(text("SELECT 1")).scalar_one() != 1:
            return False
        table_names = set(inspect(connection).get_table_names())
        expected_tables = set(Base.metadata.tables)
        if not expected_tables.issubset(table_names) or "alembic_version" not in table_names:
            return False
        for table_name, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
            actual_columns = {
                str(column["name"]) for column in inspect(connection).get_columns(table_name)
            }
            if not required_columns.issubset(actual_columns):
                return False
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        return revision == EXPECTED_SCHEMA_REVISION
