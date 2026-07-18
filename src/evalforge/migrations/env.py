"""Alembic environment backed by the same typed application settings."""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import Connection, pool

from alembic import context

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
        context.configure(
            connection=provided_connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=provided_connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    settings = _runtime_settings()
    connectable = create_database_engine(settings, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
