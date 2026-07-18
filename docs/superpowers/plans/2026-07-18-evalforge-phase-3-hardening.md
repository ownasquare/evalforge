# EvalForge Phase 3 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make EvalForge safe to operate as either a zero-configuration local workspace or an authenticated shared evaluation service, remove cold-route failures, add recoverable database-backed execution ownership, and complete locally provable PostgreSQL, calibration, and export readiness without claiming hosted or paid-provider proof.

**Architecture:** Preserve FastAPI as the only system of record and Streamlit as an API-only client. Add explicit `local` and `oidc` identity modes, denial-first workspace scoping at the database/repository/service/API layers, and Streamlit OIDC token forwarding with complete identity-state invalidation. Launch Streamlit from a neutral module outside the implementation `pages/` package. Replace process-local queue authority with atomic database leases and fencing while keeping an embedded single-worker mode for the default local experience. Add offline calibration and a versioned local export envelope; treat hosted CI, TLS deployment, human labels, and paid-provider/vendor transmission as separate external acceptance layers.

**Tech Stack:** Python 3.11/3.12, FastAPI 0.139, Streamlit 1.59, SQLAlchemy 2.0, Alembic, PyJWT with asymmetric cryptography, SQLite, PostgreSQL 17, pytest, AppTest, Playwright, Ruff, mypy, Bandit, and pip-audit.

---

## File map

### Identity and tenant isolation

- Create `src/evalforge/security/__init__.py`, `src/evalforge/security/auth.py`, and `src/evalforge/security/permissions.py` for local/OIDC principal validation and role checks.
- Create `src/evalforge/audit.py` for append-only mutation audit recording.
- Create `src/evalforge/api/routes/session.py` for safe session and workspace discovery.
- Create `src/evalforge/dashboard/auth.py` for login, logout, access-token retrieval, workspace selection, and identity reset.
- Create `src/evalforge/migrations/versions/0003_identity_tenant_scope.py` for identity tables, deterministic local backfill, and workspace scoping.
- Modify `src/evalforge/config.py`, `src/evalforge/models.py`, `src/evalforge/repositories.py`, `src/evalforge/schemas.py`, `src/evalforge/database.py`, `src/evalforge/container.py`, `src/evalforge/analytics.py`, `src/evalforge/seed.py`, and `src/evalforge/cli.py`.
- Modify `src/evalforge/api/app.py`, `src/evalforge/api/dependencies.py`, and all modules under `src/evalforge/api/routes/`.
- Modify `src/evalforge/evaluation/service.py`, `src/evalforge/dashboard/app.py`, `src/evalforge/dashboard/client.py`, `src/evalforge/dashboard/state.py`, and `src/evalforge/dashboard/pages/settings.py`.
- Create `tests/unit/test_auth.py`, `tests/unit/test_permissions.py`, `tests/integration/test_tenant_isolation.py`, and `tests/integration/test_identity_migration.py`.
- Modify `tests/conftest.py`, `tests/integration/test_api_workflow.py`, `tests/integration/test_analytics.py`, `tests/dashboard/test_client.py`, `tests/dashboard/test_app.py`, and related repository/service tests affected by required workspace context.

### Cold direct routes

- Create `src/evalforge/streamlit_app.py` as the only supported Streamlit launcher.
- Modify `Makefile`, `Dockerfile.dashboard`, `.github/workflows/ci.yml`, `README.md`, `docs/operations.md`, `tests/dashboard/test_app.py`, and `tests/e2e/test_dashboard_smoke.py` to use the neutral launcher and fresh-process route matrix.

### Durable execution

- Create `src/evalforge/migrations/versions/0004_durable_execution_leases.py` and `src/evalforge/evaluation/leases.py`.
- Modify `src/evalforge/models.py`, `src/evalforge/repositories.py`, `src/evalforge/evaluation/executor.py`, `src/evalforge/evaluation/service.py`, `src/evalforge/config.py`, `src/evalforge/container.py`, `src/evalforge/database.py`, `src/evalforge/api/routes/health.py`, `src/evalforge/api/routes/runs.py`, `src/evalforge/cli.py`, and `src/evalforge/schemas.py`.
- Create `tests/integration/test_durable_executor.py` and PostgreSQL-focused tests under `tests/postgres/`.

### Provider, calibration, and export readiness

- Create `src/evalforge/evaluation/calibration.py` and `src/evalforge/evaluation/evaluators/base.py`.
- Create `src/evalforge/exports/__init__.py`, `src/evalforge/exports/base.py`, and `src/evalforge/exports/package.py`.
- Modify `src/evalforge/config.py`, `src/evalforge/schemas.py`, `src/evalforge/evaluation/service.py`, `src/evalforge/dashboard/pages/run_evaluation.py`, and `src/evalforge/dashboard/pages/settings.py` for external-transfer consent and user-selected spend ceilings.
- Create `tests/unit/test_calibration.py`, `tests/unit/test_evaluator_contract.py`, `tests/unit/test_export_package.py`, `tests/contract/test_export_sink.py`, and an opt-in `tests/live/test_provider_calibration.py` contract that skips without an explicit live flag.

### Release and durable records

- Modify `.env.example`, `compose.yaml`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `docs/api.md`, `docs/architecture.md`, `docs/evaluation-methodology.md`, `docs/operations.md`, `docs/security.md`, `README.md`, and relevant contract tests.
- Create `docs/llm-evaluation-dashboard/2026-07-18-phase-3-hardening.md` and final repo/global `.handoff.mdc` packages.

## Task 1: Capture a clean baseline and lock the cold-route regression

**Files:**
- Create: `src/evalforge/streamlit_app.py`
- Modify: `Makefile`
- Modify: `Dockerfile.dashboard`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `tests/dashboard/test_app.py`
- Modify: `tests/e2e/test_dashboard_smoke.py`

- [ ] **Step 1: Verify the inherited Phase 2 baseline**

Run the existing deterministic suite before source edits:

```bash
uv run pytest -q --disable-socket --allow-unix-socket
```

Expected: the inherited non-live/non-E2E suite passes. Record any pre-existing failure separately before proceeding.

- [ ] **Step 2: Add the failing launcher contract**

Change `tests/dashboard/test_app.py` so `APP_PATH` targets `src/evalforge/streamlit_app.py`. Extend `tests/contract/test_project_contract.py` to require every documented/runtime launcher to use that path and to reject `streamlit run src/evalforge/dashboard/app.py`.

Run:

```bash
uv run pytest tests/dashboard/test_app.py tests/contract/test_project_contract.py -q
```

Expected before implementation: failure because the neutral launcher does not exist and launch references still point at the router-colliding file.

- [ ] **Step 3: Add the neutral launcher and update every launch surface**

Create `src/evalforge/streamlit_app.py` with exactly one responsibility:

```python
from evalforge.dashboard.app import main

main()
```

Update Make, Docker, CI, README, operations docs, AppTest, and E2E references. Keep `dashboard/pages/` as implementation modules; do not hide the error dialog or add legacy page wrappers.

- [ ] **Step 4: Add fresh-process route coverage**

Parameterize cold starts for `/`, `/evaluate`, `/runs`, `/compare`, `/assets`, and `/settings`. Each case must start a new Streamlit process, wait only on `/_stcore/health`, open the target as the first browser session, assert its expected heading and exact URL, assert no `Page not found`, and reject console/page errors. Add one warm navigation plus back/forward test and mobile spot checks at 390×844.

- [ ] **Step 5: Run the focused route tests and commit**

```bash
uv run pytest tests/dashboard/test_app.py tests/contract/test_project_contract.py -q
```

Expected: all launcher/AppTest contracts pass. Browser E2E runs later against owned services. Commit as `fix: make EvalForge deep links cold-start safe`.

## Task 2: Add explicit identity configuration and denial-first permissions

**Files:**
- Create: `src/evalforge/security/__init__.py`
- Create: `src/evalforge/security/auth.py`
- Create: `src/evalforge/security/permissions.py`
- Create: `tests/unit/test_auth.py`
- Create: `tests/unit/test_permissions.py`
- Modify: `src/evalforge/config.py`
- Modify: `src/evalforge/container.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.env.example`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing configuration and permission tests**

Cover local defaults, OIDC fail-closed settings, HTTPS issuer/JWKS/public origin requirements outside tests, pinned `RS256`/`ES256` algorithms, clock-skew/key-cache bounds, local owner principal, role ordering, suspended workspace/membership denial, and safe `401`/`403` exceptions.

Run:

```bash
uv run pytest tests/unit/test_config.py tests/unit/test_auth.py tests/unit/test_permissions.py -q
```

Expected before implementation: import and assertion failures for missing identity types/settings.

- [ ] **Step 2: Implement immutable security contracts**

Add `AuthenticatedPrincipal` and `WorkspaceContext` frozen dataclasses; `WorkspaceRole` values `viewer`, `editor`, `admin`, `owner`; `AuthBackend`; `LocalAuthenticator`; and `OidcJwtAuthenticator`. OIDC validation must require bearer format, signature, pinned asymmetric algorithm, issuer, audience, expiry, and subject; JWKS retrieval must be HTTPS, bounded, cached, and independently mockable. Token text must never appear in exceptions or logs.

- [ ] **Step 3: Add explicit settings modes**

Add `auth_mode: Literal["local", "oidc"] = "local"`, issuer/audience/JWKS/public URL fields, accepted algorithm list, clock-skew seconds, key-cache seconds, and dashboard provider name. Local mode must reject non-loopback binding; OIDC production must reject incomplete or non-HTTPS configuration. Add `PyJWT[crypto]` and Streamlit auth support to locked dependencies.

- [ ] **Step 4: Assemble the authenticator and run tests**

Build the selected authenticator in `AppContainer` without returning raw tokens in `container_summary`.

```bash
uv run pytest tests/unit/test_config.py tests/unit/test_auth.py tests/unit/test_permissions.py tests/unit/test_container.py -q
```

Expected: all identity foundation tests pass.

## Task 3: Migrate existing evidence into workspace-scoped persistence

**Files:**
- Create: `src/evalforge/migrations/versions/0003_identity_tenant_scope.py`
- Create: `src/evalforge/audit.py`
- Create: `tests/integration/test_identity_migration.py`
- Create: `tests/integration/test_tenant_isolation.py`
- Modify: `src/evalforge/models.py`
- Modify: `src/evalforge/repositories.py`
- Modify: `src/evalforge/database.py`
- Modify: `src/evalforge/seed.py`
- Modify: `src/evalforge/cli.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write populated-migration and isolation failures**

Create a real `0002` SQLite database with dataset, cases, prompt, model, run, candidate, and result; migrate to head; assert stable IDs, hashes, snapshots, timestamps, deterministic local workspace/user/owner membership, non-null scope, and metadata parity. Add two-workspace tests that require duplicate name/version and idempotency keys to coexist while every get/list/update/delete remains isolated.

```bash
uv run pytest tests/integration/test_identity_migration.py tests/integration/test_tenant_isolation.py -q
```

Expected before implementation: missing-table/column failures.

- [ ] **Step 2: Add identity and scope models**

Add `Workspace`, `User`, `WorkspaceMembership`, and immutable `AuditEvent`. Add non-null `workspace_id` to all seven domain tables and `requested_by_user_id` to runs. Make domain uniqueness tenant-local and add tenant-first indexes. Add composite tenant-preserving foreign keys and matching `(workspace_id, id)` uniqueness so the database rejects mixed-workspace associations.

- [ ] **Step 3: Implement the deterministic backfill migration**

Migration `0003` must create identity/audit tables, insert stable local workspace/user/membership IDs, add nullable scope columns, backfill without rewriting evidence bytes, verify no null/mismatched scope remains, replace constraints/indexes, and make scope columns non-null. The downgrade must refuse if more than the deterministic local workspace owns domain data.

- [ ] **Step 4: Make user-facing repositories require scope**

Every user-facing repository constructor must require `WorkspaceContext`. Scope all reads, aggregates, mutations, reference checks, idempotency lookups, and child lookups. Replace `Session.get()` for user-visible resources with filtered selects. Keep a separately named system execution repository whose methods require a lease identity in Task 6. Add append-only audit creation and block ORM update/delete of persisted audit rows.

- [ ] **Step 5: Scope seed and operator CLI behavior**

Local seed defaults to the deterministic local workspace. Shared-mode seed/export requires explicit workspace. Add operator CLI commands for workspace creation and membership provision/revoke using issuer+subject; never accept or echo bearer tokens.

- [ ] **Step 6: Run migration and isolation tests**

```bash
uv run pytest tests/integration/test_database.py tests/integration/test_identity_migration.py tests/integration/test_tenant_isolation.py tests/unit/test_seed.py -q
```

Expected: both fresh and populated migration paths pass, cross-workspace IDs behave as not found, and composite constraints reject mixed ownership.

## Task 4: Enforce identity and workspace scope through services and APIs

**Files:**
- Create: `src/evalforge/api/routes/session.py`
- Modify: `src/evalforge/api/app.py`
- Modify: `src/evalforge/api/dependencies.py`
- Modify: all `src/evalforge/api/routes/*.py`
- Modify: `src/evalforge/evaluation/service.py`
- Modify: `src/evalforge/analytics.py`
- Modify: `src/evalforge/schemas.py`
- Modify: `tests/integration/test_api_workflow.py`
- Modify: `tests/integration/test_analytics.py`
- Create: `tests/contract/test_tenant_route_contract.py`

- [ ] **Step 1: Add failing API authorization tests**

Cover missing/invalid OIDC bearer token `401` with `WWW-Authenticate`, unknown/revoked membership `403`, viewer mutation `403`, editor model-management `403`, owner/admin/editor allowed operations, cross-workspace UUID `404`, tenant-local idempotency, spoofed `requested_by` override, import/export protection, and CORS acceptance for `Authorization` plus `X-EvalForge-Workspace-ID`.

Run:

```bash
uv run pytest tests/integration/test_api_workflow.py tests/integration/test_analytics.py tests/contract/test_tenant_route_contract.py -q
```

Expected before implementation: authorization and isolation assertions fail.

- [ ] **Step 2: Add principal/workspace dependencies and session endpoints**

Resolve identity before membership. In local mode, synthesize the deterministic local owner/workspace. In OIDC mode, require bearer token for user APIs and workspace header for scoped endpoints. Add `GET /api/v1/session` and `GET /api/v1/workspaces` with safe display fields only. Keep `/health/live` public; document `/health/ready` and `/metrics` as ingress-internal.

- [ ] **Step 3: Apply role checks and audit mutations**

Viewer reads/exports; editor manages datasets/prompts and runs; admin manages model profiles; owner governs memberships. All mutation/import/export/cancel outcomes emit safe audit records with request ID and resource identity, never prompt/output/token content.

- [ ] **Step 4: Scope evaluation and analytics services**

Require `WorkspaceContext` and principal for public preflight/create/cancel methods. Scope dataset, prompt, model, idempotency, run, results, comparison, overview, and export queries. Derive `requested_by` and `requested_by_user_id` server-side. Keep execution internals explicit and never import the system repository into route modules.

- [ ] **Step 5: Add a bypass-prevention contract and run focused tests**

The contract test must fail if user route modules import workspace-scoped ORM models or issue direct `select()`/`Session.get()` calls.

```bash
uv run pytest tests/integration/test_api_workflow.py tests/integration/test_analytics.py tests/contract/test_tenant_route_contract.py -q
```

Expected: authorization matrix, safe errors, analytics isolation, and route-boundary contracts pass.

## Task 5: Add OIDC login and workspace-safe dashboard state

**Files:**
- Create: `src/evalforge/dashboard/auth.py`
- Modify: `src/evalforge/dashboard/app.py`
- Modify: `src/evalforge/dashboard/client.py`
- Modify: `src/evalforge/dashboard/state.py`
- Modify: `src/evalforge/dashboard/pages/settings.py`
- Modify: `tests/dashboard/test_client.py`
- Modify: `tests/dashboard/test_app.py`
- Create: `tests/dashboard/test_auth.py`

- [ ] **Step 1: Write failing client/state tests**

Assert that the client centrally attaches bearer/workspace headers, uses only a non-secret token fingerprint in cache partitioning, never includes credentials in URLs/errors/repr, and resets selected/active runs, preflight, exports, filters, flash state, and cached responses on identity change, workspace switch, logout, membership change, and `401`. Assert `403` does not log the user out.

```bash
uv run pytest tests/dashboard/test_client.py tests/dashboard/test_auth.py tests/dashboard/test_app.py -q
```

Expected before implementation: missing auth module and state-reset failures.

- [ ] **Step 2: Implement Streamlit identity gating**

In local mode, render the existing Phase 2 journey unchanged. In OIDC mode, use `st.login()`/`st.logout()`, require `st.user.is_logged_in`, retrieve only `st.user.tokens["access"]` configured by `expose_tokens=["access"]`, call the API session/workspaces endpoints, and require an explicit workspace selection before registering evaluation pages. Never render tokens or raw claims.

- [ ] **Step 3: Partition the API client and clear stale evidence**

Key the cached client by API URL, SHA-256 token fingerprint, and workspace ID. Attach headers in one request builder. On `401`, close the client and clear identity/resource state before showing the login gate. On workspace switch or logout, clear all resource IDs and byte exports before rerun.

- [ ] **Step 4: Present account context without technical clutter**

Settings shows display name, workspace, role, workspace switcher, and sign-out. Hide login internals behind a compact Account section. Viewer mode hides mutation controls while keeping readable evidence pages.

- [ ] **Step 5: Run dashboard tests**

```bash
uv run pytest tests/dashboard/test_client.py tests/dashboard/test_auth.py tests/dashboard/test_app.py tests/dashboard/test_truthfulness.py -q
```

Expected: local journey remains green and mocked shared identity behavior clears all stale evidence.

## Task 6: Replace process-local queue authority with database leases

**Files:**
- Create: `src/evalforge/migrations/versions/0004_durable_execution_leases.py`
- Create: `src/evalforge/evaluation/leases.py`
- Create: `tests/integration/test_durable_executor.py`
- Modify: `src/evalforge/models.py`
- Modify: `src/evalforge/repositories.py`
- Modify: `src/evalforge/evaluation/executor.py`
- Modify: `src/evalforge/evaluation/service.py`
- Modify: `src/evalforge/config.py`
- Modify: `src/evalforge/container.py`
- Modify: `src/evalforge/database.py`
- Modify: `src/evalforge/api/routes/health.py`
- Modify: `src/evalforge/api/routes/runs.py`
- Modify: `src/evalforge/cli.py`

- [ ] **Step 1: Write failing two-worker and fencing tests**

Cover one claim across two engines, discovery of committed work without local `submit`, heartbeat preventing takeover, expired lease transfer, stale-worker mutation rejection, safe retry before provider invocation, no automatic replay after ambiguous external invocation, persisted-output scoring resume, cross-process cancellation, restart preservation, and truthful readiness.

```bash
uv run pytest tests/integration/test_durable_executor.py -q
```

Expected before implementation: missing lease API and duplicate/recovery failures.

- [ ] **Step 2: Add lease and attempt persistence**

Migration `0004` adds `lease_owner`, `lease_token`, `lease_epoch`, `lease_expires_at`, `claim_attempts`, and `next_claim_at` to runs; creates `execution_attempts`; and adds a claim index over workspace/status/next-claim/lease-expiry/queue time. Advance readiness revision/columns.

- [ ] **Step 3: Implement atomic claims and fenced writes**

Use a guarded update that claims only queued work or expired ownership and increments `lease_epoch`. Renew against database time. Every run/result/progress mutation must match run ID, lease token, and epoch. Split result-plan persistence from claiming. A takeover may score a persisted provider response, but an in-flight external request without a persisted response becomes interrupted and billing-ambiguous; it is never replayed automatically.

- [ ] **Step 4: Make the executor database-driven**

Remove global `recover_interrupted()` from startup. Poll persisted work; a process-local event may only accelerate polling. Add `embedded_single`, `api_only`, and `database_worker` modes plus an explicit `evalforge worker` CLI. Close/release behavior must not interrupt another owner. Readiness reports the configured role and current worker health truthfully.

- [ ] **Step 5: Run durable execution tests**

```bash
uv run pytest tests/integration/test_durable_executor.py tests/integration/test_run_lifecycle.py -q
```

Expected: concurrency, fencing, cancellation, restart, and ambiguity tests pass on SQLite.

## Task 7: Prove the real PostgreSQL path locally and strengthen CI source

**Files:**
- Create: `tests/postgres/conftest.py`
- Create: `tests/postgres/test_migrations.py`
- Create: `tests/postgres/test_durable_execution.py`
- Create: `tests/postgres/test_api_workflow.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Modify: `tests/contract/test_project_contract.py`
- Modify: `docs/operations.md`

- [ ] **Step 1: Add PostgreSQL-marked migration/API/lease tests**

Require `EVALFORGE_POSTGRES_TEST_URL`; skip locally only when absent; fail the CI PostgreSQL job if absent. Exercise migration to head, deterministic lifecycle, tenant isolation, two-engine claim contention, lease expiry/fencing/cancellation/recovery, and readiness.

- [ ] **Step 2: Strengthen the PostgreSQL workflow**

Add packaged migration, `alembic check`, seed/doctor, and the `postgres` marker suite. Keep PostgreSQL credentials ephemeral and confined to the workflow. Add PostgreSQL-backed E2E if runtime remains within the job timeout.

- [ ] **Step 3: Start an owned local PostgreSQL 17 service and run proof**

Use an unused host port and an ephemeral named container/volume. Run:

```bash
uv run pytest -m postgres tests/postgres -q
```

Expected: all PostgreSQL tests pass. Remove the owned service and verify it is gone. If the local Docker daemon is unavailable, record source-only CI proof and do not claim runtime proof.

## Task 8: Add consent, offline calibration, and versioned local exports

**Files:**
- Create: `src/evalforge/evaluation/calibration.py`
- Create: `src/evalforge/evaluation/evaluators/base.py`
- Create: `src/evalforge/exports/__init__.py`
- Create: `src/evalforge/exports/base.py`
- Create: `src/evalforge/exports/package.py`
- Create: `tests/unit/test_calibration.py`
- Create: `tests/unit/test_evaluator_contract.py`
- Create: `tests/unit/test_export_package.py`
- Create: `tests/contract/test_export_sink.py`
- Create: `tests/live/test_provider_calibration.py`
- Modify: `src/evalforge/schemas.py`
- Modify: `src/evalforge/evaluation/service.py`
- Modify: `src/evalforge/dashboard/pages/run_evaluation.py`
- Modify: `src/evalforge/dashboard/pages/settings.py`
- Modify: `docs/evaluation-methodology.md`
- Modify: `docs/security.md`

- [ ] **Step 1: Write failing consent/calibration/export tests**

Require external-data-transfer consent separately from cost consent and require a user-selected spend ceiling no higher than the server ceiling. Calibration fixtures must produce sample size, confusion matrix, precision, recall, F1, selected threshold, and calibration-set SHA-256. Export tests must prove deterministic payload bytes/hash, schema version, disclosure profile, and idempotent local receipt.

- [ ] **Step 2: Implement offline calibration**

Add pure functions over human-labeled fixture rows. Calibration does not call a model and must label its output as offline statistical evidence, not human-production validation. Add an asynchronous evaluator protocol with declared call/cost/privacy behavior before any LLM judge implementation.

- [ ] **Step 3: Implement a versioned export envelope and local sink**

Package immutable run evidence with `schema_version`, payload SHA-256, application/metric versions, generation timestamp outside the hashed payload, and explicit disclosure/redaction profile. The sink protocol returns an idempotent receipt; implement only a local file sink now. Vendor sinks remain optional and transmit nothing by default.

- [ ] **Step 4: Add an opt-in live calibration contract**

The `live` test must require an explicit enable flag, provider/model allowlist, approved benchmark path, and exact spend ceiling; otherwise it skips before loading a credential or creating a provider client. Do not run it in this phase without user authority.

- [ ] **Step 5: Run local readiness tests**

```bash
uv run pytest tests/unit/test_calibration.py tests/unit/test_evaluator_contract.py tests/unit/test_export_package.py tests/contract/test_export_sink.py -q
```

Expected: local calibration/export contracts pass; live provider tests remain deliberately unexecuted.

## Task 9: Run the complete release matrix and rendered proof

**Files:**
- Modify: relevant source/tests/docs from Tasks 1–8

- [ ] **Step 1: Run formatting, lint, and types**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src/evalforge
```

Expected: all pass without blanket ignores.

- [ ] **Step 2: Run deterministic coverage**

```bash
uv run pytest -q --disable-socket --allow-unix-socket --cov=evalforge --cov-branch --cov-report=term-missing --cov-report=xml
```

Expected: all non-live/non-E2E/non-PostgreSQL tests pass and branch coverage remains at or above 80%.

- [ ] **Step 3: Run security, dependency, migration, and container checks**

```bash
uv run bandit -q -c pyproject.toml -r src
uv run pip-audit
uv run alembic upgrade head
uv run alembic check
uv run pytest tests/contract -q
```

Expected: no unresolved changed-surface finding, one Alembic head, and green project/container contracts.

- [ ] **Step 4: Perform in-app Browser proof before repository Playwright**

Launch API/dashboard on fresh owned ports. At 1280×720 and 390×844, exercise Home → New evaluation → named deterministic run → results → compare; directly cold-open all six routes; inspect console errors/warnings; verify no `Page not found`, no blank shell, no horizontal overflow, reachable actions, correct role/account copy, and a non-generic work-app interface. OIDC is source/mock proof only until an external provider is configured.

- [ ] **Step 5: Run Playwright E2E**

```bash
uv run pytest -q -m e2e tests/e2e
```

Expected: cold-route matrix and complete local deterministic journey pass against the owned services.

## Task 10: Close documentation, commits, and proof boundaries

**Files:**
- Modify: `README.md`
- Modify: `docs/api.md`
- Modify: `docs/architecture.md`
- Modify: `docs/evaluation-methodology.md`
- Modify: `docs/operations.md`
- Modify: `docs/security.md`
- Create: `docs/llm-evaluation-dashboard/2026-07-18-phase-3-hardening.md`
- Create: `docs/handoffs/2026-07-18-codex-evalforge-phase-3.handoff.mdc`
- Create mirror: `/Users/fortunevieyra/Documents/Github/beladed.com/docs/handoffs/2026-07-18-codex-evalforge-phase-3.handoff.mdc`

- [ ] **Step 1: Document the operator contract**

Explain local versus OIDC mode, workspace/role matrix, CLI provisioning, Streamlit token exposure configuration, ingress requirements, executor modes, worker lifecycle, lease ambiguity behavior, PostgreSQL runbook, external transfer consent, spend limits, calibration evidence, export profiles, and backup/restore implications.

- [ ] **Step 2: Record exact proof boundaries**

State separately: local SQLite result; local PostgreSQL result; Browser result; Playwright result; hosted GitHub Actions unproved without remote; TLS/public deployment unproved; OIDC provider integration unproved without provider; paid calibration unproved; human calibration unproved; LangSmith/W&B transmission not implemented or authorized.

- [ ] **Step 3: Write the completion record and final 12-section handoffs**

Include exact commits, tests, coverage, screenshots, ports/process cleanup, database/container cleanup, external gates, and next authorized actions. Do not turn source readiness into production acceptance.

- [ ] **Step 4: Commit coherently and verify clean state**

Create scoped local commits for identity/tenancy, durable execution, calibration/export readiness, and release proof. Verify no secrets, no unrelated paths, no leftover owned services, no untracked files, and clean `git status`. Do not push because no remote is configured.

## Plan self-review

- [x] Covers every Phase 2 next step: identity/network foundation, cold direct routes, durable worker ownership, PostgreSQL/CI, provider/judge calibration readiness, and export readiness.
- [x] Preserves the zero-configuration local experience while making shared mode explicit and denial-first.
- [x] Puts tenant isolation below the UI and includes analytics, exports, idempotency, children, and background execution.
- [x] Fixes the reproduced Streamlit router collision at its launcher boundary instead of hiding the symptom.
- [x] Does not claim exactly-once external provider behavior; ambiguous in-flight calls are fenced and not replayed automatically.
- [x] Separates local/source proof from hosted, public, identity-provider, paid-provider, human-label, and vendor-export proof.
- [x] Uses Playwright only for E2E and AppTest/pytest for component and service coverage.
- [x] Contains exact file paths, red/green commands, observable expectations, and no unfinished marker, placeholder implementation, or secret value.
