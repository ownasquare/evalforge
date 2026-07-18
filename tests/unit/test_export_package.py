from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

import pytest

from evalforge.exports import DisclosureProfile, build_export_package, disclose_run_evidence


def _run_evidence() -> dict[str, object]:
    return {
        "id": "run-1",
        "name": "Support benchmark",
        "output_text": "Reset the account password.",
        "input_snapshot": {
            "input": "How do I reset my password?",
            "expected_output": "Use the reset link.",
            "case_hash": "c" * 64,
        },
        "metric_results": {
            "correctness": {
                "score": 0.8,
                "evidence": {"matched_text": "reset password"},
            }
        },
    }


def _realistic_run_evidence(sentinel: str) -> dict[str, object]:
    timestamp = "2026-07-18T12:00:00Z"
    workspace_id = "00000000-0000-4000-8000-000000000001"
    run_id = "00000000-0000-4000-8000-000000000002"
    dataset_id = "00000000-0000-4000-8000-000000000003"
    case_id = "00000000-0000-4000-8000-000000000004"
    candidate_id = "00000000-0000-4000-8000-000000000005"
    prompt_id = "00000000-0000-4000-8000-000000000006"
    model_id = "00000000-0000-4000-8000-000000000007"
    result_id = "00000000-0000-4000-8000-000000000008"
    case_snapshot = {
        "id": case_id,
        "external_id": f"external-{sentinel}",
        "position": 0,
        "input": f"input {sentinel}",
        "context": f"context {sentinel}",
        "context_chunks": [f"chunk {sentinel}"],
        "expected_output": f"reference {sentinel}",
        "required_phrases": [f"phrase {sentinel}"],
        "constraints": {"description": sentinel},
        "tags": [sentinel],
        "metadata": {"private_note": sentinel},
        "case_hash": "c" * 64,
        "future_content_field": sentinel,
    }
    prompt_snapshot = {
        "name": sentinel,
        "version": "v1",
        "system_template": f"system {sentinel}",
        "user_template": f"user {sentinel}",
        "variables": [sentinel],
        "description": sentinel,
    }
    model_snapshot = {
        "name": sentinel,
        "version": "v1",
        "provider": sentinel,
        "model_name": sentinel,
        "api_mode": "responses",
        "generation_parameters": {"stop": [sentinel]},
        "pricing_source": sentinel,
    }
    return {
        "id": run_id,
        "workspace_id": workspace_id,
        "name": f"private run {sentinel}",
        "dataset_id": dataset_id,
        "dataset_snapshot": {
            "id": dataset_id,
            "name": sentinel,
            "version": "v1",
            "description": sentinel,
            "content_hash": "d" * 64,
            "metadata": {"owner": sentinel},
            "cases": [case_snapshot],
        },
        "dataset_hash": "d" * 64,
        "metric_configuration_snapshot": {"correctness": {"constraints": sentinel}},
        "preflight_snapshot": {
            "case_count": 1,
            "prompt_count": 1,
            "model_count": 1,
            "variant_count": 1,
            "provider_call_count": 1,
            "max_requested_output_tokens": 200,
            "estimated_input_tokens": 30,
            "input_token_estimate_method": sentinel,
            "estimated_known_cost_micro_usd": 17,
            "cost_estimate_complete": True,
            "real_provider": True,
            "real_provider_models": [sentinel],
            "unknown_pricing_models": [sentinel],
            "external_data_transfer_acknowledged": True,
            "spend_limit_micro_usd": 100,
            "spend_limit_basis": sentinel,
            "limits": {sentinel: 1},
        },
        "application_version": "0.1.0",
        "executor_type": "database_worker",
        "requested_by": sentinel,
        "idempotency_key": sentinel,
        "request_hash": "a" * 64,
        "acknowledge_real_cost": True,
        "acknowledge_unknown_cost": True,
        "status": "completed",
        "status_reason": sentinel,
        "state_version": 2,
        "total_items": 1,
        "completed_items": 1,
        "succeeded_items": 1,
        "failed_items": 0,
        "error_type": sentinel,
        "error_message": sentinel,
        "queued_at": timestamp,
        "started_at": timestamp,
        "heartbeat_at": timestamp,
        "finished_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
        "candidates": [
            {
                "id": candidate_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "prompt_template_id": prompt_id,
                "model_profile_id": model_id,
                "ordinal": 0,
                "label": sentinel,
                "prompt_snapshot": prompt_snapshot,
                "prompt_hash": "b" * 64,
                "model_snapshot": model_snapshot,
                "model_hash": "e" * 64,
                "generation_parameters_snapshot": {"stop": sentinel},
                "candidate_hash": "f" * 64,
                "status": "completed",
                "status_reason": sentinel,
                "state_version": 2,
                "total_items": 1,
                "completed_items": 1,
                "failed_items": 0,
                "error_type": sentinel,
                "error_message": sentinel,
                "started_at": timestamp,
                "heartbeat_at": timestamp,
                "finished_at": timestamp,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        ],
        "results": [
            {
                "id": result_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "run_candidate_id": candidate_id,
                "test_case_id": case_id,
                "input_snapshot": case_snapshot,
                "case_hash": "c" * 64,
                "prompt_snapshot": prompt_snapshot,
                "prompt_hash": "b" * 64,
                "model_snapshot": model_snapshot,
                "model_hash": "e" * 64,
                "generation_parameters_snapshot": {"stop": sentinel},
                "rendered_system_prompt": sentinel,
                "rendered_user_prompt": sentinel,
                "output_text": sentinel,
                "metric_versions": {"correctness": "lexical-correctness-v1"},
                "metric_directions": {"correctness": "higher_is_better"},
                "metric_applicability": {"correctness": "applicable"},
                "metric_results": {
                    "correctness": {
                        "name": "correctness",
                        "version": "lexical-correctness-v1",
                        "score": 0.8,
                        "threshold": 0.7,
                        "passed": True,
                        "status": "applicable",
                        "direction": "higher_is_better",
                        "reason": sentinel,
                        "evidence": {"matched_text": sentinel},
                    }
                },
                "aggregate_score": 0.8,
                "aggregate_passed": True,
                "effective_metric_weight": 1.0,
                "provider": sentinel,
                "model_name": sentinel,
                "api_mode": "responses",
                "request_id": sentinel,
                "finish_reason": sentinel,
                "retry_count": 0,
                "latency_ms": 25,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "estimated_cost_micro_usd": 17,
                "cost_source": "reported_usage",
                "provider_metadata": {"trace": sentinel},
                "status": "completed",
                "status_reason": sentinel,
                "state_version": 2,
                "error_type": sentinel,
                "error_message": sentinel,
                "error_retryable": False,
                "queued_at": timestamp,
                "started_at": timestamp,
                "finished_at": timestamp,
                "created_at": timestamp,
                "updated_at": timestamp,
                "future_provider_payload": {"raw": sentinel},
            }
        ],
        "future_run_content": {"notes": sentinel},
    }


def test_export_payload_bytes_and_hash_are_canonical_and_exclude_generation_time() -> None:
    first = build_export_package(
        _run_evidence(),
        application_version="0.1.0",
        metric_versions={"relevance": "v2", "correctness": "v1"},
        disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
        generated_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )
    reordered = dict(reversed(list(_run_evidence().items())))
    second = build_export_package(
        reordered,
        application_version="0.1.0",
        metric_versions={"correctness": "v1", "relevance": "v2"},
        disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
        generated_at=datetime(2026, 7, 18, 12, 5, tzinfo=UTC),
    )

    assert first.schema_version == "evalforge.run-export.v1"
    assert first.payload_bytes == second.payload_bytes
    assert first.payload_sha256 == second.payload_sha256
    assert first.payload_sha256 == hashlib.sha256(first.payload_bytes).hexdigest()
    assert first.envelope["generated_at"] != second.envelope["generated_at"]
    assert first.envelope["payload_sha256"] == first.payload_sha256
    assert b"generated_at" not in first.payload_bytes


def test_content_redacted_profile_removes_known_user_content_but_keeps_scores_and_hashes() -> None:
    sentinel = "SENTINEL_PRIVATE_BENCHMARK_CONTENT"
    package = build_export_package(
        _realistic_run_evidence(sentinel),
        application_version="0.1.0",
        metric_versions={"correctness": "lexical-correctness-v1"},
        disclosure_profile=DisclosureProfile.CONTENT_REDACTED,
        generated_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )
    payload = package.payload
    run = payload["run"]
    result = run["results"][0]

    assert payload["disclosure_profile"] == "content_redacted"
    assert sentinel not in json.dumps(payload)
    assert run["id"] == "00000000-0000-4000-8000-000000000002"
    assert "name" not in run
    assert "requested_by" not in run
    assert "idempotency_key" not in run
    assert run["dataset_snapshot"]["cases"][0]["input"] == "[redacted]"
    assert "external_id" not in run["dataset_snapshot"]["cases"][0]
    assert "label" not in run["candidates"][0]
    assert "prompt_snapshot" not in run["candidates"][0]
    assert result["output_text"] == "[redacted]"
    assert result["input_snapshot"]["expected_output"] == "[redacted]"
    assert result["input_snapshot"]["case_hash"] == "c" * 64
    assert result["metric_results"]["correctness"]["score"] == 0.8
    assert result["metric_results"]["correctness"]["evidence"] == "[redacted]"
    assert "reason" not in result["metric_results"]["correctness"]
    assert "provider" not in result
    assert "provider_metadata" not in result
    assert result["total_tokens"] == 15
    assert result["estimated_cost_micro_usd"] == 17


def test_disclosed_run_evidence_applies_profiles_and_detaches_input() -> None:
    sentinel = "SENTINEL_PRIVATE_DIRECT_EXPORT_CONTENT"
    evidence = _realistic_run_evidence(sentinel)

    redacted = disclose_run_evidence(evidence, DisclosureProfile.CONTENT_REDACTED)
    full = disclose_run_evidence(evidence, DisclosureProfile.FULL_EVIDENCE)
    evidence["name"] = "Changed after disclosure"

    assert sentinel not in json.dumps(redacted)
    assert redacted["results"][0]["output_text"] == "[redacted]"
    assert full["name"] == f"private run {sentinel}"
    assert full["results"][0]["output_text"] == sentinel


def test_export_package_is_detached_from_mutable_input() -> None:
    evidence = _run_evidence()
    package = build_export_package(
        evidence,
        application_version="0.1.0",
        metric_versions={"correctness": "v1"},
        disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
    )
    evidence["name"] = "Changed later"

    assert package.payload["run"]["name"] == "Support benchmark"


def test_export_rejects_naive_generation_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_export_package(
            _run_evidence(),
            application_version="0.1.0",
            metric_versions={"correctness": "v1"},
            disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
            generated_at=datetime(2026, 7, 18, 12, 0),
        )


def test_export_rejects_non_json_or_non_finite_evidence() -> None:
    invalid_values: list[object] = [{"value": object()}, {"value": math.nan}]

    for value in invalid_values:
        with pytest.raises(ValueError):
            build_export_package(
                value,
                application_version="0.1.0",
                metric_versions={"correctness": "v1"},
                disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
            )


def test_envelope_bytes_are_canonical_json() -> None:
    package = build_export_package(
        _run_evidence(),
        application_version="0.1.0",
        metric_versions={"correctness": "v1"},
        disclosure_profile=DisclosureProfile.FULL_EVIDENCE,
        generated_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )

    assert json.loads(package.envelope_bytes) == package.envelope
    assert b"\n" not in package.envelope_bytes
    assert b": " not in package.envelope_bytes
    assert b", " not in package.envelope_bytes
