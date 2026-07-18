# EvalForge completion record — 2026-07-17

## Outcome

EvalForge is complete for its declared single-user, loopback-first evaluation-workbench scope. It
provides a FastAPI system of record, Streamlit dashboard, versioned custom metrics, immutable
run/case/provider evidence, SQLite by default, an explicit PostgreSQL path, deterministic offline
providers, opt-in OpenAI/OpenAI-compatible providers, Docker Compose, CI configuration, examples,
and operator documentation.

Implementation commit: `3347260427f23ef42d56a6736a5eef0c07d83d9b` on local branch `main`.
The repository has no Git remote, so no push, pull request, hosted deployment, or GitHub Actions run
is claimed.

## Delivered product surfaces

- Dashboard routes for overview, evaluation setup/preflight/submission, run history and case-level
  evidence, paired candidate comparison, datasets/prompts, and safe settings/capabilities.
- CRUD and import/export APIs for versioned datasets, cases, prompts, and model profiles.
- Atomic run preflight/creation with exact persisted preflight evidence, idempotency, call/token/cost
  limits, explicit real-cost and unknown-price consent, and reference-leakage prevention.
- Persisted local worker with bounded concurrency, cancellation, startup interruption recovery,
  provider-response-before-scoring persistence, and no automatic retry after billing-ambiguous
  failures.
- Versioned deterministic metrics for correctness, relevance, groundedness, hallucination risk,
  phrase coverage, JSON validity, constraints, style, and direction-aware aggregate quality.
- Paired case comparison plus separate quality, latency, usage, failure, and known-cost denominators.
- Packaged Alembic migrations and demo fixtures, a CLI for seed/doctor, Docker images, Compose
  security controls, and a GitHub CI workflow with a separate Playwright E2E job.

## Final validation evidence

| Layer | Evidence | Result |
|---|---|---|
| Locked environment | `uv lock --check`; `uv sync --all-groups --frozen` | Pass; 123 packages resolved and 111 installed/audited in the development environment. |
| Lint/format/types | Ruff check, Ruff format check, strict mypy | Pass; 74 Python files formatted and 51 source files type-checked. |
| Automated core | Pytest with socket disabled and branch coverage | **128 passed, 1 E2E deselected**, 82.11% branch coverage against an 80% gate. |
| Security | Bandit over `src`; `pip-audit` | Pass; no Bandit findings and no known dependency vulnerabilities. The unpublished local package itself is the documented audit skip. |
| Migration/readiness | Upgrade a populated interrupted SQLite 0001 database; seed; doctor | Pass at `0002_preflight_context_cost_ack`; stale batch artifacts and partially applied columns recover; 18 focused database tests pass. |
| Native browser | Real API + Streamlit processes, root navigation, preflight, run, result evidence, comparison | Pass; completed 10/10 results with two candidates. Clean desktop session at 1280×720 and mobile at 390×844 had no dialog, horizontal overflow, console warning, or console error. |
| Wheel | Build, archive integrity, isolated virtualenv install outside checkout, seed, doctor, revision readback | Pass; installed from site-packages, seeded 2 datasets/2 prompts/3 models, reported database ready at migration 0002 and metadata adapter `deterministic`. |
| Docker Compose | Exact final images built and run; service-network health/meta; seeded deterministic matrix | Pass; API and dashboard healthy, UID 10001, read-only root filesystems, all capabilities dropped, `no-new-privileges`; 10/10 results and two comparison candidates. |
| Cleanup | Native servers stopped; Compose containers/network/disposable demo volume removed | Pass; the unrelated `privacy-first-local-llm` Compose project and the unrelated `codebase-intelligence` process were left untouched. |

The Playwright E2E file is intentionally excluded from the core test invocation and is configured
as a separate CI job. Its supported root-first journey was exercised manually with the in-app
browser, but GitHub Actions itself was not run because this local repository has no remote.

## Browser observations

The root-first workflow rendered all primary surfaces and completed a deterministic evaluation.
Run detail exposed output, reference, source context, each metric's structured evidence (including
best context-chunk indexes), and immutable provenance. Compare showed paired-case counts,
wins/ties/losses, quality, and operational trade-offs.

Streamlit's first cold direct request to a dynamically registered subroute such as `/evaluate` can
show its framework-level “Page not found” overlay before the initial session registers pages. The
supported entry contract, README, operations guide, and E2E test therefore start at
`http://127.0.0.1:8501/` and navigate through the visible menu. Once bootstrapped, dashboard route
navigation is clean.

## Container observations

Final local image IDs:

- API: `sha256:bd6e32a28de35aa4a1a6bb5b916501fb97d6e7d6afd0a9ecc8b1d2ad247eddb6`
- Dashboard: `sha256:4eb9de819a557e23a286463845b30bb1489fa1a0d1fb20b231a549e73b7fa46f`

A separate workspace service, `codebase-intelligence`, occupied host port 8000 during the final
rebuild. The final-image readback therefore used the intended Compose service network
(`dashboard -> api:8000`) rather than stopping or modifying that unrelated service. Both internal
health and metadata returned ready; metadata listed the built-in deterministic adapter.

## Artifact hashes

- Wheel `dist/evalforge_dashboard-0.1.0-py3-none-any.whl`:
  `00b346afdff7a0e5a6578e03c2e83d8c734008766ab5056644463fd402d9e27c`
- `uv.lock`: `b37e7da0c11ef2769deae0af20fb1c720e60dfd3f3f0edd79444c74e2c4687a0`
- `pyproject.toml`: `16b73ca77e4328bc8f3a8f254573eefbfba56704f8985fba2c9eef461c66124d`
- `compose.yaml`: `acdd5bab462c59ac872827966de2480c9973cfe140139f856d5b189b973e6bd4`
- Migration 0002:
  `542dce71b6c020202b870cb879b724f6a686a82fc116eda06a8e78f2d7c35612`

## Proof boundaries

Verified now:

- local deterministic Python runtime;
- local file-backed SQLite persistence and populated-schema upgrade recovery;
- native-process desktop/mobile browser workflow;
- exact wheel build and isolated install;
- local Docker image build, Compose health/security, service-to-service API readback, and completed
  deterministic run;
- mocked provider contracts and headerless compatible-provider behavior;
- source/contract configuration for PostgreSQL and GitHub Actions.

Not claimed:

- paid/live provider execution;
- locally executed PostgreSQL or a green remote GitHub Actions run;
- hosted-dev or production deployment;
- authentication, authorization, tenant isolation, TLS ingress, or horizontal worker safety;
- human-calibrated correctness or hallucination detection.

## Run locally

```bash
cp .env.example .env
uv sync --all-groups --frozen
uv run alembic upgrade head
uv run evalforge seed
uv run uvicorn evalforge.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

In a second terminal:

```bash
uv run streamlit run src/evalforge/dashboard/app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501/` and select **Run Evaluation**.

## Durable continuation

The required cross-harness handoff is mirrored in this repository at
`docs/handoffs/2026-07-17-codex-evalforge-dashboard.handoff.mdc` and globally at
`/Users/fortunevieyra/Documents/Github/beladed.com/docs/handoffs/2026-07-17-codex-evalforge-dashboard.handoff.mdc`.
