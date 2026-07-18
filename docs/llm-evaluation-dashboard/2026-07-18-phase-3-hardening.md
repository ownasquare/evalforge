# EvalForge Phase 3 Hardening and Shared-Workspace Readiness

Date: 2026-07-18
Repository: `/Users/fortunevieyra/Documents/Github/ai-projects/llm-evaluation-dashboard`
Implementation commit: `TO_BE_RECORDED_AFTER_COMMIT`

## Outcome

Phase 3 closes the bounded next steps from the Phase 2 handoff. EvalForge now has a real local/OIDC
identity boundary, tenant-scoped persistence, ordered workspace roles, durable database-backed run
leases, PostgreSQL lifecycle proof, provider transfer and spend safeguards, offline calibration
contracts, and portable versioned evidence packages. Cold direct routes work, Viewer screens remove
mutation affordances, and the interface retains the restrained work-app language established in
Phase 2.

The result is a substantially stronger shared-workspace source foundation. It is not a claim of a
hosted service, production TLS/IdP readback, paid-provider accuracy, or exactly-once external
billing.

## Identity, tenancy, and startup

- Added deterministic loopback-only local identity and OIDC JWT authentication.
- OIDC validates an exact HTTPS issuer and audience, RS256/ES256 signature, subject, expiration,
  bounded clock skew, and a bounded cached JWKS resolver.
- Added active users, workspaces, memberships, Viewer/Editor/Admin/Owner roles, and application
  append-only audit events.
- Scoped datasets, cases, prompts, model profiles, runs, candidates, results, attempts, analytics,
  comparisons, exports, idempotency, and dashboard caches to one workspace.
- Added operator CLI commands to create workspaces and provision or revoke memberships without
  erasing history.
- Added validated API and dashboard startup wrappers. Local auth cannot bind beyond loopback.
- Non-test OIDC requires HTTPS issuer, JWKS, public base, and dashboard API URLs.
- Compose requires trusted public hosts and a secret-mounted Streamlit OAuth TOML. The dashboard
  launcher validates callback, cookie secret, named provider, metadata URL, and
  `expose_tokens = ["access"]` before binding.

## Durable execution and PostgreSQL

- Added persisted execution attempts plus atomic database claims with owner, random token, fencing
  epoch, expiry, and heartbeat.
- Added `embedded_single`, `api_only`, and `database_worker` modes.
- The database row is queue authority; workers discover committed work instead of relying on a
  process-local queue.
- Every worker mutation is fenced. Lost heartbeats cancel in-flight work and stop further writes.
  Billing-ambiguous leases remain until expiry rather than being released for immediate replay.
- API-only readiness reports `external_unobserved` instead of inventing worker health.
- New run eligibility uses the database clock, preventing host/PostgreSQL clock skew from delaying
  claims.
- SQLite remains the supported one-process embedded topology. PostgreSQL is the supported
  multi-process coordination path; neither topology can promise exactly-once external billing.

## Provider, evaluator, and export safeguards

- Real generation requires separate external-transfer, real-cost, and unknown-cost consent as
  applicable, plus a positive user spend ceiling under the server cap.
- Preflight shows planned logical calls, maximum provider request count, known/unknown pricing, and
  zero automatic provider retries.
- Generic billable generation makes exactly one network attempt, including HTTP 429; no compatible
  gateway is assumed to reject before upstream generation.
- Added provider-neutral offline/external evaluator declarations covering calls, cost behavior,
  transmitted fields, and metric identity.
- Added deterministic offline threshold calibration with confusion matrix, precision, recall, F1,
  and calibration-set SHA-256. It remains a library primitive and has not been human-calibrated.
- Added `evalforge.run-export.v1`, canonical payload hashing, API response hash, explicit
  `content_redacted` / `full_evidence` profiles, and a private idempotent local file sink.
- The redacted profile is a strict safe-provenance allowlist. Unknown fields are omitted, and a full
  realistic sentinel test proves benchmark, identity, prompt, output, provider, and evidence content
  cannot leak through the default package.

## Product and UI changes

- Fixed cold direct links for all six Streamlit routes through the neutral top-level launcher.
- Added sign-in, sign-out, workspace selection, role display, and identity-aware cache invalidation.
- Viewer mode keeps evidence review available while hiding create, import, edit, run, and cancel
  actions.
- Added plain-language transfer and budget controls to preflight.
- Evidence packages default to content-redacted. Full evidence shows a warning and remains disabled
  until the user separately confirms disclosure.
- Preserved the off-white canvas, neutral typography, restrained teal accent, compact spacing, and
  progressive disclosure of technical evidence.

## Migration safety

- Revision `0003_identity_tenant_scope` backfills Phase 2 rows into the stable local workspace.
- Its downgrade refuses to discard nonlocal identity or audit history and is tested for an exact
  populated-`0002` schema/data round trip.
- Revision `0004_durable_execution_leases` adds claim and attempt evidence.
- SQLite Alembic batch migration uses a private connection, temporarily suspends foreign keys only
  for reconstruction, validates `foreign_key_check`, and restores enforcement.
- PostgreSQL avoids SQLite-only batch recreation and uses explicit `postgresql+psycopg` URLs.

## Automated validation

| Proof layer | Final result |
|---|---|
| Offline pytest with sockets disabled | `TO_BE_RECORDED` |
| Branch coverage | `TO_BE_RECORDED` (required floor `80%`) |
| Ruff lint / format | All checks passed / `111 files already formatted` |
| mypy strict | Success across `67` source files |
| Bandit | Exit `0`, no findings emitted |
| pip-audit | No known vulnerabilities; local non-PyPI package skipped as expected |
| PostgreSQL 17 migration/lease/lifecycle | `TO_BE_RECORDED` |
| Live-provider boundary | `TO_BE_RECORDED`; no credential read or provider client created |
| Playwright E2E | `TO_BE_RECORDED` |
| API and dashboard image builds | `TO_BE_RECORDED` |
| Compose valid / missing-required-variable checks | `TO_BE_RECORDED` |
| Git whitespace | Clean |

## Rendered UI proof

The in-app Browser was used first against owned loopback API/dashboard services, followed by the
repository Playwright suite.

- Desktop: `1440 x 900`; complete Home -> New evaluation -> Check setup -> Start -> completed ->
  Runs journey.
- Mobile: `390 x 844`; collapsed navigation, `scrollWidth == clientWidth == 390`, no horizontal
  overflow.
- Redacted package was the default and became downloadable.
- Full evidence displayed a warning and remained disabled until its explicit checkbox was selected.
- Settings copy matched local identity and external-provider boundaries.
- No browser warning/error logs were observed in the final interactive proof.
- A separate dark-mode claim is not made because the app currently ships a fixed light Streamlit
  theme.

## Container, migration, and cleanup proof

- Fresh SQLite upgraded to Alembic head, reported no new upgrade operations, seeded deterministic
  records, and passed doctor/readiness.
- A disposable PostgreSQL 17 container exercised real migration, lifecycle, and lease behavior and
  was removed afterward.
- API and dashboard proof images were built and removed after validation.
- Compose accepts a complete nonsecret OIDC/trusted-host/Streamlit-secret configuration and fails
  before startup when required values are missing.
- Owned API/dashboard services and temporary test directories were stopped/removed; their proof
  ports had no remaining listeners.

## Proof boundaries

Verified:

- local source, static analysis, security scan, dependency audit, and branch coverage;
- local SQLite migration, seed, doctor, deterministic evaluation, and downgrade safety;
- local disposable PostgreSQL 17 migration, atomic claims, lease lifecycle, and database clock;
- local OIDC token/role/cross-tenant behavior using signed fixtures;
- local Browser desktop/mobile rendering and Playwright E2E;
- container image builds and fail-closed Compose/startup contracts;
- provider consent, spend, request-count, retry, and no-credential live-test boundaries.

Not claimed:

- remote GitHub Actions execution or publication; this repository has no remote;
- hosted/public TLS, real IdP login/logout, production secrets, or authenticated deployment readback;
- rate limiting, automated retention/deletion, storage encryption, backup/restore, or operational
  alerting in a deployed environment;
- an actual paid provider or external judge call, human-reviewed calibration, or production score
  thresholds;
- exactly-once external provider billing;
- LangSmith, Weights & Biases, RAGAS, or DeepEval integration;
- fixed dark-theme rendering.

## Remaining bounded work

There is no known P0 or P1 defect in the completed local Phase 3 scope after the adversarial review
and remediation. The final handoff inventories the remaining externally gated work: a hosted
OIDC/TLS deployment and policy readback, remote CI publication, explicitly authorized provider and
human calibration, production retention/rate-limit/encryption/backup controls, and optional tracking
or evaluator integrations.
