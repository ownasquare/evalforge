"""Persist atomic preflight evidence, context chunks, and unknown-cost consent.

Revision ID: 0002_preflight_context_cost_ack
Revises: 0001_initial_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_preflight_context_cost_ack"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQLITE_BATCH_DROP = {
    "test_cases": sa.text("DROP TABLE IF EXISTS _alembic_tmp_test_cases"),
    "evaluation_runs": sa.text("DROP TABLE IF EXISTS _alembic_tmp_evaluation_runs"),
}


def _column_names(table_name: str) -> set[str]:
    """Return live columns so a non-transactional SQLite retry can resume."""
    return {str(column["name"]) for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _clear_stale_sqlite_batch_table(table_name: str) -> None:
    """Remove Alembic batch artifacts left by an interrupted SQLite DDL step."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(_SQLITE_BATCH_DROP[table_name])


def upgrade() -> None:
    """Add backward-compatible, non-null evidence fields with safe defaults."""
    _clear_stale_sqlite_batch_table("test_cases")
    if "context_chunks" not in _column_names("test_cases"):
        op.add_column(
            "test_cases",
            sa.Column(
                "context_chunks",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )

    _clear_stale_sqlite_batch_table("evaluation_runs")
    run_columns = _column_names("evaluation_runs")
    if "preflight_snapshot" not in run_columns:
        op.add_column(
            "evaluation_runs",
            sa.Column(
                "preflight_snapshot",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    if "acknowledge_unknown_cost" not in run_columns:
        op.add_column(
            "evaluation_runs",
            sa.Column(
                "acknowledge_unknown_cost",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    """Remove fields introduced by this revision."""
    _clear_stale_sqlite_batch_table("evaluation_runs")
    run_columns = _column_names("evaluation_runs")
    if "acknowledge_unknown_cost" in run_columns:
        op.drop_column("evaluation_runs", "acknowledge_unknown_cost")
    if "preflight_snapshot" in run_columns:
        op.drop_column("evaluation_runs", "preflight_snapshot")

    _clear_stale_sqlite_batch_table("test_cases")
    if "context_chunks" in _column_names("test_cases"):
        op.drop_column("test_cases", "context_chunks")
