from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evalforge.evaluation.adapters import AdapterRegistry, ModelAdapter
from evalforge.evaluation.evaluators import AsyncEvaluator, run_evaluator
from evalforge.evaluation.types import ApiMode, EvaluationCase, GenerationRequest
from evalforge.exports import DisclosureProfile, ExportSink, build_export_package
from examples.extensions.custom_adapter import register_example_adapter
from examples.extensions.custom_evaluator import WordLimitEvaluator
from examples.extensions.custom_export_sink import InMemoryExportSink


@pytest.mark.asyncio
async def test_custom_adapter_example_satisfies_and_registers_contract() -> None:
    registry = AdapterRegistry()
    register_example_adapter(registry)
    adapter = registry.get("example-echo")

    assert isinstance(adapter, ModelAdapter)
    response = await registry.generate(
        "example-echo",
        GenerationRequest(
            model="example-model",
            api_mode=ApiMode.DEMO,
            system_prompt="Answer directly.",
            user_prompt="What should this echo?",
            expected_output="This reference must never become model input.",
        ),
    )

    assert response.provider == "example-echo"
    assert response.text == "Echo: What should this echo?"
    assert response.metadata == {"synthetic": True}


@pytest.mark.asyncio
async def test_custom_evaluator_example_satisfies_declared_contract() -> None:
    evaluator = WordLimitEvaluator(maximum_words=3)

    assert isinstance(evaluator, AsyncEvaluator)
    result = await run_evaluator(
        evaluator,
        EvaluationCase(input_text="Be brief.", output="A short answer."),
    )

    assert result.name == "word_limit"
    assert result.passed is True
    assert result.evidence == {"word_count": 3, "maximum_words": 3}


def test_custom_export_sink_example_is_idempotent_and_protocol_compatible() -> None:
    package = build_export_package(
        {"id": "example-run"},
        application_version="0.1.0",
        metric_versions={"correctness": "example-v1"},
        disclosure_profile=DisclosureProfile.CONTENT_REDACTED,
        generated_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    sink = InMemoryExportSink()

    assert isinstance(sink, ExportSink)
    first = sink.export(package)
    second = sink.export(package)

    assert first.created is True
    assert second.created is False
    assert first.idempotency_key == second.idempotency_key
    assert first.exported_at == second.exported_at
    assert sink.packages[package.payload_sha256] == package.envelope_bytes
