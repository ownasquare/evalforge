"""Add durable run leases and execution-attempt evidence.

Revision ID: 0004_durable_execution_leases
Revises: 0003_identity_tenant_scope
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_durable_execution_leases"
down_revision: str | None = "0003_identity_tenant_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
DOMAIN_TABLES = (
    "datasets",
    "test_cases",
    "prompt_templates",
    "model_profiles",
    "evaluation_runs",
    "run_candidates",
    "evaluation_results",
)


def upgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("claim_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "next_claim_at",
            sa.DateTime(timezone=True),
            nullable=False,
            # SQLite permits only a constant when ALTER TABLE adds a column.
            # Existing queued work should be immediately claimable after upgrade.
            server_default=sa.text("'1970-01-01 00:00:00'"),
        ),
    )
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_evaluation_runs_lease_epoch_nonnegative",
            "evaluation_runs",
            "lease_epoch >= 0",
        )
        op.create_check_constraint(
            "ck_evaluation_runs_claim_attempts_nonnegative",
            "evaluation_runs",
            "claim_attempts >= 0",
        )
    op.create_index(
        "ix_evaluation_runs_claimable",
        "evaluation_runs",
        ["status", "next_claim_at", "lease_expires_at", "queued_at"],
    )

    op.create_table(
        "execution_attempts",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=False),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=40), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lease_epoch > 0",
            name="ck_execution_attempts_epoch_positive",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_execution_attempts_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_execution_attempts_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_execution_attempts_workspace_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "lease_epoch",
            name="uq_execution_attempts_run_epoch",
        ),
        sa.UniqueConstraint("lease_token", name="uq_execution_attempts_lease_token"),
    )
    op.create_index(
        "ix_execution_attempts_run_started",
        "execution_attempts",
        ["run_id", "started_at"],
    )
    op.create_index(
        "ix_execution_attempts_owner_active",
        "execution_attempts",
        ["lease_owner", "finished_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in DOMAIN_TABLES:
        foreign_count = bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {table_name} "  # noqa: S608  # nosec B608
                "WHERE workspace_id <> :workspace_id"
            ),
            {"workspace_id": LOCAL_WORKSPACE_ID},
        ).scalar_one()
        if int(foreign_count):
            raise RuntimeError(
                "identity scope downgrade refused: non-local workspace data exists; "
                "restore a pre-migration backup instead"
            )
    op.drop_index("ix_execution_attempts_owner_active", table_name="execution_attempts")
    op.drop_index("ix_execution_attempts_run_started", table_name="execution_attempts")
    op.drop_table("execution_attempts")
    op.drop_index("ix_evaluation_runs_claimable", table_name="evaluation_runs")
    with op.batch_alter_table("evaluation_runs") as batch:
        if op.get_bind().dialect.name != "sqlite":
            batch.drop_constraint(
                "ck_evaluation_runs_claim_attempts_nonnegative",
                type_="check",
            )
            batch.drop_constraint(
                "ck_evaluation_runs_lease_epoch_nonnegative",
                type_="check",
            )
        batch.drop_column("next_claim_at")
        batch.drop_column("claim_attempts")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_epoch")
        batch.drop_column("lease_token")
        batch.drop_column("lease_owner")
