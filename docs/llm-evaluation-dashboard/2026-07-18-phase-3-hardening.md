# EvalForge Phase 3 Hardening and Shared-Workspace Readiness

Date: 2026-07-18
Repository: `/Users/fortunevieyra/Documents/Github/ai-projects/llm-evaluation-dashboard`
Source commits: `c3775be470bc64146172d81e1c919f3a16056d2e`, `7393422ee16103d9396b248d0b1f31ad5f717d7f`, `aa53c64ff67bc2e8c148ef65a0fae60df75326e6`
Completion record: the commit containing this file

## Outcome

Phase 3 closes the bounded productization and hardening work from the Phase 2 handoff. EvalForge now
has a loopback-local or OIDC identity boundary, tenant-scoped persistence, ordered workspace roles,
database-backed run leases, PostgreSQL lifecycle proof, explicit provider transfer and spend
safeguards, deterministic offline calibration primitives, and portable versioned evidence exports.
The Streamlit dashboard keeps the restrained work-app design from Phase 2 while making shared-use
risks understandable in plain language.

This is a complete local source and runtime foundation for the Phase 3 scope. It is not a claim of a
hosted service, production TLS or IdP readback, paid-provider accuracy, or exactly-once external
billing.

## Identity, tenancy, and startup

- Added deterministic loopback-only local identity and OIDC JWT authentication.
- OIDC validates an exact HTTPS issuer and audience, asymmetric signatures, subject, expiration,
  bounded clock skew, and bounded JWKS caching.
- Added active users, workspaces, memberships, Viewer/Editor/Admin/Owner roles, and append-only audit
  events.
- Scoped datasets, cases, prompts, model profiles, runs, candidates, results, attempts, analytics,
  comparisons, exports, idempotency, and dashboard caches to one workspace.
- Added operator commands for workspace creation and membership provisioning or revocation without
  erasing history.
- Added validated API and dashboard launchers. Local auth cannot bind beyond loopback.
- Non-test OIDC requires HTTPS issuer, JWKS, public base, and dashboard API URLs.
- Compose requires trusted public hosts and a secret-mounted Streamlit OAuth configuration.
- FastAPI lifespan is the single API migration authority; the launcher no longer races a duplicate
  migration path.

## Durable execution and PostgreSQL

- Added persisted execution attempts and atomic database claims with owner, random token, fencing
  epoch, expiry, and heartbeat.
- Added `embedded_single`, `api_only`, and `database_worker` roles.
- The database row is queue authority; workers discover committed work instead of relying on a
  process-local queue.
- Every worker mutation is fenced. Lost heartbeats cancel in-flight work and stop further writes.
- An expired lease owner cannot finish a run or clear another worker's active lease.
- Rate-limited or otherwise uncertain provider outcomes are retained as `billing_ambiguous` and are
  not immediately replayed.
- API-only readiness reports `external_unobserved` instead of inventing worker health.
- New-run eligibility uses the database clock, preventing host/database clock skew from delaying
  claims.
- SQLite is restricted to one-process embedded execution. PostgreSQL is required for the
  multi-process database-worker topology.

## Provider, evaluator, and export safeguards

- Real generation requires separate external-transfer, real-cost, and unknown-cost consent as
  applicable, plus a positive user spend ceiling under the server cap.
- Preflight shows planned logical calls, the maximum provider request count, known/unknown pricing,
  and zero automatic provider retries.
- Generic billable generation makes exactly one network attempt, including HTTP 429.
- Added provider-neutral offline/external evaluator declarations covering calls, cost behavior,
  transmitted fields, and metric identity.
- Added deterministic offline threshold calibration with confusion matrix, precision, recall, F1,
  and calibration-set SHA-256. It remains a library primitive and has not been human-calibrated.
- Added `evalforge.run-export.v1`, canonical payload hashing, API response hashes, explicit
  `content_redacted` and `full_evidence` profiles, and a private idempotent local file sink.
- Evidence package, JSON, and CSV use the same disclosure profile and confirmation guard.
- The redacted profile is a strict safe-provenance allowlist; realistic sentinel tests prove that
  benchmark, identity, prompt, output, provider, and evidence content cannot leak through it.

## Product and UI changes

- Fixed cold direct links for all six Streamlit routes through the neutral top-level launcher.
- Added sign-in, sign-out, workspace selection, role display, and identity-aware cache invalidation.
- Viewer mode preserves evidence review while removing create, import, edit, run, and cancel
  affordances.
- Added plain-language transfer, request-count, cost, and budget controls to preflight.
- Evidence exports default to scores and metadata. Full stored content displays a prominent warning
  and keeps package, JSON, and CSV preparation disabled until the user acknowledges disclosure.
- Preserved the off-white canvas, neutral typography, restrained teal accent, compact spacing, and
  progressive disclosure of technical evidence.

## Migration safety

- Revision `0003_identity_tenant_scope` backfills Phase 2 rows into a stable local workspace.
- Its downgrade refuses to discard nonlocal identity or audit history and has an exact populated
  `0002` schema/data round-trip test.
- Revision `0004_durable_execution_leases` adds claim and attempt evidence.
- SQLite migration uses a private connection, suspends foreign keys only for batch reconstruction,
  runs `foreign_key_check`, and restores enforcement.
- PostgreSQL avoids SQLite-only batch recreation and uses explicit `postgresql+psycopg` URLs.

## Automated validation

| Proof layer | Final result |
|---|---|
| Offline pytest with sockets disabled | `304 passed, 3 skipped, 11 deselected` |
| Branch coverage | `83.89%` (required floor `80%`) |
| Ruff lint / format | All checks passed / `111 files already formatted` |
| mypy strict | Success across `67` source files |
| Bandit | Exit `0`; no findings emitted with project configuration |
| pip-audit | No known vulnerabilities; local non-PyPI package skipped as expected |
| PostgreSQL 17 integration | `3 passed`; migrations `0001` through `0004`; no Alembic drift |
| Focused Compose/config/startup contracts | `37 passed` |
| Playwright E2E | `10 passed in 30.37s` |
| Provider boundary | Live/provider cases deselected; no credential or provider call |
| API and dashboard image builds | Both built; API includes Psycopg `3.3.4`; both run as `evalforge` |
| Compose runtime | API and dashboard healthy; protected API returns `401` without bearer |

## Rendered UI proof

The in-app Browser was used first against owned loopback API and dashboard services, followed by the
repository Playwright suite.

- Desktop `1280 x 720`: Home rendered as a familiar work dashboard with no bootstrap overlay.
- Journey: New evaluation -> named run -> Check setup -> Start evaluation -> completed ten-result
  offline run -> Review results.
- Results: pass rate `90.0%`, mean quality `0.813`, P95 latency `180 ms`, known spend `$0.00`.
- The default redacted evidence package became downloadable.
- Selecting full stored content displayed the data-handling warning and disabled package, JSON, and
  CSV preparation while acknowledgement remained unchecked.
- Mobile `390 x 844`: collapsed navigation, `scrollWidth == innerWidth == 390`, and no horizontal
  overflow.
- No browser warning or error logs were observed during the final interactive proof.
- A dark-mode claim is not made because the app currently ships a fixed light Streamlit theme.

Screenshot evidence:

- `/private/tmp/evalforge-phase3-desktop-home.png` — SHA-256
  `69821d9a9070a0dd0cd1f60f6a1407f86959564aef8c5e189b7545a312440a83`
- `/private/tmp/evalforge-phase3-desktop-results.png` — SHA-256
  `4ab61430002cd1d5d92d6a7be0a0ab461efd95dab3effc67500b48451632798b`
- `/private/tmp/evalforge-phase3-mobile-home.png` — SHA-256
  `ab6344b70305c16cf65d8e3553dad8e534336cad288b6d5e9760cfe877ef63bb`

## Container, migration, and cleanup proof

- A disposable PostgreSQL `17.10-alpine` container applied all four revisions, reported no new
  upgrade operations, seeded two datasets/two prompts/three models, passed doctor, and ran all three
  PostgreSQL integration tests.
- Compose failed closed when a required OIDC variable was omitted.
- The API image imported Psycopg `3.3.4`; API and dashboard image users were both `evalforge`.
- The isolated Compose runtime reported API readiness as database and embedded worker ready, while
  the dashboard `_stcore/health` endpoint returned `ok`.
- The API healthcheck derived the public host and supplied the correct `Host` header; the protected
  metadata endpoint returned `401` without a bearer token.
- Owned native services, PostgreSQL container, Compose containers, volume, and network were stopped
  or removed. The locally built proof images remain available for inspection.

## Proof boundaries

Verified:

- local source, lint, formatting, types, security scan, dependency audit, and branch coverage;
- local SQLite migration, seed, doctor, deterministic evaluation, and downgrade safety;
- disposable PostgreSQL 17 migration, atomic claims, lease lifecycle, and database clock behavior;
- local OIDC token, role, cross-tenant, and disclosure behavior using signed or deterministic
  fixtures;
- desktop/mobile Browser rendering and repository Playwright E2E;
- container image builds and fail-closed Compose/startup contracts;
- provider consent, spend, request-count, retry, and no-credential live-test boundaries.

Not claimed:

- remote GitHub Actions execution or publication; this repository has no remote;
- hosted/public TLS, real IdP login/logout, production secrets, or deployment readback;
- deployed rate limiting, automated retention/deletion, storage encryption, backup/restore, or
  operational alerting;
- an actual paid provider or external judge call, human-reviewed calibration, or production score
  thresholds;
- exactly-once external provider billing;
- LangSmith, Weights & Biases, RAGAS, or DeepEval integration;
- dark-theme rendering.

## Remaining bounded work

No known P0 or P1 defect remains in the completed local Phase 3 scope. The final handoff inventories
the external gates: hosted OIDC/TLS deployment and authenticated readback, remote CI publication,
explicitly authorized provider and human calibration, production data-governance controls, and
optional tracking or evaluator integrations.
