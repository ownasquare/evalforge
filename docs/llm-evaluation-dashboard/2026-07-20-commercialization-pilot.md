# EvalForge commercialization pilot completion record

Date: 2026-07-20
Status: **Source implementation and local proof complete; hosted and market proof remain open**

## Outcome

EvalForge is now the primary Own A Square commercialization pilot, with Dataset Foundry retained as
the documented backup. The repository contains the smallest hosted-team bridge around the existing
open-source evaluation workflow: server-authoritative entitlements, pending team-pilot requests,
commercial event evidence, protected readback, hosted deployment configuration, a local captioned
demo, and opt-in hosted acceptance tests. The useful deterministic self-hosted flow remains free,
credential-free, and ungated.

The 1,000-app mission remains the north star. It is not the current build backlog; this 14-day pilot
is the active roadmap.

## Completed implementation

- Added the [15-candidate weighted decision](../commercialization/2026-07-20-pilot-decision.md),
  locked [offer and 14-day execution record](../commercialization/2026-07-20-hosted-pilot-offer-and-execution.md),
  [five-blocker audit](../commercialization/2026-07-20-hosted-blocker-audit.md),
  [buyer-discovery tracker](../commercialization/2026-07-20-buyer-discovery.md), and
  [launch/Day-14 record](../commercialization/2026-07-20-launch-and-day-14-decision.md).
- Added workspace entitlements, append-only billing receipts and activation events, one-pending
  team-request enforcement, safe first-touch acquisition attribution, bounded event history, and
  server-owned funnel readback.
- Added trial start/cancel, request submit/cancel, plan/readback, billing history, event ingestion,
  and protected metrics API contracts while preserving the existing run/result/export contracts.
- Gated hosted run admission by authoritative entitlement state while leaving local self-hosting
  unchanged. PostgreSQL workspace locks serialize run admission, cancellation, trial transitions,
  and supported membership mutations.
- Counted activation only when the same actor completes a run with successful evidence for at least
  two candidates and then exports that run. Early export, viewer export, canceled/all-error runs,
  single-candidate runs, and client-authored run linkage do not activate the team-request path.
- Added migration `0006_commercial_pilot` with evidence-preserving, target-aware downgrade guards.
- Added an OSS-first Settings surface for plan comparison, server readback, trial/request controls,
  and honest no-live-money language.
- Added a fail-closed Render reference topology for API, dashboard, database worker, and PostgreSQL;
  API pre-deploy is the sole migration authority.
- Hardened dashboard startup so provider, metrics, database, and OIDC secrets are not inherited by
  Streamlit; temporary OIDC configuration is mode `0600` and removed on exit.
- Added opt-in [hosted Playwright acceptance](../../tests/e2e/test_hosted_commercial_pilot.py) for
  real OIDC callback/logout, two-tenant denial, qualifying evaluation/export activation, commercial
  readback/cancellation, and mobile Settings. It requires caller-owned HTTPS and private identity
  fixtures and therefore makes no local hosted claim.
- Applied mechanical import ordering to seven clean baseline files so the repository-wide CI lint
  command is green.

## Local launch evidence

The [captioned local OSS demo](../commercialization/2026-07-20-local-demo-proof.md) records one real
browser journey:

- one prompt × two model profiles × five shared cases;
- 10 of 10 persisted results;
- evidence inspection plus an observed JSON download;
- one server-read `activated_run`;
- 47.863 seconds from the first run event to local result engagement;
- desktop 1440×1000 and mobile 390×844 proof; and
- zero browser-console warnings or errors.

This is local deterministic evidence. It is not hosted signup-to-activation, customer outcome,
live-provider latency/cost, or production proof.

## Validation

| Boundary | Result |
|---|---|
| Full deterministic suite with coverage | 460 passed, 4 PostgreSQL skips, 17 live/E2E deselections; 85.03% branch-aware coverage |
| Local Playwright E2E | 13 passed |
| Hosted Playwright specification without private fixtures | 3 explicit skips with each missing input named |
| Commercial + tenant-route regression | 18 passed after the final route/service boundary repair |
| Ruff full tree and format check | Passed; 138 files already formatted |
| Strict mypy | Passed; 76 source files |
| Configured Bandit scan | Passed |
| Dependency audit | No known third-party vulnerabilities; the local package is not published on the package index and is skipped by the auditor |
| Patch integrity | `git diff --check` passed |
| Browser console | 0 warnings, 0 errors |
| Pull-request CI | All six jobs passed on run `29768508446`: quality, browser E2E, PostgreSQL 3.11, PostgreSQL 3.12, API image, and dashboard image |

## Commit and push evidence

- Validated implementation commit: `73d27f77cd097040b35649b1f36b75edf391b107` (`Add EvalForge commercialization pilot`).
- Publication-evidence commit: `10b8db80c2d7f37805cc5acfd56f62d850afba89` (`Record commercialization publication evidence`).
- CI portability commit: `247358c52a52985a25b52ae55c157477a28295e9` (`Stabilize Ruff Alembic import classification`).
- Remote: `origin` (`ownasquare/evalforge`).
- Published branch: `agent/own-a-square-commercialization-pilot`.
- Draft review: [pull request #11](https://github.com/ownasquare/evalforge/pull/11), targeting `main`.
- The CI-validated source head matched `247358c52a52985a25b52ae55c157477a28295e9`.
- [GitHub Actions run 29768508446](https://github.com/ownasquare/evalforge/actions/runs/29768508446)
  completed successfully with all six jobs green.

Four local PostgreSQL-specific cases require an explicit disposable PostgreSQL test URL and remained
truthfully skipped in the local full suite. Separately, the pull request's PostgreSQL 3.11 and 3.12
jobs passed against their ephemeral CI databases. The hosted Playwright tests require a deployed HTTPS dashboard/API, two live identities,
two workspaces, private token files, separate authenticated-app and IdP-only browser states, and an
explicitly disposable mutable primary workspace.

## Proof and authorization boundaries

| Boundary | Status |
|---|---|
| Local OSS workflow | Passed in tests, Playwright, and real browser proof |
| Local commercial contracts | Passed in API, database, migration, dashboard, and contract tests |
| Hosted deployment / TLS | Open; reference topology only |
| Live OIDC callback and hosted tenant isolation | Open; opt-in acceptance test exists but was not run against a provider |
| Managed PostgreSQL, worker recovery, backup/restore | Open |
| Pending team-request path | Implemented and locally validated; no qualified disposition or commitment is claimed |
| Live checkout/payment | Not selected for the first cohort and not claimed |
| Public Own A Square profile update | Blocked; current CLI authentication lacks profile-write scope and the browser session was signed out |
| Buyer outreach | 0 contacts, 0 messages, 0 conversations; no recipient list/channel authority was supplied |
| External activation and second use | 0 proven; internal/local accounts are excluded |
| Day-14 decision | Unselected until real distribution evidence exists |

## Remaining pilot work

1. Provision one hosted HTTPS environment and managed PostgreSQL database from the reviewed topology.
2. Configure a real identity provider, two pilot workspaces, and the private acceptance fixtures;
   then run and retain the hosted Playwright evidence.
3. Prove worker restart persistence, lease recovery, backup/restore, alerting, and rollback separately.
4. Use an authorized GitHub profile session to apply the approved EvalForge-centered bio and read it
   back publicly.
5. Supply or approve ten targeted buyer contacts/channels, conduct five conversations, and record
   real pain, security, hosted-interest, seat, and spend evidence.
6. Run the 14-day distribution/activation experiment and choose Kill, Continue, or Scale from the
   preregistered thresholds.

Deferred scope remains unchanged: no new micro-app, All Access, universal billing, CRDT, mobile app,
browser extension, mass-branding program, or portfolio-wide platform during this pilot.
