"""Persist content-minimized human-calibration evidence.

Revision ID: 0005_calibration_reports
Revises: 0004_durable_execution_leases
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_calibration_reports"
down_revision: str | None = "0004_durable_execution_leases"
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
    op.create_index(
        "ux_run_candidates_workspace_run_id",
        "run_candidates",
        ["workspace_id", "run_id", "id"],
        unique=True,
    )
    op.create_table(
        "calibration_reports",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("run_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("metric_version", sa.String(length=128), nullable=False),
        sa.Column("metric_direction", sa.String(length=32), nullable=False),
        sa.Column("selected_threshold", sa.Float(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_kind", sa.String(length=64), nullable=False),
        sa.Column("production_validated", sa.Boolean(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("human_pass_count", sa.Integer(), nullable=False),
        sa.Column("human_fail_count", sa.Integer(), nullable=False),
        sa.Column("reviewer_count", sa.Integer(), nullable=False),
        sa.Column("precision", sa.Float(), nullable=False),
        sa.Column("recall", sa.Float(), nullable=False),
        sa.Column("f1", sa.Float(), nullable=False),
        sa.Column("report_payload", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "selected_threshold >= 0 AND selected_threshold <= 1",
            name="ck_calibration_reports_threshold_unit_interval",
        ),
        sa.CheckConstraint(
            "metric_direction IN ('higher_is_better', 'lower_is_better')",
            name="ck_calibration_reports_metric_direction",
        ),
        sa.CheckConstraint(
            "schema_version = 'evalforge.calibration-report.v1'",
            name="ck_calibration_reports_schema_version",
        ),
        sa.CheckConstraint(
            "evidence_kind = 'offline_statistical_evidence'",
            name="ck_calibration_reports_evidence_kind",
        ),
        sa.CheckConstraint(
            "production_validated = false",
            name="ck_calibration_reports_not_production_validated",
        ),
        sa.CheckConstraint(
            "length(manifest_sha256) = 64 AND manifest_sha256 = lower(manifest_sha256)",
            name="ck_calibration_reports_manifest_hash",
        ),
        sa.CheckConstraint(
            "length(report_sha256) = 64 AND report_sha256 = lower(report_sha256)",
            name="ck_calibration_reports_report_hash",
        ),
        sa.CheckConstraint(
            "sample_size > 0",
            name="ck_calibration_reports_sample_positive",
        ),
        sa.CheckConstraint(
            "human_pass_count >= 0 AND human_fail_count >= 0",
            name="ck_calibration_reports_human_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "human_pass_count + human_fail_count = sample_size",
            name="ck_calibration_reports_human_counts_total",
        ),
        sa.CheckConstraint(
            "reviewer_count > 0",
            name="ck_calibration_reports_reviewer_positive",
        ),
        sa.CheckConstraint(
            "reviewer_count <= sample_size",
            name="ck_calibration_reports_reviewer_within_sample",
        ),
        sa.CheckConstraint(
            "precision >= 0 AND precision <= 1",
            name="ck_calibration_reports_precision_unit_interval",
        ),
        sa.CheckConstraint(
            "recall >= 0 AND recall <= 1",
            name="ck_calibration_reports_recall_unit_interval",
        ),
        sa.CheckConstraint(
            "f1 >= 0 AND f1 <= 1",
            name="ck_calibration_reports_f1_unit_interval",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_candidate_id"],
            ["run_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_calibration_reports_workspace_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_candidate_id"],
            ["run_candidates.workspace_id", "run_candidates.id"],
            name="fk_calibration_reports_workspace_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id", "run_candidate_id"],
            ["run_candidates.workspace_id", "run_candidates.run_id", "run_candidates.id"],
            name="fk_calibration_reports_workspace_run_candidate",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_calibration_reports_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "run_id",
            "run_candidate_id",
            "report_sha256",
            name="uq_calibration_reports_workspace_report_hash",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "run_id",
            "run_candidate_id",
            "metric_name",
            "manifest_sha256",
            "selected_threshold",
            name="uq_calibration_reports_idempotency",
        ),
    )
    op.create_index(
        "ix_calibration_reports_workspace_run_created",
        "calibration_reports",
        ["workspace_id", "run_id", "created_at"],
    )
    op.create_index(
        "ix_calibration_reports_workspace_candidate_metric",
        "calibration_reports",
        ["workspace_id", "run_candidate_id", "metric_name"],
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

    report_count = bind.execute(sa.text("SELECT COUNT(*) FROM calibration_reports")).scalar_one()
    if int(report_count):
        raise RuntimeError(
            "calibration report downgrade refused: persisted calibration evidence exists; "
            "restore a pre-migration backup instead"
        )
    op.drop_index(
        "ix_calibration_reports_workspace_candidate_metric",
        table_name="calibration_reports",
    )
    op.drop_index(
        "ix_calibration_reports_workspace_run_created",
        table_name="calibration_reports",
    )
    op.drop_table("calibration_reports")
    op.drop_index(
        "ux_run_candidates_workspace_run_id",
        table_name="run_candidates",
    )
