"""Add provisioned identities and database-enforced workspace scope.

Revision ID: 0003_identity_tenant_scope
Revises: 0002_preflight_context_cost_ack
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from alembic import op

revision: str = "0003_identity_tenant_scope"
down_revision: str | None = "0002_preflight_context_cost_ack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
LOCAL_USER_ID = "00000000-0000-4000-8000-000000000002"
LOCAL_MEMBERSHIP_ID = "00000000-0000-4000-8000-000000000003"

DOMAIN_TABLES = (
    "datasets",
    "test_cases",
    "prompt_templates",
    "model_profiles",
    "evaluation_runs",
    "run_candidates",
    "evaluation_results",
)


def _batch_recreate_mode() -> Literal["always", "auto"]:
    return "always" if op.get_bind().dialect.name == "sqlite" else "auto"


def _add_scope_column(table_name: str) -> None:
    op.add_column(table_name, sa.Column("workspace_id", sa.String(length=36), nullable=True))


def _backfill_scope(table_name: str) -> None:
    op.execute(
        sa.text(
            f"UPDATE {table_name} SET workspace_id = :workspace_id"  # noqa: S608  # nosec B608
        ).bindparams(workspace_id=LOCAL_WORKSPACE_ID)
    )


def _set_sqlite_foreign_keys(*, enabled: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    if enabled:
        # The Alembic environment disposes its private migration connection;
        # provided application connections are restored by apply_migrations().
        return
    # SQLite cannot recreate a table referenced by an external foreign key.
    # Its DDL is non-transactional under Alembic, so close the current unit,
    # suspend enforcement only on this migration connection, then validate all
    # rows before returning control for Alembic's revision update.
    driver_connection = bind.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("SQLite migration requires an active driver connection")
    driver_connection.commit()
    cursor = driver_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
    finally:
        cursor.close()


def _scope_table(
    table_name: str,
    *,
    old_indexes: tuple[str, ...],
    old_uniques: tuple[str, ...],
    new_indexes: tuple[tuple[str, tuple[str, ...]], ...],
    new_uniques: tuple[tuple[str, tuple[str, ...]], ...],
    composite_foreign_keys: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...], str], ...] = (),
) -> None:
    with op.batch_alter_table(table_name, recreate=_batch_recreate_mode()) as batch:
        for index_name in old_indexes:
            batch.drop_index(index_name)
        for constraint_name in old_uniques:
            batch.drop_constraint(constraint_name, type_="unique")
        batch.alter_column("workspace_id", existing_type=sa.String(length=36), nullable=False)
        batch.create_foreign_key(
            f"fk_{table_name}_workspace",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        for name, columns in new_uniques:
            batch.create_unique_constraint(name, list(columns))
        for name, local_columns, remote_table, remote_columns, ondelete in composite_foreign_keys:
            batch.create_foreign_key(
                name,
                remote_table,
                list(local_columns),
                list(remote_columns),
                ondelete=ondelete,
            )
        for name, columns in new_indexes:
            batch.create_index(name, list(columns), unique=False)


def upgrade() -> None:
    _set_sqlite_foreign_keys(enabled=False)
    status_values = sa.Enum(
        "active",
        "suspended",
        name="workspace_status",
        native_enum=False,
        create_constraint=True,
    )
    user_status_values = sa.Enum(
        "active",
        "suspended",
        name="user_status",
        native_enum=False,
        create_constraint=True,
    )
    membership_status_values = sa.Enum(
        "active",
        "suspended",
        name="membership_status",
        native_enum=False,
        create_constraint=True,
    )

    op.create_table(
        "workspaces",
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", status_values, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
    )
    op.create_index("ix_workspaces_status_name", "workspaces", ["status", "name"])
    op.create_table(
        "users",
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", user_status_values, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    op.create_index("ix_users_status_created", "users", ["status", "created_at"])
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", membership_status_values, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_memberships_workspace_user"),
    )
    op.create_index("ix_memberships_user_status", "workspace_memberships", ["user_id", "status"])
    op.create_table(
        "audit_events",
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_workspace_created", "audit_events", ["workspace_id", "created_at"]
    )
    op.create_index(
        "ix_audit_events_actor_created", "audit_events", ["actor_user_id", "created_at"]
    )

    now = datetime.now(UTC)
    workspaces = sa.table(
        "workspaces",
        sa.column("id", sa.String),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    users = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("issuer", sa.String),
        sa.column("subject", sa.String),
        sa.column("display_name", sa.String),
        sa.column("email", sa.String),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    memberships = sa.table(
        "workspace_memberships",
        sa.column("id", sa.String),
        sa.column("workspace_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("role", sa.String),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        workspaces,
        [
            {
                "id": LOCAL_WORKSPACE_ID,
                "slug": "local",
                "name": "Local workspace",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
        multiinsert=False,
    )
    op.bulk_insert(
        users,
        [
            {
                "id": LOCAL_USER_ID,
                "issuer": "urn:evalforge:local",
                "subject": "local-owner",
                "display_name": "Local owner",
                "email": None,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
        multiinsert=False,
    )
    op.bulk_insert(
        memberships,
        [
            {
                "id": LOCAL_MEMBERSHIP_ID,
                "workspace_id": LOCAL_WORKSPACE_ID,
                "user_id": LOCAL_USER_ID,
                "role": "owner",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
        multiinsert=False,
    )

    for table_name in DOMAIN_TABLES:
        _add_scope_column(table_name)
    op.add_column(
        "evaluation_runs",
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
    )
    for table_name in DOMAIN_TABLES:
        _backfill_scope(table_name)
    op.execute(
        sa.text(
            "UPDATE evaluation_runs SET requested_by_user_id = :user_id "
            "WHERE requested_by_user_id IS NULL"
        ).bindparams(user_id=LOCAL_USER_ID)
    )

    _scope_table(
        "datasets",
        old_indexes=("ix_datasets_created_at",),
        old_uniques=("uq_datasets_name_version",),
        new_indexes=(("ix_datasets_workspace_created", ("workspace_id", "created_at")),),
        new_uniques=(
            ("uq_datasets_workspace_id", ("workspace_id", "id")),
            (
                "uq_datasets_workspace_name_version",
                ("workspace_id", "name", "version"),
            ),
        ),
    )
    _scope_table(
        "prompt_templates",
        old_indexes=("ix_prompt_templates_created_at",),
        old_uniques=("uq_prompt_templates_name_version",),
        new_indexes=(("ix_prompt_templates_workspace_created", ("workspace_id", "created_at")),),
        new_uniques=(
            ("uq_prompt_templates_workspace_id", ("workspace_id", "id")),
            (
                "uq_prompt_templates_workspace_name_version",
                ("workspace_id", "name", "version"),
            ),
        ),
    )
    _scope_table(
        "model_profiles",
        old_indexes=("ix_model_profiles_provider_model",),
        old_uniques=("uq_model_profiles_name_version",),
        new_indexes=(
            (
                "ix_model_profiles_workspace_provider_model",
                ("workspace_id", "provider", "model_name"),
            ),
        ),
        new_uniques=(
            ("uq_model_profiles_workspace_id", ("workspace_id", "id")),
            (
                "uq_model_profiles_workspace_name_version",
                ("workspace_id", "name", "version"),
            ),
        ),
    )
    _scope_table(
        "test_cases",
        old_indexes=("ix_test_cases_dataset_id", "ix_test_cases_case_hash"),
        old_uniques=("uq_test_cases_dataset_external", "uq_test_cases_dataset_position"),
        new_indexes=(
            ("ix_test_cases_workspace_dataset", ("workspace_id", "dataset_id")),
            ("ix_test_cases_case_hash", ("case_hash",)),
        ),
        new_uniques=(
            ("uq_test_cases_workspace_id", ("workspace_id", "id")),
            (
                "uq_test_cases_workspace_dataset_external",
                ("workspace_id", "dataset_id", "external_id"),
            ),
            (
                "uq_test_cases_workspace_dataset_position",
                ("workspace_id", "dataset_id", "position"),
            ),
        ),
        composite_foreign_keys=(
            (
                "fk_test_cases_workspace_dataset",
                ("workspace_id", "dataset_id"),
                "datasets",
                ("workspace_id", "id"),
                "CASCADE",
            ),
        ),
    )
    _scope_table(
        "evaluation_runs",
        old_indexes=(
            "ix_evaluation_runs_status_created",
            "ix_evaluation_runs_dataset_id",
            "ix_evaluation_runs_request_hash",
        ),
        old_uniques=("uq_evaluation_runs_idempotency_key",),
        new_indexes=(
            (
                "ix_evaluation_runs_workspace_status_created",
                ("workspace_id", "status", "created_at"),
            ),
            ("ix_evaluation_runs_workspace_dataset", ("workspace_id", "dataset_id")),
            (
                "ix_evaluation_runs_workspace_request_hash",
                ("workspace_id", "request_hash"),
            ),
        ),
        new_uniques=(
            ("uq_evaluation_runs_workspace_id", ("workspace_id", "id")),
            (
                "uq_evaluation_runs_workspace_idempotency",
                ("workspace_id", "idempotency_key"),
            ),
        ),
        composite_foreign_keys=(
            (
                "fk_evaluation_runs_workspace_dataset",
                ("workspace_id", "dataset_id"),
                "datasets",
                ("workspace_id", "id"),
                "RESTRICT",
            ),
        ),
    )
    with op.batch_alter_table("evaluation_runs", recreate=_batch_recreate_mode()) as batch:
        batch.create_foreign_key(
            "fk_evaluation_runs_requested_by_user",
            "users",
            ["requested_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _scope_table(
        "run_candidates",
        old_indexes=("ix_run_candidates_run_status",),
        old_uniques=("uq_run_candidates_matrix", "uq_run_candidates_run_ordinal"),
        new_indexes=(
            (
                "ix_run_candidates_workspace_run_status",
                ("workspace_id", "run_id", "status"),
            ),
        ),
        new_uniques=(
            ("uq_run_candidates_workspace_id", ("workspace_id", "id")),
            (
                "uq_run_candidates_workspace_matrix",
                ("workspace_id", "run_id", "prompt_template_id", "model_profile_id"),
            ),
            (
                "uq_run_candidates_workspace_ordinal",
                ("workspace_id", "run_id", "ordinal"),
            ),
        ),
        composite_foreign_keys=(
            (
                "fk_run_candidates_workspace_run",
                ("workspace_id", "run_id"),
                "evaluation_runs",
                ("workspace_id", "id"),
                "CASCADE",
            ),
            (
                "fk_run_candidates_workspace_prompt",
                ("workspace_id", "prompt_template_id"),
                "prompt_templates",
                ("workspace_id", "id"),
                "RESTRICT",
            ),
            (
                "fk_run_candidates_workspace_model",
                ("workspace_id", "model_profile_id"),
                "model_profiles",
                ("workspace_id", "id"),
                "RESTRICT",
            ),
        ),
    )
    _scope_table(
        "evaluation_results",
        old_indexes=(
            "ix_evaluation_results_run_status",
            "ix_evaluation_results_candidate_id",
            "ix_evaluation_results_case_hash",
        ),
        old_uniques=("uq_results_candidate_case",),
        new_indexes=(
            ("ix_results_workspace_run_status", ("workspace_id", "run_id", "status")),
            (
                "ix_results_workspace_candidate",
                ("workspace_id", "run_candidate_id"),
            ),
            ("ix_results_workspace_case_hash", ("workspace_id", "case_hash")),
        ),
        new_uniques=(
            ("uq_results_workspace_id", ("workspace_id", "id")),
            (
                "uq_results_workspace_candidate_case",
                ("workspace_id", "run_candidate_id", "test_case_id"),
            ),
        ),
        composite_foreign_keys=(
            (
                "fk_results_workspace_run",
                ("workspace_id", "run_id"),
                "evaluation_runs",
                ("workspace_id", "id"),
                "CASCADE",
            ),
            (
                "fk_results_workspace_candidate",
                ("workspace_id", "run_candidate_id"),
                "run_candidates",
                ("workspace_id", "id"),
                "CASCADE",
            ),
            (
                "fk_results_workspace_case",
                ("workspace_id", "test_case_id"),
                "test_cases",
                ("workspace_id", "id"),
                "RESTRICT",
            ),
        ),
    )
    _set_sqlite_foreign_keys(enabled=True)
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("workspace migration produced invalid foreign-key relationships")


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

    _set_sqlite_foreign_keys(enabled=False)

    # Downgrades are intentionally backup-oriented. The exact 0002 global
    # uniqueness and index contract is safe only after the data-loss guards.
    table_changes = (
        (
            "evaluation_results",
            ("uq_results_workspace_candidate_case", "uq_results_workspace_id"),
            (
                "fk_results_workspace_run",
                "fk_results_workspace_candidate",
                "fk_results_workspace_case",
                "fk_evaluation_results_workspace",
            ),
            (
                "ix_results_workspace_run_status",
                "ix_results_workspace_candidate",
                "ix_results_workspace_case_hash",
            ),
            (("uq_results_candidate_case", ("run_candidate_id", "test_case_id")),),
            (
                ("ix_evaluation_results_candidate_id", ("run_candidate_id",)),
                ("ix_evaluation_results_case_hash", ("case_hash",)),
                ("ix_evaluation_results_run_status", ("run_id", "status")),
            ),
        ),
        (
            "run_candidates",
            (
                "uq_run_candidates_workspace_matrix",
                "uq_run_candidates_workspace_ordinal",
                "uq_run_candidates_workspace_id",
            ),
            (
                "fk_run_candidates_workspace_run",
                "fk_run_candidates_workspace_prompt",
                "fk_run_candidates_workspace_model",
                "fk_run_candidates_workspace",
            ),
            ("ix_run_candidates_workspace_run_status",),
            (
                (
                    "uq_run_candidates_matrix",
                    ("run_id", "prompt_template_id", "model_profile_id"),
                ),
                ("uq_run_candidates_run_ordinal", ("run_id", "ordinal")),
            ),
            (("ix_run_candidates_run_status", ("run_id", "status")),),
        ),
        (
            "evaluation_runs",
            (
                "uq_evaluation_runs_workspace_idempotency",
                "uq_evaluation_runs_workspace_id",
            ),
            (
                "fk_evaluation_runs_workspace_dataset",
                "fk_evaluation_runs_workspace",
                "fk_evaluation_runs_requested_by_user",
            ),
            (
                "ix_evaluation_runs_workspace_status_created",
                "ix_evaluation_runs_workspace_dataset",
                "ix_evaluation_runs_workspace_request_hash",
            ),
            (("uq_evaluation_runs_idempotency_key", ("idempotency_key",)),),
            (
                ("ix_evaluation_runs_dataset_id", ("dataset_id",)),
                ("ix_evaluation_runs_request_hash", ("request_hash",)),
                ("ix_evaluation_runs_status_created", ("status", "created_at")),
            ),
        ),
        (
            "test_cases",
            (
                "uq_test_cases_workspace_dataset_external",
                "uq_test_cases_workspace_dataset_position",
                "uq_test_cases_workspace_id",
            ),
            ("fk_test_cases_workspace_dataset", "fk_test_cases_workspace"),
            ("ix_test_cases_workspace_dataset", "ix_test_cases_case_hash"),
            (
                ("uq_test_cases_dataset_external", ("dataset_id", "external_id")),
                ("uq_test_cases_dataset_position", ("dataset_id", "position")),
            ),
            (
                ("ix_test_cases_case_hash", ("case_hash",)),
                ("ix_test_cases_dataset_id", ("dataset_id",)),
            ),
        ),
        (
            "model_profiles",
            ("uq_model_profiles_workspace_name_version", "uq_model_profiles_workspace_id"),
            ("fk_model_profiles_workspace",),
            ("ix_model_profiles_workspace_provider_model",),
            (("uq_model_profiles_name_version", ("name", "version")),),
            (("ix_model_profiles_provider_model", ("provider", "model_name")),),
        ),
        (
            "prompt_templates",
            (
                "uq_prompt_templates_workspace_name_version",
                "uq_prompt_templates_workspace_id",
            ),
            ("fk_prompt_templates_workspace",),
            ("ix_prompt_templates_workspace_created",),
            (("uq_prompt_templates_name_version", ("name", "version")),),
            (("ix_prompt_templates_created_at", ("created_at",)),),
        ),
        (
            "datasets",
            ("uq_datasets_workspace_name_version", "uq_datasets_workspace_id"),
            ("fk_datasets_workspace",),
            ("ix_datasets_workspace_created",),
            (("uq_datasets_name_version", ("name", "version")),),
            (("ix_datasets_created_at", ("created_at",)),),
        ),
    )
    for (
        table_name,
        unique_names,
        foreign_names,
        new_index_names,
        old_uniques,
        old_indexes,
    ) in table_changes:
        with op.batch_alter_table(table_name, recreate=_batch_recreate_mode()) as batch:
            for name in new_index_names:
                batch.drop_index(name)
            for name in unique_names:
                batch.drop_constraint(name, type_="unique")
            for name in foreign_names:
                batch.drop_constraint(name, type_="foreignkey")
            if table_name == "evaluation_runs":
                batch.drop_column("requested_by_user_id")
            batch.drop_column("workspace_id")
            for name, columns in old_uniques:
                batch.create_unique_constraint(name, list(columns))
            for name, columns in old_indexes:
                batch.create_index(name, list(columns), unique=False)

    op.drop_table("audit_events")
    op.drop_table("workspace_memberships")
    op.drop_table("users")
    op.drop_table("workspaces")
    if bind.dialect.name == "sqlite":
        violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("identity downgrade produced invalid foreign-key relationships")
    _set_sqlite_foreign_keys(enabled=True)
