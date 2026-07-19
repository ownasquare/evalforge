# Persisted calibration evidence completion record

Date: 2026-07-19  
Release target: v0.3.0  
Pull request: [#9](https://github.com/ownasquare/evalforge/pull/9)

## Outcome

EvalForge now turns completed human labels into durable, run-linked calibration evidence. A reviewer can download a case-ordered CSV or JSON template, add anonymous labels, import it for one candidate and metric, and return later to the same aggregate report.

The workflow remains deliberately narrow: calibration is optional within Results, reports are append-only, identical imports are idempotent, raw labels and reviewer identifiers are not stored, and the UI never presents offline evidence as production validation.

## Why this changed

The earlier calibration command proved the statistical calculation, but it did not give contributors or operators a durable evidence trail. This phase closes that adoption gap without adding a second review product or cluttering the primary evaluation workflow.

## Affected surfaces

- FastAPI template, import, list, and detail routes under `/api/v1/runs/{run_id}/calibrations`.
- SQLite and PostgreSQL-compatible persistence plus Alembic revision `0005_calibration_reports`.
- Result-page calibration tools, aggregate report cards, privacy guidance, and compact help text.
- CSV/JSON parsing, canonicalization, formula-safe exports, report integrity validation, and idempotency.
- Public installation, API, architecture, methodology, operations, security, support, issue-template, and v0.3.0 release documentation.
- Contract, unit, integration, authorization, tenant-isolation, migration, dashboard, and browser tests.

## Evidence contract

- Every report is linked to one completed run, candidate, dataset snapshot, metric version, direction, and selected threshold.
- The service verifies every submitted row against immutable stored result identifiers, scores, case order, and dataset hashes.
- Canonical hashes cover the accepted label manifest and persisted aggregate report.
- Raw human labels and reviewer identifiers are used only for in-memory validation and aggregation.
- Persisted privacy fields are exact and reports cannot be edited, deleted, approved, or promoted into scoring thresholds.
- `evidence_kind` is `offline_statistical_evidence` and `production_validated` is always `false` in this release.

## Validation Environment

- Local macOS workspace using Python 3.11 and the locked dependency graph.
- SQLite for the real dashboard workflow and persistence readback.
- FastAPI and Streamlit started through the package-owned `evalforge demo` launcher.
- Browser proof at 1440 × 1000 and 390 × 844.
- Clean wheel installation in a new temporary virtual environment.

## Validation Scope

- `make check`: lint, format check, mypy, Bandit, dependency audit, and the non-E2E test suite.
- Result: 431 passed, 3 skipped, 14 deselected; branch coverage 84.17%, above the 80% gate.
- Playwright E2E suite: 13 passed.
- Clean source and wheel build succeeded; the wheel installed as `evalforge-dashboard==0.3.0`, imported as version `0.3.0`, and exposed the documented CLI.
- Real workflow proof: created a 10-result offline comparison, downloaded a five-row template, imported completed labels, replayed the identical import, reloaded Results, and read the persisted report.
- Idempotency proof: the first import returned `created`; the identical replay returned `already_exists` with the same report ID and hashes.
- Browser proof: desktop and mobile layouts rendered without clipping the primary actions; the mobile report card stacked cleanly; console warning/error readback was empty.
- Protected PR CI then passed the same release candidate against live PostgreSQL 3.11 and 3.12 service containers, both production Dockerfiles, E2E, and the quality/security suite.

## Data Integrity Classification

Run-linked, hash-verifiable, append-only aggregate evidence. The proof data was generated from deterministic offline fixtures and does not establish live-provider quality or production correctness.

## Mock/Fixture Usage

The browser and E2E proof used EvalForge's documented deterministic demo models and seeded support benchmark. No provider request, API key, billable call, or production dataset was used. Synthetic latency, token, and cost values remain labeled as such.

## Production Validation Status

Not production validated. Live OIDC, authorized provider evaluation, and a production deployment remain separate external gates tracked in [#2](https://github.com/ownasquare/evalforge/issues/2), [#3](https://github.com/ownasquare/evalforge/issues/3), and [#4](https://github.com/ownasquare/evalforge/issues/4). Live PostgreSQL compatibility is covered by protected GitHub CI, but that is database compatibility proof rather than production deployment proof.

## Localhost Validation Integrity

The browser used the real Streamlit-to-FastAPI boundary and a real SQLite database, not a mocked browser response. The persisted report was read after a full page reload. This is strong local application proof, but it is intentionally not described as hosted or production proof.

## Warning/Issue Triage

- An initial full-suite run inherited temporary proof ports from a local `.env`, causing two launcher assertions to expect 8000 while receiving 8010. The temporary file was removed and the clean rerun passed all 431 applicable tests.
- The dependency audit reported no known vulnerabilities; it correctly noted that the local, unpublished `evalforge-dashboard` package itself is not a PyPI dependency it can audit.
- Three live PostgreSQL tests were skipped locally because `EVALFORGE_POSTGRES_TEST_URL` was not configured. PostgreSQL DDL compilation and migration-focused tests passed locally; protected PR and main CI later passed the live suite on PostgreSQL 3.11 and 3.12.

## Warning Suppression Status

No new warning suppression was added. Existing narrowly documented test annotations remain unchanged in purpose. Browser console warnings and errors were read directly and were empty.

## Commit Evidence

- Feature commit: `3d6f971` (`feat: persist human calibration evidence`).
- Independent final review: no remaining actionable findings; its focused pass covered 123 tests plus Ruff, mypy, diff checks, and PostgreSQL DDL compilation.

## Push Evidence

- Branch: `agent/persisted-calibration-evidence`.
- Remote: `ownasquare/evalforge`.
- Draft pull request: [#9](https://github.com/ownasquare/evalforge/pull/9).
- Protected-main commit: `abf00360dabccebfb1dd6f2abe8fe91fba8f1c17`.
- Immutable prerelease verification: [v0.3.0 release record](./2026-07-19-v0.3.0-release.md).

## Extra Mile Improvements

- Replaced multipart upload handling with a bounded streamed request body so large calibration input cannot spill into Starlette temporary files.
- Added strict content-type matching, explicit OpenAPI request-body metadata, CORS-exposed evidence headers, and a 2 MiB input ceiling.
- Normalized signed zero, escaped formula-shaped CSV display values, and preserved JSON/CSV hash equivalence.
- Added full persisted-report self-consistency checks covering canonical payload hashes, privacy shape, counts, confusion matrices, and rounded rates.
- Added a compact docs index and question template so adopters can find the right extension point without scanning the repository.

## Follow-up boundaries

The release does not add calibration approval workflows, threshold promotion, raw-label storage, provider benchmarking, hosted infrastructure, or organization provisioning. Those would materially change the trust model and require separate design and validation.
