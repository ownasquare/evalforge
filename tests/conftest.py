from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evalforge.config import Settings
from evalforge.database import Base, SessionFactory, create_database_engine, create_session_factory
from evalforge.models import (
    ApiMode,
    Dataset,
    EvaluationResult,
    EvaluationRun,
    ModelProfile,
    PromptTemplate,
    ResultStatus,
    RunCandidate,
    RunStatus,
    TestCase,
    canonical_json_hash,
)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'evalforge-test.db'}"


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        sqlite_busy_timeout_ms=3_500,
        openai_api_key="secret-value",
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    database_engine = create_database_engine(settings)
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> SessionFactory:
    return create_session_factory(engine)


@pytest.fixture
def session(session_factory: SessionFactory) -> Iterator[Session]:
    database_session = session_factory()
    try:
        yield database_session
    finally:
        database_session.rollback()
        database_session.close()


@pytest.fixture
def sample_result(session: Session) -> EvaluationResult:
    case_payload = {
        "external_id": "case-1",
        "position": 0,
        "input": "What is the capital of France?",
        "context": "France's capital is Paris.",
        "expected_output": "Paris",
        "required_phrases": ["Paris"],
        "constraints": {},
        "tags": ["geography"],
        "metadata": {},
    }
    case_hash = canonical_json_hash(case_payload)
    dataset_snapshot = {
        "name": "Geography",
        "version": 1,
        "cases": [{**case_payload, "case_hash": case_hash}],
    }
    dataset = Dataset(
        name="Geography",
        description="Small factual benchmark",
        version=1,
        content_hash=canonical_json_hash(dataset_snapshot),
        metadata_json={},
    )
    case = TestCase(
        dataset=dataset,
        external_id="case-1",
        position=0,
        input_text=case_payload["input"],
        context_text=case_payload["context"],
        expected_output=case_payload["expected_output"],
        required_phrases=["Paris"],
        constraints_json={},
        tags=["geography"],
        metadata_json={},
        case_hash=case_hash,
    )
    prompt_snapshot = {
        "name": "Direct answer",
        "version": 1,
        "system_template": "Answer from the evidence.",
        "user_template": "{input}\n{context}",
        "variables": ["context", "input"],
    }
    prompt = PromptTemplate(
        name="Direct answer",
        description=None,
        version=1,
        system_template=prompt_snapshot["system_template"],
        user_template=prompt_snapshot["user_template"],
        variables=prompt_snapshot["variables"],
        template_hash=canonical_json_hash(prompt_snapshot),
        metadata_json={},
    )
    model_snapshot = {
        "name": "Offline balanced",
        "version": 1,
        "provider": "deterministic",
        "model_name": "balanced",
        "api_mode": "deterministic",
        "generation_parameters": {"temperature": 0},
        "input_price_micro_usd_per_million_tokens": 0,
        "output_price_micro_usd_per_million_tokens": 0,
        "pricing_source": "deterministic",
    }
    model = ModelProfile(
        name="Offline balanced",
        description=None,
        version=1,
        provider="deterministic",
        model_name="balanced",
        api_mode=ApiMode.DETERMINISTIC,
        generation_parameters={"temperature": 0},
        input_price_micro_usd_per_million_tokens=0,
        output_price_micro_usd_per_million_tokens=0,
        pricing_source="deterministic",
        profile_hash=canonical_json_hash(model_snapshot),
        enabled=True,
        metadata_json={},
    )
    session.add_all([dataset, prompt, model])
    session.flush()

    run = EvaluationRun(
        dataset=dataset,
        dataset_snapshot=dataset_snapshot,
        dataset_hash=dataset.content_hash,
        metric_configuration_snapshot={
            "versions": {"correctness": "1.0.0"},
            "directions": {"correctness": "higher_is_better"},
        },
        application_version="test",
        executor_type="in_process",
        acknowledge_real_cost=False,
        status=RunStatus.QUEUED,
        total_items=1,
    )
    session.add(run)
    session.flush()
    candidate = RunCandidate(
        run=run,
        prompt_template=prompt,
        model_profile=model,
        ordinal=0,
        label="Direct answer / Offline balanced",
        prompt_snapshot=prompt_snapshot,
        prompt_hash=prompt.template_hash,
        model_snapshot=model_snapshot,
        model_hash=model.profile_hash,
        generation_parameters_snapshot={"temperature": 0},
        candidate_hash=canonical_json_hash(
            {"prompt": prompt_snapshot, "model": model_snapshot, "temperature": 0}
        ),
        status=RunStatus.QUEUED,
        total_items=1,
    )
    session.add(candidate)
    session.flush()

    return EvaluationResult(
        run=run,
        candidate=candidate,
        test_case=case,
        input_snapshot=case.snapshot(),
        case_hash=case.case_hash,
        prompt_snapshot=prompt_snapshot,
        prompt_hash=canonical_json_hash(
            {"system": "Answer from the evidence.", "user": "Question and evidence"}
        ),
        model_snapshot=model_snapshot,
        model_hash=model.profile_hash,
        generation_parameters_snapshot={"temperature": 0},
        rendered_system_prompt="Answer from the evidence.",
        rendered_user_prompt="What is the capital of France?\nFrance's capital is Paris.",
        output_text="Paris",
        metric_versions={"correctness": "1.0.0"},
        metric_directions={"correctness": "higher_is_better"},
        metric_applicability={"correctness": "applicable"},
        metric_results={"correctness": {"score": 1.0, "passed": True}},
        aggregate_score=1.0,
        aggregate_passed=True,
        effective_metric_weight=1.0,
        provider="deterministic",
        model_name="balanced",
        api_mode=ApiMode.DETERMINISTIC,
        retry_count=0,
        latency_ms=1,
        input_tokens=10,
        output_tokens=1,
        total_tokens=11,
        estimated_cost_micro_usd=0,
        cost_source="deterministic",
        provider_metadata={},
        status=ResultStatus.COMPLETED,
    )
