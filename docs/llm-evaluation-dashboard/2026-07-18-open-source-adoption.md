# EvalForge open-source adoption completion

Date: 2026-07-18

## Outcome

EvalForge is ready to present as a public beta. A first-time user can install the project, launch a
complete offline workspace without a provider key, run a seeded evaluation, inspect the result,
compare candidates, and find a focused path for adding a provider or extension.

The public claim is deliberately narrower than “production SaaS.” Hosted identity, real-provider
calibration, production ingress, backup and restore, and provider billing behavior still require
proof in the environment where the project will be deployed.

## Publication

- Public repository: [ownasquare/evalforge](https://github.com/ownasquare/evalforge)
- Default branch: `main`
- Initial published release commit: `04a9d669644cf97b7a1835f4097c90abff49172b`
- Hosted-verified product commit: `f3e490110d5598e1761eaf6de87144edcfce3cb6`
- Repository visibility: public, with Issues enabled and the Wiki disabled
- Security intake: GitHub private vulnerability reporting enabled
- Repository topics: `ai-evaluation`, `fastapi`, `llm`, `llm-evaluation`,
  `prompt-engineering`, `python`, `rag`, and `streamlit`

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
- Hosted publication checks: the follow-up
  [GitHub Actions run](https://github.com/ownasquare/evalforge/actions/runs/29664399429) passed every
  job at commit `f3e490110d5598e1761eaf6de87144edcfce3cb6`: PostgreSQL 3.11 and 3.12,
  Playwright E2E, both container builds, Ruff, formatting, mypy, Bandit, the full test and coverage
  command, and the dependency audit. The first hosted run exposed terminal-rendering-sensitive CLI
  assertions; the follow-up strips styling and normalizes wrapping before asserting output.
- Browser acceptance: 13 of 13 Playwright end-to-end checks passed through the real
  Streamlit-to-FastAPI boundary.
- Rendered layout: no horizontal overflow at 1280 px or 390 px; a fresh browser tab produced no
  console errors or warnings; the primary comparison action rendered white text on the teal action
  color.
- Packaging: a clean wheel installed into a fresh environment, all command help surfaces worked,
  and the demo started on custom ports even when given a stale API URL override.
- Runtime behavior: both health endpoints returned ready, Streamlit usage reporting and file
  watching were disabled, `Ctrl+C` stopped both services, and both test ports were released.
- Documentation: all 46 local links across 16 public Markdown files resolved.
- Public contract: project layout, extension examples, import examples, documentation, and
  generated-file exclusions are regression-tested.

## Remaining environment-specific validation

The following are deployment actions, not unfinished local product behavior:

- Validate a real OIDC login, logout, role, and workspace journey in the hosted environment.
- Perform an explicitly authorized provider calibration run before using scores as release gates.
- Prove hosted TLS, storage, backup and restore, monitoring, retention, and production readback.

## Recommended release label

The repository is published as a public beta. When a formal GitHub release is desired, use
`v0.1.0`: a complete local LLM evaluation workbench with production-minded contracts, not a claim
that every hosted deployment has already been certified.
