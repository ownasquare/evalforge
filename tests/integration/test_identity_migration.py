from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.engine import Connection, Engine

from evalforge.database import check_database_readiness, create_database_engine
from evalforge.models import Dataset
from evalforge.security.permissions import (
    LOCAL_ISSUER,
    LOCAL_MEMBERSHIP_ID,
    LOCAL_SUBJECT,
    LOCAL_USER_ID,
    LOCAL_WORKSPACE_ID,
)

ROOT = Path(__file__).resolve().parents[2]

DATASET_ID = "10000000-0000-4000-8000-000000000001"
CASE_ID = "10000000-0000-4000-8000-000000000002"
PROMPT_ID = "10000000-0000-4000-8000-000000000003"
MODEL_ID = "10000000-0000-4000-8000-000000000004"
RUN_ID = "10000000-0000-4000-8000-000000000005"
CANDIDATE_ID = "10000000-0000-4000-8000-000000000006"
RESULT_ID = "10000000-0000-4000-8000-000000000007"

DOMAIN_IDS = {
    "datasets": DATASET_ID,
    "test_cases": CASE_ID,
    "prompt_templates": PROMPT_ID,
    "model_profiles": MODEL_ID,
    "evaluation_runs": RUN_ID,
    "run_candidates": CANDIDATE_ID,
    "evaluation_results": RESULT_ID,
}


def _configuration(database_url: str) -> Config:
    configuration = Config()
    configuration.set_main_option("script_location", str(ROOT / "src" / "evalforge" / "migrations"))
    configuration.attributes["database_url"] = database_url
    return configuration


def _legacy_rows(now: datetime) -> dict[str, dict[str, Any]]:
    case_snapshot = {
        "id": CASE_ID,
        "external_id": "legacy-case",
        "position": 0,
        "input": "What is the support PIN?",
        "context": "The support PIN is 3141.",
        "context_chunks": ["The support PIN is 3141."],
        "expected_output": "3141",
        "required_phrases": ["3141"],
        "constraints": {"max_words": 4},
        "tags": ["legacy", "support"],
        "metadata": {"source": "migration-fixture"},
        "case_hash": "c" * 64,
    }
    prompt_snapshot = {
        "id": PROMPT_ID,
        "name": "Legacy prompt",
        "version": 3,
        "system_template": "Answer from the supplied context.",
        "user_template": "{input}\n{context}",
        "variables": ["input", "context"],
        "template_hash": "p" * 64,
    }
    model_snapshot = {
        "id": MODEL_ID,
        "name": "Legacy deterministic model",
        "version": 2,
        "provider": "deterministic",
        "model_name": "balanced",
        "api_mode": "deterministic",
        "generation_parameters": {"temperature": 0.0, "max_output_tokens": 32},
        "input_price_micro_usd_per_million_tokens": 0,
        "output_price_micro_usd_per_million_tokens": 0,
        "pricing_source": "legacy deterministic fixture",
        "profile_hash": "m" * 64,
    }
    dataset_snapshot = {
        "id": DATASET_ID,
        "name": "Legacy benchmark",
        "version": 4,
        "description": "Populated before identity scope existed.",
        "content_hash": "d" * 64,
        "metadata": {"retention": "migration-proof"},
        "cases": [case_snapshot],
    }
    metric_snapshot = {
        "metrics": [
            {
                "name": "correctness",
                "version": "lexical-correctness-v1",
                "direction": "higher_is_better",
                "weight": 1.0,
                "threshold": 0.7,
                "enabled": True,
            }
        ],
        "versions": {"correctness": "lexical-correctness-v1"},
        "directions": {"correctness": "higher_is_better"},
        "configuration_hash": "f" * 64,
    }
    metric_result = {
        "name": "correctness",
        "version": "lexical-correctness-v1",
        "direction": "higher_is_better",
        "applicability": "applicable",
        "score": 1.0,
        "threshold": 0.7,
        "passed": True,
        "reason": "Legacy answer matched the reference.",
        "evidence": {"exact_match": True},
    }
    return {
        "datasets": {
            "id": DATASET_ID,
            "name": "Legacy benchmark",
            "description": "Populated before identity scope existed.",
            "version": 4,
            "content_hash": "d" * 64,
            "metadata": {"retention": "migration-proof"},
            "created_at": now,
            "updated_at": now,
        },
        "test_cases": {
            "id": CASE_ID,
            "dataset_id": DATASET_ID,
            "external_id": "legacy-case",
            "position": 0,
            "input_text": "What is the support PIN?",
            "context_text": "The support PIN is 3141.",
            "context_chunks": ["The support PIN is 3141."],
            "expected_output": "3141",
            "required_phrases": ["3141"],
            "constraints": {"max_words": 4},
            "tags": ["legacy", "support"],
            "metadata": {"source": "migration-fixture"},
            "case_hash": "c" * 64,
            "created_at": now,
            "updated_at": now,
        },
        "prompt_templates": {
            "id": PROMPT_ID,
            "name": "Legacy prompt",
            "description": "Existing versioned prompt",
            "version": 3,
            "system_template": "Answer from the supplied context.",
            "user_template": "{input}\n{context}",
            "variables": ["input", "context"],
            "template_hash": "p" * 64,
            "metadata": {"owner": "legacy-team"},
            "created_at": now,
            "updated_at": now,
        },
        "model_profiles": {
            "id": MODEL_ID,
            "name": "Legacy deterministic model",
            "description": "Existing model profile",
            "version": 2,
            "provider": "deterministic",
            "model_name": "balanced",
            "api_mode": "deterministic",
            "generation_parameters": {"temperature": 0.0, "max_output_tokens": 32},
            "input_price_micro_usd_per_million_tokens": 0,
            "output_price_micro_usd_per_million_tokens": 0,
            "pricing_source": "legacy deterministic fixture",
            "profile_hash": "m" * 64,
            "enabled": True,
            "metadata": {"synthetic": True},
            "created_at": now,
            "updated_at": now,
        },
        "evaluation_runs": {
            "id": RUN_ID,
            "name": "Legacy completed run",
            "dataset_id": DATASET_ID,
            "dataset_snapshot": dataset_snapshot,
            "dataset_hash": "d" * 64,
            "metric_configuration_snapshot": metric_snapshot,
            "preflight_snapshot": {
                "case_count": 1,
                "variant_count": 1,
                "provider_call_count": 1,
            },
            "application_version": "0.1.0-legacy",
            "executor_type": "persistent_local_worker",
            "requested_by": "Legacy operator display name",
            "idempotency_key": "legacy-run-request",
            "request_hash": "r" * 64,
            "acknowledge_real_cost": False,
            "acknowledge_unknown_cost": False,
            "status": "completed",
            "status_reason": "Legacy matrix completed",
            "state_version": 2,
            "total_items": 1,
            "completed_items": 1,
            "succeeded_items": 1,
            "failed_items": 0,
            "error_type": None,
            "error_message": None,
            "queued_at": now,
            "started_at": now,
            "heartbeat_at": now,
            "cancel_requested_at": None,
            "finished_at": now,
            "created_at": now,
            "updated_at": now,
        },
        "run_candidates": {
            "id": CANDIDATE_ID,
            "run_id": RUN_ID,
            "prompt_template_id": PROMPT_ID,
            "model_profile_id": MODEL_ID,
            "ordinal": 0,
            "label": "Legacy prompt v3 / Legacy deterministic model v2",
            "prompt_snapshot": prompt_snapshot,
            "prompt_hash": "p" * 64,
            "model_snapshot": model_snapshot,
            "model_hash": "m" * 64,
            "generation_parameters_snapshot": {
                "temperature": 0.0,
                "max_output_tokens": 32,
            },
            "candidate_hash": "a" * 64,
            "status": "completed",
            "status_reason": "Legacy candidate completed",
            "state_version": 2,
            "total_items": 1,
            "completed_items": 1,
            "failed_items": 0,
            "error_type": None,
            "error_message": None,
            "started_at": now,
            "heartbeat_at": now,
            "finished_at": now,
            "created_at": now,
            "updated_at": now,
        },
        "evaluation_results": {
            "id": RESULT_ID,
            "run_id": RUN_ID,
            "run_candidate_id": CANDIDATE_ID,
            "test_case_id": CASE_ID,
            "input_snapshot": case_snapshot,
            "case_hash": "c" * 64,
            "prompt_snapshot": prompt_snapshot,
            "prompt_hash": "p" * 64,
            "model_snapshot": model_snapshot,
            "model_hash": "m" * 64,
            "generation_parameters_snapshot": {
                "temperature": 0.0,
                "max_output_tokens": 32,
            },
            "rendered_system_prompt": "Answer from the supplied context.",
            "rendered_user_prompt": ("What is the support PIN?\nThe support PIN is 3141."),
            "output_text": "3141",
            "metric_versions": {"correctness": "lexical-correctness-v1"},
            "metric_directions": {"correctness": "higher_is_better"},
            "metric_applicability": {"correctness": "applicable"},
            "metric_results": {
                "correctness": metric_result,
                "aggregate_quality": {
                    **metric_result,
                    "name": "aggregate_quality",
                    "version": "direction-aware-weighted-v1",
                },
            },
            "aggregate_score": 1.0,
            "aggregate_passed": True,
            "effective_metric_weight": 1.0,
            "provider": "deterministic-demo",
            "model_name": "balanced",
            "api_mode": "deterministic",
            "request_id": "demo_legacy_request",
            "finish_reason": "stop",
            "retry_count": 0,
            "latency_ms": 7,
            "input_tokens": 12,
            "output_tokens": 1,
            "total_tokens": 13,
            "estimated_cost_micro_usd": 0,
            "cost_source": "synthetic",
            "provider_metadata": {"synthetic": True, "usage_reported": True},
            "status": "completed",
            "status_reason": "Legacy generation and scoring completed",
            "state_version": 3,
            "error_type": None,
            "error_message": None,
            "error_retryable": None,
            "queued_at": now,
            "started_at": now,
            "finished_at": now,
            "created_at": now,
            "updated_at": now,
        },
    }


def _insert_complete_legacy_matrix(
    engine: Engine, rows: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    with engine.begin() as connection:
        for table_name in DOMAIN_IDS:
            connection.execute(metadata.tables[table_name].insert(), rows[table_name])
        return {
            table_name: _row(connection, metadata, table_name, resource_id)
            for table_name, resource_id in DOMAIN_IDS.items()
        }


def _row(
    connection: Connection,
    metadata: MetaData,
    table_name: str,
    resource_id: str,
) -> dict[str, Any]:
    table = metadata.tables[table_name]
    return dict(connection.execute(select(table).where(table.c.id == resource_id)).mappings().one())


def _assert_legacy_values_preserved(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> None:
    for table_name, before_row in before.items():
        after_row = after[table_name]
        for column_name, expected in before_row.items():
            assert after_row[column_name] == expected, f"{table_name}.{column_name} changed"


def _schema_key_contract(engine: Engine) -> dict[str, dict[str, set[tuple[str, tuple[str, ...]]]]]:
    inspector = inspect(engine)
    return {
        table_name: {
            "unique": {
                (str(item["name"]), tuple(item["column_names"]))
                for item in inspector.get_unique_constraints(table_name)
            },
            "index": {
                (str(item["name"]), tuple(item["column_names"]))
                for item in inspector.get_indexes(table_name)
            },
        }
        for table_name in DOMAIN_IDS
    }


@pytest.mark.integration
def test_populated_0002_identity_migration_preserves_evidence_and_scopes_every_row(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'populated-identity-0002.db'}"
    configuration = _configuration(database_url)
    command.upgrade(configuration, "0002_preflight_context_cost_ack")
    engine = create_database_engine(database_url)
    timestamp = datetime(2026, 7, 18, 16, 30, 45, 123456, tzinfo=UTC)
    try:
        legacy_rows = _legacy_rows(timestamp)
        before = _insert_complete_legacy_matrix(engine, legacy_rows)
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    migrated_engine = create_database_engine(database_url)
    try:
        metadata = MetaData()
        metadata.reflect(bind=migrated_engine)
        with migrated_engine.connect() as connection:
            after = {
                table_name: _row(connection, metadata, table_name, resource_id)
                for table_name, resource_id in DOMAIN_IDS.items()
            }
            _assert_legacy_values_preserved(before, after)

            assert {row["workspace_id"] for row in after.values()} == {LOCAL_WORKSPACE_ID}
            run = after["evaluation_runs"]
            assert run["requested_by"] == "Legacy operator display name"
            assert run["requested_by_user_id"] == LOCAL_USER_ID

            workspace = (
                connection.execute(
                    select(metadata.tables["workspaces"]).where(
                        metadata.tables["workspaces"].c.id == LOCAL_WORKSPACE_ID
                    )
                )
                .mappings()
                .one()
            )
            user = (
                connection.execute(
                    select(metadata.tables["users"]).where(
                        metadata.tables["users"].c.id == LOCAL_USER_ID
                    )
                )
                .mappings()
                .one()
            )
            membership = (
                connection.execute(
                    select(metadata.tables["workspace_memberships"]).where(
                        metadata.tables["workspace_memberships"].c.id == LOCAL_MEMBERSHIP_ID
                    )
                )
                .mappings()
                .one()
            )
            assert workspace["slug"] == "local"
            assert workspace["name"] == "Local workspace"
            assert workspace["status"] == "active"
            assert workspace["created_at"] == workspace["updated_at"]
            assert user["issuer"] == LOCAL_ISSUER
            assert user["subject"] == LOCAL_SUBJECT
            assert user["display_name"] == "Local owner"
            assert user["status"] == "active"
            assert user["created_at"] == user["updated_at"] == workspace["created_at"]
            assert membership["workspace_id"] == LOCAL_WORKSPACE_ID
            assert membership["user_id"] == LOCAL_USER_ID
            assert membership["role"] == "owner"
            assert membership["status"] == "active"
            assert membership["created_at"] == membership["updated_at"] == workspace["created_at"]

            migration_context = MigrationContext.configure(connection, opts={"compare_type": True})
            schema_differences = compare_metadata(migration_context, Dataset.metadata)
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()

        assert revision == "0004_durable_execution_leases"
        assert schema_differences == []
        assert check_database_readiness(migrated_engine) is True

        non_local_workspace_id = "20000000-0000-4000-8000-000000000001"
        with migrated_engine.begin() as connection:
            connection.execute(
                metadata.tables["workspaces"].insert(),
                {
                    "id": non_local_workspace_id,
                    "slug": "downgrade-guard",
                    "name": "Downgrade guard",
                    "status": "active",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            foreign_dataset = dict(legacy_rows["datasets"])
            foreign_dataset.update(
                {
                    "id": "20000000-0000-4000-8000-000000000002",
                    "workspace_id": non_local_workspace_id,
                    "name": "Non-local downgrade guard",
                    "content_hash": "x" * 64,
                }
            )
            connection.execute(metadata.tables["datasets"].insert(), foreign_dataset)
    finally:
        migrated_engine.dispose()

    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(configuration, "0002_preflight_context_cost_ack")

    verification_engine = create_database_engine(database_url)
    try:
        assert check_database_readiness(verification_engine) is True
        with verification_engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004_durable_execution_leases"
            )
    finally:
        verification_engine.dispose()


@pytest.mark.integration
def test_populated_identity_round_trip_restores_exact_0002_schema_and_rows(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'identity-round-trip.db'}"
    configuration = _configuration(database_url)
    command.upgrade(configuration, "0002_preflight_context_cost_ack")
    timestamp = datetime(2026, 7, 18, 18, 0, tzinfo=UTC)
    engine = create_database_engine(database_url)
    try:
        legacy_rows = _legacy_rows(timestamp)
        before_rows = _insert_complete_legacy_matrix(engine, legacy_rows)
        before_schema = _schema_key_contract(engine)
    finally:
        engine.dispose()

    command.upgrade(configuration, "head")
    command.downgrade(configuration, "0002_preflight_context_cost_ack")

    downgraded = create_database_engine(database_url)
    try:
        metadata = MetaData()
        metadata.reflect(bind=downgraded)
        with downgraded.connect() as connection:
            after_rows = {
                table_name: _row(connection, metadata, table_name, resource_id)
                for table_name, resource_id in DOMAIN_IDS.items()
            }
            assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0002_preflight_context_cost_ack"
            )
        _assert_legacy_values_preserved(before_rows, after_rows)
        assert _schema_key_contract(downgraded) == before_schema
        assert not {
            "workspaces",
            "users",
            "workspace_memberships",
            "audit_events",
            "execution_attempts",
        }.intersection(inspect(downgraded).get_table_names())
        for table_name in DOMAIN_IDS:
            assert "workspace_id" not in {
                column["name"] for column in inspect(downgraded).get_columns(table_name)
            }
    finally:
        downgraded.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("guard_kind", ["identity", "audit"])
def test_identity_downgrade_refuses_identity_or_audit_only_evidence(
    tmp_path: Path,
    guard_kind: str,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / f'identity-{guard_kind}-guard.db'}"
    configuration = _configuration(database_url)
    command.upgrade(configuration, "head")
    engine = create_database_engine(database_url)
    timestamp = datetime(2026, 7, 18, 18, 15, tzinfo=UTC)
    try:
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with engine.begin() as connection:
            if guard_kind == "identity":
                connection.execute(
                    metadata.tables["workspaces"].insert(),
                    {
                        "id": "30000000-0000-4000-8000-000000000001",
                        "slug": "identity-only-guard",
                        "name": "Identity only guard",
                        "status": "active",
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                )
            else:
                connection.execute(
                    metadata.tables["audit_events"].insert(),
                    {
                        "id": "30000000-0000-4000-8000-000000000002",
                        "workspace_id": LOCAL_WORKSPACE_ID,
                        "actor_user_id": LOCAL_USER_ID,
                        "action": "guard.test",
                        "resource_type": "workspace",
                        "resource_id": LOCAL_WORKSPACE_ID,
                        "outcome": "succeeded",
                        "request_id": None,
                        "metadata": {},
                        "created_at": timestamp,
                    },
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(configuration, "0002_preflight_context_cost_ack")
