"""Create the initial immutable evaluation schema from packaged migrations.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_datasets_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_datasets_name_version"),
    )
    op.create_index("ix_datasets_created_at", "datasets", ["created_at"], unique=False)

    op.create_table(
        "prompt_templates",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_template", sa.Text(), nullable=False),
        sa.Column("user_template", sa.Text(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("template_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_prompt_templates_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_prompt_templates_name_version"),
    )
    op.create_index(
        "ix_prompt_templates_created_at", "prompt_templates", ["created_at"], unique=False
    )

    op.create_table(
        "model_profiles",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column(
            "api_mode",
            sa.Enum(
                "deterministic",
                "responses",
                "chat_completions",
                name="api_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("generation_parameters", sa.JSON(), nullable=False),
        sa.Column("input_price_micro_usd_per_million_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_price_micro_usd_per_million_tokens", sa.BigInteger(), nullable=True),
        sa.Column("pricing_source", sa.String(length=200), nullable=True),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_model_profiles_version_positive"),
        sa.CheckConstraint(
            "input_price_micro_usd_per_million_tokens IS NULL OR "
            "input_price_micro_usd_per_million_tokens >= 0",
            name="ck_model_profiles_input_price_nonnegative",
        ),
        sa.CheckConstraint(
            "output_price_micro_usd_per_million_tokens IS NULL OR "
            "output_price_micro_usd_per_million_tokens >= 0",
            name="ck_model_profiles_output_price_nonnegative",
        ),
        sa.CheckConstraint(
            "(input_price_micro_usd_per_million_tokens IS NULL AND "
            "output_price_micro_usd_per_million_tokens IS NULL AND pricing_source IS NULL) "
            "OR pricing_source IS NOT NULL",
            name="ck_model_profiles_pricing_source_present",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_model_profiles_name_version"),
    )
    op.create_index(
        "ix_model_profiles_provider_model",
        "model_profiles",
        ["provider", "model_name"],
        unique=False,
    )

    op.create_table(
        "test_cases",
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=True),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("required_phrases", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("case_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_test_cases_position_nonnegative"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "external_id", name="uq_test_cases_dataset_external"),
        sa.UniqueConstraint("dataset_id", "position", name="uq_test_cases_dataset_position"),
    )
    op.create_index("ix_test_cases_case_hash", "test_cases", ["case_hash"], unique=False)
    op.create_index("ix_test_cases_dataset_id", "test_cases", ["dataset_id"], unique=False)

    op.create_table(
        "evaluation_runs",
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_snapshot", sa.JSON(), nullable=False),
        sa.Column("dataset_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("application_version", sa.String(length=64), nullable=False),
        sa.Column("executor_type", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("acknowledge_real_cost", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "cancel_requested",
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
                "interrupted",
                name="run_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("succeeded_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total_items >= 0", name="ck_evaluation_runs_total_nonnegative"),
        sa.CheckConstraint("completed_items >= 0", name="ck_evaluation_runs_completed_nonnegative"),
        sa.CheckConstraint("succeeded_items >= 0", name="ck_evaluation_runs_succeeded_nonnegative"),
        sa.CheckConstraint("failed_items >= 0", name="ck_evaluation_runs_failed_nonnegative"),
        sa.CheckConstraint(
            "completed_items <= total_items", name="ck_evaluation_runs_completed_within_total"
        ),
        sa.CheckConstraint(
            "succeeded_items + failed_items <= completed_items",
            name="ck_evaluation_runs_outcomes_within_completed",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_evaluation_runs_idempotency_key"),
    )
    op.create_index(
        "ix_evaluation_runs_dataset_id", "evaluation_runs", ["dataset_id"], unique=False
    )
    op.create_index(
        "ix_evaluation_runs_request_hash", "evaluation_runs", ["request_hash"], unique=False
    )
    op.create_index(
        "ix_evaluation_runs_status_created",
        "evaluation_runs",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "run_candidates",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("prompt_template_id", sa.String(length=36), nullable=False),
        sa.Column("model_profile_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_hash", sa.String(length=64), nullable=False),
        sa.Column("generation_parameters_snapshot", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "cancel_requested",
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
                "interrupted",
                name="candidate_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_run_candidates_ordinal_nonnegative"),
        sa.CheckConstraint("total_items >= 0", name="ck_run_candidates_total_nonnegative"),
        sa.CheckConstraint("completed_items >= 0", name="ck_run_candidates_completed_nonnegative"),
        sa.CheckConstraint("failed_items >= 0", name="ck_run_candidates_failed_nonnegative"),
        sa.CheckConstraint(
            "completed_items <= total_items", name="ck_run_candidates_completed_within_total"
        ),
        sa.ForeignKeyConstraint(["model_profile_id"], ["model_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["prompt_template_id"], ["prompt_templates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "prompt_template_id",
            "model_profile_id",
            name="uq_run_candidates_matrix",
        ),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_run_candidates_run_ordinal"),
    )
    op.create_index(
        "ix_run_candidates_run_status", "run_candidates", ["run_id", "status"], unique=False
    )

    op.create_table(
        "evaluation_results",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("run_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("test_case_id", sa.String(length=36), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("case_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_hash", sa.String(length=64), nullable=False),
        sa.Column("generation_parameters_snapshot", sa.JSON(), nullable=False),
        sa.Column("rendered_system_prompt", sa.Text(), nullable=False),
        sa.Column("rendered_user_prompt", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("metric_versions", sa.JSON(), nullable=False),
        sa.Column("metric_directions", sa.JSON(), nullable=False),
        sa.Column("metric_applicability", sa.JSON(), nullable=False),
        sa.Column("metric_results", sa.JSON(), nullable=False),
        sa.Column("aggregate_score", sa.Float(), nullable=True),
        sa.Column("aggregate_passed", sa.Boolean(), nullable=True),
        sa.Column("effective_metric_weight", sa.Float(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "api_mode",
            sa.Enum(
                "deterministic",
                "responses",
                "chat_completions",
                name="result_api_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("finish_reason", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("cost_source", sa.String(length=200), nullable=True),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "completed",
                "error",
                "cancelled",
                "interrupted",
                name="result_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("retry_count >= 0", name="ck_results_retry_count_nonnegative"),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ck_results_latency_nonnegative"
        ),
        sa.CheckConstraint("input_tokens >= 0", name="ck_results_input_tokens_nonnegative"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_results_output_tokens_nonnegative"),
        sa.CheckConstraint("total_tokens >= 0", name="ck_results_total_tokens_nonnegative"),
        sa.CheckConstraint(
            "estimated_cost_micro_usd IS NULL OR estimated_cost_micro_usd >= 0",
            name="ck_results_cost_micro_usd_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost_micro_usd IS NULL OR cost_source IS NOT NULL",
            name="ck_results_cost_source_consistent",
        ),
        sa.CheckConstraint(
            "aggregate_score IS NULL OR (aggregate_score >= 0 AND aggregate_score <= 1)",
            name="ck_results_aggregate_score_unit_interval",
        ),
        sa.CheckConstraint(
            "effective_metric_weight >= 0", name="ck_results_effective_weight_nonnegative"
        ),
        sa.ForeignKeyConstraint(["run_candidate_id"], ["run_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_candidate_id", "test_case_id", name="uq_results_candidate_case"),
    )
    op.create_index(
        "ix_evaluation_results_candidate_id",
        "evaluation_results",
        ["run_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_results_case_hash", "evaluation_results", ["case_hash"], unique=False
    )
    op.create_index(
        "ix_evaluation_results_run_status",
        "evaluation_results",
        ["run_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("evaluation_results")
    op.drop_table("run_candidates")
    op.drop_table("evaluation_runs")
    op.drop_table("test_cases")
    op.drop_table("model_profiles")
    op.drop_table("prompt_templates")
    op.drop_table("datasets")
