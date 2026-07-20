# EvalForge

[![CI](https://github.com/ownasquare/evalforge/actions/workflows/ci.yml/badge.svg)](https://github.com/ownasquare/evalforge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-256078.svg)](LICENSE)
[![Release: v0.3.0 beta](https://img.shields.io/badge/release-v0.3.0%20beta-256078.svg)](https://github.com/ownasquare/evalforge/releases/tag/v0.3.0)

**Compare prompts and models against the same test set before you ship.**

EvalForge is a local-first evaluation dashboard for teams building with LLMs. It runs a shared set
of test cases across prompt and model candidates, then shows correctness, relevance, groundedness,
hallucination risk, speed, and cost in one reviewable result. The included demo is deterministic,
works offline, and needs no API key.

![EvalForge result review showing the outcome summary and candidate comparison](docs/assets/evalforge-results.png)

## Try it locally

You need Python 3.11 or 3.12, Git, and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/ownasquare/evalforge.git
cd evalforge
uv sync --frozen
uv run evalforge demo
```

Open `http://127.0.0.1:8501`. EvalForge creates a local database, installs sample data, and starts
the API and dashboard for you. Press `Ctrl+C` when you are done.

For a guided first run, see [Getting started](docs/getting-started.md).
For an immutable source snapshot and verified package artifacts, use the
[v0.3.0 public-beta release](https://github.com/ownasquare/evalforge/releases/tag/v0.3.0).

## Open source first, hosted pilot second

The complete credential-free workflow remains MIT-licensed and self-hostable. You can run it on
your own machine or infrastructure, keep your own persistence, and bring your own approved model
providers. The commercialization pilot does not remove, gate, or weaken that path.

EvalForge is also preparing an optional hosted team pilot for small AI engineering and product
teams that want a shared workspace without operating it themselves:

| Community self-hosted | Hosted team pilot |
| --- | --- |
| Free and open source | Invitation-based pilot |
| You operate the application and persistence | Managed persistence and workspace operations |
| Complete deterministic workflow and exports | Shared team access and pilot support |
| Available now through this repository | Begins with a pending qualification request; availability is not yet a production claim |

The first cohort does not use Stripe or collect a card. A team request is an expression of interest,
not checkout, payment, subscription activation, or proof that a hosted environment is live. See the
[hosted pilot offer](docs/commercialization/2026-07-20-hosted-pilot-offer-and-execution.md) and
[current proof boundaries](docs/commercialization/2026-07-20-hosted-blocker-audit.md).

## The core workflow

1. **Choose a test set.** Start with a sample benchmark or import JSON/CSV cases.
2. **Choose candidates.** Compare prompts and model profiles, and select the baseline.
3. **Review the result.** Find regressions, inspect the evidence, and export a review package.

Everything else supports that loop. Provider setup, shared-workspace controls, and operational
details stay out of the way until you need them.

### Optional: check a threshold against human labels

For a completed run, open **Human calibration** in Results to download a run-linked label template
and import a private review. EvalForge verifies every score against stored result evidence and keeps
only the derived report and hashes.

To work entirely outside the dashboard and application database, use the offline CLI:

```bash
uv run evalforge calibrate examples/calibration-labels.json --threshold 0.7 --output-dir ./private-calibration
```

See [Evaluation methodology](docs/evaluation-methodology.md#offline-threshold-calibration) for the
input contract, report meaning, and proof boundaries.

## What EvalForge measures

| Signal | What it helps answer |
| --- | --- |
| Correctness | Does the output match the expected answer? |
| Relevance | Does it address the question or configured keywords? |
| Groundedness | Are claims supported by the supplied context? |
| Hallucination risk | Does the output introduce unsupported facts, numbers, or links? |
| Constraints | Is the format, phrase, JSON, or style requirement satisfied? |
| Operations | What were the latency, token usage, and known estimated cost? |

Built-in quality scores are deterministic, explainable heuristics. They are useful for regression
checks and comparisons, not a replacement for calibrated human review. A metric is marked not
applicable when its required evidence is missing; EvalForge does not invent a score.

## Use your own data and models

- Open **Benchmarks** under **Library** to import a JSON or CSV test set.
- Add a prompt version and a model profile in the dashboard.
- Keep the default offline models while learning the workflow.
- Enable a real provider only after reviewing the data-transfer and spend controls in
  [Operations](docs/operations.md).

Real provider calls are disabled by default. Provider credentials stay in backend settings and are
never entered in the dashboard or stored in a run request.

## Extend the source

EvalForge has typed source-level contracts for model adapters, asynchronous evaluators, and export
sinks. It does **not** yet discover third-party plugins automatically; an extension must be wired
into a source build. See [Extending EvalForge](docs/extending.md) and the tested
[extension examples](examples/extensions/README.md).

## Documentation

- [Documentation index](docs/README.md)
- [Getting started](docs/getting-started.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Evaluation methodology](docs/evaluation-methodology.md)
- [Architecture](docs/architecture.md)
- [API contract](docs/api.md)
- [Operations and shared workspaces](docs/operations.md)
- [Security design](docs/security.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)

## Project status

EvalForge is beta software. The deterministic local workflow, SQLite and PostgreSQL persistence,
provider contracts, evidence exports, the desktop workflow, and key mobile layouts are covered by
automated tests.
The source now includes a narrow hosted-pilot contract for trials, workspace entitlements, team
requests, append-only commercial events, and an activation funnel. Hosted deployment, a specific
identity provider, managed-database recovery, external-user activation, and paid-provider behavior
still require separate environment-specific proof.

Licensed under the [MIT License](LICENSE). See the [changelog](CHANGELOG.md) for release notes.
