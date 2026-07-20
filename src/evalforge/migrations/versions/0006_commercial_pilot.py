"""Add the minimal hosted-pilot commercial and activation contracts.

Revision ID: 0006_commercial_pilot
Revises: 0005_calibration_reports
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0006_commercial_pilot"
down_revision: str | None = "0005_calibration_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
LOCAL_USER_ID = "00000000-0000-4000-8000-000000000002"
LOCAL_MEMBERSHIP_ID = "00000000-0000-4000-8000-000000000003"
WORKSPACE_SCOPED_EVIDENCE_TABLES = (
    "datasets",
    "test_cases",
    "prompt_templates",
    "model_profiles",
    "evaluation_runs",
    "run_candidates",
    "evaluation_results",
)


def upgrade() -> None:
    op.create_table(
        "workspace_entitlements",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column(
            "plan_code",
            sa.Enum(
                "open_source",
                "hosted_trial",
                "team",
                name="workspace_plan_code",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "trialing",
                "active",
                "expired",
                "canceled",
                name="workspace_entitlement_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("seat_limit", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seat_limit >= 1", name="ck_workspace_entitlements_seat_limit"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_entitlements_workspace"),
    )
    op.create_index(
        "ix_workspace_entitlements_status_period",
        "workspace_entitlements",
        ["status", "current_period_end"],
    )

    op.create_table(
        "billing_events",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 = lower(payload_sha256)",
            name="ck_billing_events_payload_hash",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_billing_events_provider_event",
        ),
    )
    op.create_index(
        "ix_billing_events_workspace_created",
        "billing_events",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "team_pilot_requests",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("requested_seats", sa.Integer(), nullable=False),
        sa.Column(
            "evaluation_frequency",
            sa.Enum(
                "weekly",
                "several_times_week",
                "daily",
                "release_driven",
                name="team_pilot_evaluation_frequency",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("security_review_required", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "canceled",
                "qualified",
                "declined",
                name="team_pilot_request_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "requested_seats >= 2 AND requested_seats <= 250",
            name="ck_team_pilot_requests_requested_seats",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_team_pilot_requests_idempotency",
        ),
    )
    op.create_index(
        "ix_team_pilot_requests_workspace_status",
        "team_pilot_requests",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ux_team_pilot_requests_workspace_pending",
        "team_pilot_requests",
        ["workspace_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "activation_events",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "name",
            sa.Enum(
                "landing",
                "signup",
                "core_job_start",
                "evaluation_complete",
                "result_engagement",
                "second_use",
                "upgrade_view",
                "checkout_start",
                "entitlement_activation",
                "team_request_submitted",
                name="activation_event_name",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
            name="fk_activation_events_workspace_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "event_key",
            name="uq_activation_events_event_key",
        ),
    )
    op.create_index(
        "ix_activation_events_workspace_name_created",
        "activation_events",
        ["workspace_id", "name", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "workspace_entitlements",
        "billing_events",
        "team_pilot_requests",
        "activation_events",
    ):
        row_count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()  # noqa: S608  # nosec B608
        if int(row_count):
            raise RuntimeError(
                "hosted pilot downgrade refused: durable evaluation evidence exists; "
                "restore a pre-migration backup instead"
            )

    # Alembic can apply several downgrade functions in one command. Preflight the older
    # revisions' own refusal rules before dropping this revision, while still permitting a
    # safe one-step 0006 -> 0005 rollback when only older calibration/identity evidence exists.
    destination = context.get_revision_argument()
    if destination != "0005_calibration_reports":
        calibration_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM calibration_reports")
        ).scalar_one()
        if int(calibration_count):
            raise RuntimeError(
                "calibration report downgrade refused: persisted calibration evidence exists; "
                "restore a pre-migration backup instead"
            )
    if destination not in {"0005_calibration_reports", "0004_durable_execution_leases"}:
        for table_name in WORKSPACE_SCOPED_EVIDENCE_TABLES:
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
    if destination not in {
        "0005_calibration_reports",
        "0004_durable_execution_leases",
        "0003_identity_tenant_scope",
    }:
        identity_guards = (
            ("workspaces", "id", LOCAL_WORKSPACE_ID),
            ("users", "id", LOCAL_USER_ID),
            ("workspace_memberships", "id", LOCAL_MEMBERSHIP_ID),
        )
        for table_name, id_column, local_id in identity_guards:
            extra_count = bind.execute(
                sa.text(
                    f"SELECT COUNT(*) FROM {table_name} "  # noqa: S608  # nosec B608
                    f"WHERE {id_column} <> :local_id"  # nosec B608
                ),
                {"local_id": local_id},
            ).scalar_one()
            if int(extra_count):
                raise RuntimeError(
                    "identity scope downgrade refused: non-local identity data exists; "
                    "restore a pre-migration backup instead"
                )
        audit_count = bind.execute(sa.text("SELECT COUNT(*) FROM audit_events")).scalar_one()
        if int(audit_count):
            raise RuntimeError(
                "identity scope downgrade refused: audit evidence exists; "
                "restore a pre-migration backup instead"
            )

    op.drop_index(
        "ix_activation_events_workspace_name_created",
        table_name="activation_events",
    )
    op.drop_table("activation_events")
    op.drop_index(
        "ux_team_pilot_requests_workspace_pending",
        table_name="team_pilot_requests",
    )
    op.drop_index(
        "ix_team_pilot_requests_workspace_status",
        table_name="team_pilot_requests",
    )
    op.drop_table("team_pilot_requests")
    op.drop_index(
        "ix_billing_events_workspace_created",
        table_name="billing_events",
    )
    op.drop_table("billing_events")
    op.drop_index(
        "ix_workspace_entitlements_status_period",
        table_name="workspace_entitlements",
    )
    op.drop_table("workspace_entitlements")
