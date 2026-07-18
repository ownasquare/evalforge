# EvalForge open-source adoption completion

Date: 2026-07-18

## Outcome

EvalForge is ready to present as a public beta. A first-time user can install the project, launch a
complete offline workspace without a provider key, run a seeded evaluation, inspect the result,
compare candidates, and find a focused path for adding a provider or extension.

The public claim is deliberately narrower than “production SaaS.” Hosted identity, real-provider
calibration, production ingress, backup and restore, and provider billing behavior still require
proof in the environment where the project will be deployed.

## Core workflow

The dashboard now keeps the primary job visible:

1. Open the included offline workspace.
2. Choose a benchmark, prompt, baseline, and candidate model.
3. Review scoring and safety preflight, then start the evaluation.
4. Read the outcome, target misses, metric scorecard, and case evidence.
5. Compare candidates or export redacted evidence.

Models and benchmarks have their own focused library pages. Operational, audit, export, and
provider details use progressive disclosure so they remain available without crowding the core
workflow.

## Adoption improvements completed

- Added `evalforge demo`, `evalforge api`, and `evalforge ui` package commands.
- Made `evalforge demo` migrate, seed, start, supervise, and cleanly stop both local services.
- Kept the offline path deterministic and key-free while preserving explicit real-provider gates.
- Removed provider credentials from every dashboard launcher environment.
- Added a workflow-first README, getting-started guide, troubleshooting guide, extension guide,
  import schemas, copyable JSON and CSV examples, and a safe real-provider setup recipe.
- Added contribution, support, security, conduct, changelog, issue, and pull-request guidance.
- Added first-run guidance, grouped navigation, a Models page, clearer benchmark forms, explicit
  baseline selection, scoring validation, and outcome-first result presentation.
- Added complete run-history and case-evidence pagination, resource-specific empty states, paused
  model handling, form-draft preservation, identity-scoped state reset, and server readback after
  model changes.
- Added documented adapter, evaluator, and export-sink extension examples with executable contract
  tests.
- Excluded development tools from the default adopter install while retaining an explicit
  all-groups contributor setup.
- Replaced internal build-era material in the public tree with adopter-facing documentation and a
  representative results screenshot.

## Validation evidence

- Full quality gate: 359 tests passed, 3 PostgreSQL checks skipped because no local test URL was
  configured, and 14 separately marked browser/live tests were deselected by the default suite.
- Branch coverage: 84.22%, above the configured 80% threshold.
- Static checks: Ruff, formatting, mypy, Bandit, and dependency vulnerability audit passed.
- Browser acceptance: 13 of 13 Playwright end-to-end checks passed through the real
  Streamlit-to-FastAPI boundary.
- Rendered layout: no horizontal overflow at 1280 px or 390 px; a fresh browser tab produced no
  console errors or warnings; the primary comparison action rendered white text on the teal action
  color.
- Packaging: a clean wheel installed into a fresh environment, all command help surfaces worked,
  and the demo started on custom ports even when given a stale API URL override.
- Runtime behavior: both health endpoints returned ready, Streamlit usage reporting and file
  watching were disabled, `Ctrl+C` stopped both services, and both test ports were released.
- Documentation: all 46 local links across 15 public Markdown files resolved.
- Public contract: project layout, extension examples, import examples, documentation, and
  generated-file exclusions are regression-tested.

## Current release boundary

The following are publish or deployment actions, not unfinished local product behavior:

- Enable GitHub private vulnerability reporting before announcing the repository.
- Add a Git remote and publish the reviewed commit and release tag.
- Run the PostgreSQL job in CI or against an authorized local PostgreSQL URL.
- Validate a real OIDC login, logout, role, and workspace journey in the hosted environment.
- Perform an explicitly authorized provider calibration run before using scores as release gates.
- Prove hosted TLS, storage, backup and restore, monitoring, retention, and production readback.

## Recommended release label

Publish as `v0.1.0` public beta: a complete local LLM evaluation workbench with production-minded
contracts, not a claim that every hosted deployment has already been certified.
