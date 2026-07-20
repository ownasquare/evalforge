"""Alembic environment backed by the same typed application settings."""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import Connection, pool

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evalforge import models as _models  # noqa: E402,F401
from evalforge.config import Settings, get_settings  # noqa: E402
from evalforge.database import Base, create_database_engine  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _prepare_sqlite_migration_connection(connection: Connection) -> None:
    """Disable enforcement for SQLite batch table rebuilds on this connection."""
    if connection.dialect.name != "sqlite":
        return
    if connection.in_transaction():
        connection.commit()
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("SQLite migration requires an active driver connection")
    driver_connection.commit()
    cursor = driver_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("PRAGMA foreign_keys")
        if cursor.fetchone() != (0,):
            raise RuntimeError("SQLite foreign-key enforcement could not be suspended")
    finally:
        cursor.close()


def _finish_sqlite_migration_connection(connection: Connection) -> None:
    """Validate rebuilt relationships and restore enforcement before handoff."""
    if connection.dialect.name != "sqlite":
        return
    if connection.in_transaction():
        connection.commit()
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("SQLite migration requires an active driver connection")
    driver_connection.commit()
    cursor = driver_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_key_check")
        if cursor.fetchall():
            raise RuntimeError("SQLite migration produced invalid foreign-key relationships")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA foreign_keys")
        if cursor.fetchone() != (1,):
            raise RuntimeError("SQLite foreign-key enforcement could not be restored")
    finally:
        cursor.close()


def _runtime_settings() -> Settings:
    """Honor an in-process migration URL without routing it through config text."""
    configured_url = config.attributes.get("database_url")
    settings = get_settings()
    if isinstance(configured_url, str):
        return settings.model_copy(update={"database_url": configured_url})
    return settings


def run_migrations_offline() -> None:
    settings = _runtime_settings()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_connection = config.attributes.get("connection")
    if isinstance(provided_connection, Connection):
        _prepare_sqlite_migration_connection(provided_connection)
        try:
            context.configure(
                connection=provided_connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=provided_connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            _finish_sqlite_migration_connection(provided_connection)
        return
    settings = _runtime_settings()
    connectable = create_database_engine(settings, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            _prepare_sqlite_migration_connection(connection)
            try:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                    render_as_batch=connection.dialect.name == "sqlite",
                )
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                _finish_sqlite_migration_connection(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
