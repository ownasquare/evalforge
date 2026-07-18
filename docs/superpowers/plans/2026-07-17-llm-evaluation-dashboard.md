# LLM Evaluation Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-minded, locally runnable dashboard that evaluates a versioned test dataset across multiple prompt and model candidates, persists immutable provenance, and explains correctness, relevance, groundedness, hallucination risk, constraints, latency, and cost.

**Architecture:** FastAPI is the system of record and owns SQLAlchemy persistence, model credentials, background run execution, and analytics. Streamlit is a separate API-only client with a polished multipage workflow. A provider-neutral adapter protocol supplies a deterministic offline provider and explicit OpenAI Responses, OpenAI-compatible Chat Completions, and Ollama paths without silent fallback.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite/PostgreSQL URL support, Streamlit, Plotly, OpenAI Python SDK, HTTPX, pytest, Ruff, mypy, Bandit, pip-audit, uv, Docker Compose.

---

## File map

- `src/evalforge/config.py`: typed environment configuration and secret-safe provider settings.
- `src/evalforge/database.py`: SQLAlchemy engine/session lifecycle, SQLite pragmas, and readiness checks.
- `src/evalforge/models.py`: typed ORM entities for datasets, cases, prompts, models, runs, candidates, and results.
- `src/evalforge/schemas.py`: validated public API contracts.
- `src/evalforge/repositories.py`: transaction-scoped persistence operations.
- `src/evalforge/evaluation/`: prompt rendering, metric registry, model adapter contracts, and run orchestration.
- `src/evalforge/api/`: FastAPI factory, dependencies, middleware, and versioned routes.
- `src/evalforge/dashboard/`: API client, design system, page renderers, and Streamlit entry point.
- `src/evalforge/cli.py`: seed, reset, export, and local health commands.
- `alembic/`: versioned schema migrations.
- `tests/`: unit, contract, API integration, dashboard AppTest, and optional live-provider tests.
- `docs/`: architecture, API, evaluation methodology, security, operations, and completion evidence.

### Task 1: Scaffold the standalone repository and quality contract

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `Makefile`
- Create: `LICENSE`
- Create: `src/evalforge/__init__.py`
- Create: `tests/contract/test_project_contract.py`

- [ ] **Step 1: Write the failing repository contract**

```python
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_required_project_surfaces_exist() -> None:
    required = {
        "README.md",
        "pyproject.toml",
        ".env.example",
        "Dockerfile.api",
        "Dockerfile.dashboard",
        "compose.yaml",
        "docs/architecture.md",
        "docs/evaluation-methodology.md",
        "docs/operations.md",
        "docs/security.md",
    }
    assert not {path for path in required if not (ROOT / path).exists()}
```

- [ ] **Step 2: Run the contract and confirm the missing-surface failure**

Run: `uv run pytest tests/contract/test_project_contract.py -q`

Expected: the assertion lists the not-yet-created project surfaces.

- [ ] **Step 3: Add the package, dependency, lint, typing, and test configuration**

Use a `src` layout, pin compatible minor ranges, enable Ruff's `E`, `F`, `I`, `B`, `UP`, `SIM`, `ASYNC`, and `S` rules, run mypy in strict mode for `evalforge`, and require branch coverage for the core package. Keep the live-provider marker excluded from default pytest runs.

- [ ] **Step 4: Install and lock dependencies**

Run: `uv sync --all-extras --dev`

Expected: `.venv` and `uv.lock` are created without resolver conflicts.

- [ ] **Step 5: Initialize the repository and record the scaffold as one coherent commit**

Run: `git init`, then stage only the scaffold paths after validation.

Expected: the new folder is an independent Git repository with no unrelated workspace files.

### Task 2: Implement configuration, database lifecycle, and versioned domain models

**Files:**
- Create: `src/evalforge/config.py`
- Create: `src/evalforge/database.py`
- Create: `src/evalforge/models.py`
- Create: `src/evalforge/schemas.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_initial_schema.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/integration/test_database.py`

- [ ] **Step 1: Write failing settings and persistence tests**

```python
def test_settings_never_expose_provider_secret(settings) -> None:
    dumped = settings.model_dump_json()
    assert "OPENAI_API_KEY" not in dumped
    assert "secret-value" not in dumped


def test_run_result_keeps_immutable_case_snapshot(session, sample_result) -> None:
    session.add(sample_result)
    session.commit()
    assert sample_result.input_snapshot
    assert sample_result.case_hash
    assert sample_result.metric_versions
```

- [ ] **Step 2: Verify the tests fail because the typed settings and mappings do not exist**

Run: `uv run pytest tests/unit/test_config.py tests/integration/test_database.py -q`

Expected: import failures for the new modules.

- [ ] **Step 3: Implement settings and SQLAlchemy 2 mappings**

Create entities for `Dataset`, `TestCase`, `PromptTemplate`, `ModelProfile`, `EvaluationRun`, `RunCandidate`, and `EvaluationResult`. Store UUID strings, UTC timestamps, hashes, model/API mode, generation parameters, metric versions, per-metric evidence, request IDs, retry counts, latency, usage, estimated cost, status, and error taxonomy.

- [ ] **Step 4: Implement transaction-scoped sessions and SQLite pragmas**

Enable foreign keys, WAL, and a bounded busy timeout for file-backed SQLite. Keep sessions request-scoped and never share them between evaluation workers.

- [ ] **Step 5: Add and apply the initial Alembic migration**

Run: `uv run alembic upgrade head`

Expected: the database has all seven domain tables plus Alembic version state.

- [ ] **Step 6: Re-run persistence tests**

Run: `uv run pytest tests/unit/test_config.py tests/integration/test_database.py -q`

Expected: all settings and temporary-file SQLite tests pass.

### Task 3: Build transparent custom metrics and safe prompt rendering

**Files:**
- Create: `src/evalforge/evaluation/types.py`
- Create: `src/evalforge/evaluation/text.py`
- Create: `src/evalforge/evaluation/metrics.py`
- Create: `src/evalforge/evaluation/prompts.py`
- Test: `tests/unit/test_metrics.py`
- Test: `tests/unit/test_prompts.py`

- [ ] **Step 1: Write metric boundary and explanation tests**

```python
def test_groundedness_is_not_applicable_without_evidence(metric_registry) -> None:
    result = metric_registry.score_groundedness(output="Paris", context=None, reference=None)
    assert result.status == "not_applicable"
    assert result.score is None


def test_hallucination_risk_flags_unsupported_number(metric_registry) -> None:
    result = metric_registry.score_groundedness(
        output="Revenue grew 91 percent.",
        context="Revenue grew 12 percent.",
        reference="Revenue grew 12 percent.",
    )
    assert result.evidence["unsupported_numbers"] == ["91"]
    assert result.score is not None and result.score < 0.5
```

- [ ] **Step 2: Confirm the tests fail before metric code exists**

Run: `uv run pytest tests/unit/test_metrics.py tests/unit/test_prompts.py -q`

Expected: import failures.

- [ ] **Step 3: Implement versioned metric results**

Every metric returns `name`, `version`, `score`, `status`, `threshold`, `passed`, `reason`, and JSON-safe `evidence`. Implement normalized exact match, token precision/recall/F1, relevance, required phrase coverage, JSON/schema validity, groundedness, hallucination risk, and constraint adherence. Clamp applicable quality scores to `[0, 1]` and keep unavailable evidence as `not_applicable`.

- [ ] **Step 4: Implement deterministic weighted aggregation**

Average only applicable quality metrics using stored weights; invert hallucination risk exactly once; record the effective denominator so a missing metric cannot silently lower or raise the score.

- [ ] **Step 5: Implement strict prompt templates**

Allow only `{input}` and `{context}` fields, keep expected outputs evaluator-only, reject unknown placeholders before a run starts, and hash the final system and user prompt bytes for provenance.

- [ ] **Step 6: Run focused tests with full branch coverage on the metric package**

Run: `uv run pytest tests/unit/test_metrics.py tests/unit/test_prompts.py --cov=evalforge.evaluation --cov-branch -q`

Expected: all score-bound, empty-input, Unicode, numeric, JSON, and prompt-validation cases pass.

### Task 4: Add deterministic and real-provider model adapters

**Files:**
- Create: `src/evalforge/evaluation/adapters/base.py`
- Create: `src/evalforge/evaluation/adapters/deterministic.py`
- Create: `src/evalforge/evaluation/adapters/openai_compatible.py`
- Create: `src/evalforge/evaluation/adapters/registry.py`
- Test: `tests/unit/test_adapters.py`
- Test: `tests/live/test_provider_smoke.py`

- [ ] **Step 1: Write adapter contract tests**

```python
async def test_deterministic_adapter_is_repeatable(adapter, request) -> None:
    first = await adapter.generate(request)
    second = await adapter.generate(request)
    assert first.text == second.text
    assert first.request_id == second.request_id


async def test_api_mode_never_silently_falls_back(fake_openai_client, adapter) -> None:
    fake_openai_client.responses.create.side_effect = RuntimeError("unsupported")
    with pytest.raises(ProviderError):
        await adapter.generate(make_request(api_mode="responses"))
    fake_openai_client.chat.completions.create.assert_not_called()
```

- [ ] **Step 2: Confirm contract tests fail before adapters exist**

Run: `uv run pytest tests/unit/test_adapters.py -q`

Expected: import failures.

- [ ] **Step 3: Implement a normalized adapter protocol**

Return `text`, `provider`, `model`, `api_mode`, token usage, `latency_ms`, `request_id`, `finish_reason`, retry count, and safe metadata. Use SHA-256-derived fixture behavior so demo output is stable across Python processes and expose explicit balanced, concise, hallucinating, slow, and failing profiles.

- [ ] **Step 4: Implement explicit Responses and Chat Completions modes**

Use the official asynchronous OpenAI client with configured base URL, timeout, and bounded retries. Resolve secrets only from backend settings. Never accept raw secrets or arbitrary provider base URLs from dashboard requests and never retry by switching API modes.

- [ ] **Step 5: Keep the live smoke test opt-in**

Run: `uv run pytest -m live tests/live/test_provider_smoke.py -q` only when the operator intentionally supplies a configured provider.

Expected: default CI reports the test as deselected and makes no paid network request.

### Task 5: Implement repositories, background execution, recovery, and analytics

**Files:**
- Create: `src/evalforge/repositories.py`
- Create: `src/evalforge/evaluation/service.py`
- Create: `src/evalforge/evaluation/executor.py`
- Create: `src/evalforge/analytics.py`
- Test: `tests/integration/test_evaluation_service.py`
- Test: `tests/unit/test_analytics.py`

- [ ] **Step 1: Write a failing matrix-run integration test**

```python
async def test_run_evaluates_every_case_candidate_pair(service, seeded_ids) -> None:
    run = await service.create_run(
        dataset_id=seeded_ids.dataset,
        prompt_ids=seeded_ids.prompts,
        model_ids=seeded_ids.models,
    )
    await service.execute(run.id)
    detail = service.get_run(run.id)
    assert detail.status == "completed"
    assert detail.completed_items == detail.total_items == 12
    assert all(result.metric_results for result in detail.results)
```

- [ ] **Step 2: Confirm orchestration tests fail before service code exists**

Run: `uv run pytest tests/integration/test_evaluation_service.py tests/unit/test_analytics.py -q`

Expected: import failures.

- [ ] **Step 3: Implement snapshot-first run creation and bounded concurrency**

Validate all prompt fields and candidate profiles before storing the queued run. Snapshot case, dataset, prompt, model, generation, application, and metric versions. Execute candidates with an async semaphore and short database transactions; one failed item becomes an error result without erasing successful pairs.

- [ ] **Step 4: Implement a replaceable local executor**

Define a `RunExecutor` protocol and an in-process implementation for local/demo operation. On startup, mark abandoned queued/running work as interrupted with a recovery reason instead of leaving false in-progress status.

- [ ] **Step 5: Implement analytics with paired comparisons**

Return pass rate, error rate, mean score, per-metric means, paired case deltas, win/tie/loss counts, median and P95 latency, token totals, estimated cost, and failure taxonomy.

- [ ] **Step 6: Re-run orchestration and analytics tests**

Run: `uv run pytest tests/integration/test_evaluation_service.py tests/unit/test_analytics.py -q`

Expected: deterministic matrix totals and analytics pass.

### Task 6: Expose a complete FastAPI contract

**Files:**
- Create: `src/evalforge/api/app.py`
- Create: `src/evalforge/api/dependencies.py`
- Create: `src/evalforge/api/middleware.py`
- Create: `src/evalforge/api/routes/health.py`
- Create: `src/evalforge/api/routes/datasets.py`
- Create: `src/evalforge/api/routes/prompts.py`
- Create: `src/evalforge/api/routes/models.py`
- Create: `src/evalforge/api/routes/runs.py`
- Create: `src/evalforge/api/routes/analytics.py`
- Test: `tests/api/test_health.py`
- Test: `tests/api/test_resources.py`
- Test: `tests/api/test_runs.py`

- [ ] **Step 1: Write failing API lifecycle and validation tests**

```python
def test_run_submission_is_asynchronous(client, seeded_payload) -> None:
    response = client.post("/api/v1/runs", json=seeded_payload)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_unknown_prompt_field_is_rejected(client, prompt_payload) -> None:
    prompt_payload["user_template"] = "Answer {private_secret}"
    response = client.post("/api/v1/prompts", json=prompt_payload)
    assert response.status_code == 422
```

- [ ] **Step 2: Confirm the endpoint tests fail before the app factory exists**

Run: `uv run pytest tests/api -q`

Expected: import failures.

- [ ] **Step 3: Implement app factory, lifespan, middleware, and error envelopes**

Expose `/health/live`, `/health/ready`, `/api/v1/meta`, request IDs, bounded request size, timing headers, safe structured errors, and OpenAPI tags. Lifespan applies migrations, seeds only when explicitly configured, recovers interrupted jobs, and closes clients cleanly.

- [ ] **Step 4: Implement resource and run endpoints**

Provide dataset/test-case JSON and CSV import/export, prompt/model CRUD, run submission/list/detail/results/cancel, paired comparison, dashboard overview, and a progress event stream. Use page/limit bounds and return 409 for deletion conflicts.

- [ ] **Step 5: Run API tests and inspect OpenAPI generation**

Run: `uv run pytest tests/api -q`

Expected: CRUD, validation, 202 workflow, cancellation, pagination, imports, and safe error tests pass.

### Task 7: Build the Streamlit API client and visual system

**Files:**
- Create: `src/evalforge/dashboard/client.py`
- Create: `src/evalforge/dashboard/state.py`
- Create: `src/evalforge/dashboard/theme.py`
- Create: `src/evalforge/dashboard/components.py`
- Test: `tests/dashboard/test_client.py`
- Test: `tests/dashboard/test_components.py`

- [ ] **Step 1: Write failing client-state tests**

```python
def test_client_surfaces_request_id_on_api_failure(respx_mock, client) -> None:
    respx_mock.get("http://api/api/v1/meta").mock(
        return_value=httpx.Response(503, json={"detail": "not ready"}, headers={"x-request-id": "req-7"})
    )
    with pytest.raises(ApiError, match="req-7"):
        client.meta()
```

- [ ] **Step 2: Confirm the dashboard unit tests fail before client code exists**

Run: `uv run pytest tests/dashboard/test_client.py tests/dashboard/test_components.py -q`

Expected: import failures.

- [ ] **Step 3: Implement a typed, timeout-bounded API client**

Use an HTTPX client with explicit connect/read timeouts, safe JSON errors, request-ID display, short retry only for idempotent reads, and no provider credential handling. Cache only short-lived GET results.

- [ ] **Step 4: Implement accessible design tokens and reusable components**

Create indigo, cyan, amber, coral, slate, and surface tokens; high-contrast text; reduced-motion behavior; responsive cards; live health badge; metric gauges; status pills; empty/loading/error states; and keyboard-visible focus styling.

- [ ] **Step 5: Run focused dashboard tests**

Run: `uv run pytest tests/dashboard/test_client.py tests/dashboard/test_components.py -q`

Expected: timeout, retry, error, and rendering helper tests pass.

### Task 8: Implement the complete dashboard workflow

**Files:**
- Create: `src/evalforge/dashboard/app.py`
- Create: `src/evalforge/dashboard/pages/overview.py`
- Create: `src/evalforge/dashboard/pages/run_evaluation.py`
- Create: `src/evalforge/dashboard/pages/run_detail.py`
- Create: `src/evalforge/dashboard/pages/compare.py`
- Create: `src/evalforge/dashboard/pages/test_cases.py`
- Create: `src/evalforge/dashboard/pages/settings.py`
- Test: `tests/dashboard/test_app.py`

- [ ] **Step 1: Write a failing AppTest smoke journey**

```python
def test_overview_renders_product_identity(app_test) -> None:
    app_test.run(timeout=15)
    assert not app_test.exception
    assert any("EvalForge" in title.value for title in app_test.title)
```

- [ ] **Step 2: Confirm AppTest fails before the entry point exists**

Run: `uv run pytest tests/dashboard/test_app.py -q`

Expected: the Streamlit application path is missing.

- [ ] **Step 3: Implement navigation and overview**

Use `st.navigation` with Overview, Run Evaluation, Run Detail, Compare, Test Cases, and Settings. Show total runs, pass rate, mean score, estimated spend, trend chart, candidate leaderboard, failure categories, recent activity, and a prominent deterministic-demo action.

- [ ] **Step 4: Implement the evaluation lab and live progress**

Select one dataset, multiple prompt versions, and multiple model profiles; preview the matrix and estimated call count; require confirmation for real providers; submit one run; poll boundedly; render completed/error counts; and link directly to results.

- [ ] **Step 5: Implement exploration and management pages**

Run Detail shows metric cards, radar/bar charts, per-case evidence, output/reference/context panes, provenance, latency, cost, and error traces. Compare shows paired deltas and win/tie/loss. Test Cases supports create/edit/import/export. Settings shows backend readiness, provider capability without secrets, limits, metric versions, and local-executor caveats.

- [ ] **Step 6: Run AppTest coverage**

Run: `uv run pytest tests/dashboard -q`

Expected: navigation, empty state, populated state, API error state, and run submission tests pass with no uncaught Streamlit exceptions.

### Task 9: Add demo data, CLI, operational packaging, and CI

**Files:**
- Create: `src/evalforge/seed.py`
- Create: `src/evalforge/cli.py`
- Create: `examples/customer-support.json`
- Create: `examples/rag-groundedness.json`
- Create: `scripts/wait_for_api.py`
- Create: `Dockerfile.api`
- Create: `Dockerfile.dashboard`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.github/workflows/ci.yml`
- Create: `.pre-commit-config.yaml`
- Test: `tests/unit/test_seed.py`
- Test: `tests/contract/test_compose_contract.py`

- [ ] **Step 1: Write failing idempotency and container contract tests**

```python
def test_demo_seed_is_idempotent(session) -> None:
    seed_demo(session)
    first = count_everything(session)
    seed_demo(session)
    assert count_everything(session) == first
```

- [ ] **Step 2: Implement curated demo benchmarks and CLI commands**

Ship support, extraction, grounded QA, structured JSON, and constraint cases. Add `evalforge seed`, `evalforge reset-demo`, `evalforge export-run`, and `evalforge doctor` with non-destructive defaults.

- [ ] **Step 3: Implement hardened containers**

Build API and dashboard separately from `python:3.11-slim`, install locked dependencies, copy only required files, run as a non-root user, add health checks, and mount the SQLite volume only into the single-worker API container.

- [ ] **Step 4: Add continuous quality and security gates**

CI runs Ruff, mypy, pytest with coverage, Bandit, pip-audit, migration upgrade, and container contract checks. Live-provider tests remain excluded unless deliberately invoked.

- [ ] **Step 5: Validate demo and operational surfaces**

Run: `uv run evalforge seed`, `uv run evalforge doctor`, and `uv run pytest tests/unit/test_seed.py tests/contract -q`.

Expected: repeated seed is unchanged, readiness passes, and all required deployment surfaces exist.

### Task 10: Document, validate, and package truthful proof

**Files:**
- Create: `README.md`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `docs/architecture.md`
- Create: `docs/api.md`
- Create: `docs/evaluation-methodology.md`
- Create: `docs/operations.md`
- Create: `docs/security.md`
- Create: `docs/evaluation-dashboard/2026-07-17-completion.md`
- Create: `docs/handoffs/2026-07-17-codex-llm-evaluation-dashboard.handoff.mdc`

- [ ] **Step 1: Write user, operator, evaluator, and contributor documentation**

Document a five-minute deterministic demo, optional real-provider configuration, architecture and data flow, schema and endpoints, metric math and limitations, hallucination `not_applicable` semantics, security model, SQLite single-writer boundary, PostgreSQL migration path, local executor recovery behavior, tests, and troubleshooting.

- [ ] **Step 2: Run the complete static and automated validation matrix**

Run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest --cov=evalforge --cov-branch`, `uv run bandit -c pyproject.toml -r src`, `uv run pip-audit`.

Expected: every command exits successfully; any unavailable external/container proof is labeled rather than implied.

- [ ] **Step 3: Run the local API and dashboard against seeded, fixture-backed data**

Start one API worker on port 8000 and Streamlit on port 8501. Verify live/ready health, submit the complete deterministic matrix, observe progress, inspect results, compare candidates, import/export cases, and confirm provider secrets never reach browser payloads.

- [ ] **Step 4: Capture and inspect rendered browser proof**

Inspect desktop, tablet, and mobile widths for Overview, Run Evaluation, Run Detail, Compare, and error/empty states. Verify text wrapping, contrast, focus states, navigation, charts, evidence panels, and responsive behavior. Save approved screenshots under `docs/assets/` and fix every visible error before recapture.

- [ ] **Step 5: Create the completion record and 12-section continuation handoff**

Record changed files, branch/commit state, exact validation results, local fixture-backed integrity, provider/live test status, screenshot paths, known limits, and prioritized next work. Keep localhost, mock/fixture, live-provider, container, hosted, and production proof boundaries separate.

- [ ] **Step 6: Self-review the implementation against the original goal**

Confirm a user can create/import test cases, version prompts, register allowed model profiles, run a cross-product matrix, see correctness/relevance/groundedness/hallucination/constraint/latency/cost results, compare candidates, inspect evidence, export data, operate fully offline, and opt into a real provider without exposing credentials.
