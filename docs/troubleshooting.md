# Troubleshooting

Most local problems are dependency, port, or database-readiness issues. The offline demo never
needs a provider key.

## The `uv` command is missing

Install [uv](https://docs.astral.sh/uv/) using its official instructions, then run:

```bash
uv sync --frozen
```

## Python has the wrong version

EvalForge supports Python 3.11 and 3.12. Ask uv to use an installed supported version:

```bash
uv python pin 3.12
uv sync --frozen
```

## A dependency or lock-file error appears

Use the committed lock file without changing it:

```bash
uv sync --frozen
```

Contributors who intentionally change dependencies should update and review `uv.lock` in the same
pull request. A normal install should not regenerate it.

## The dashboard says the API is unavailable

When using the one-command demo, return to its terminal and read the first startup error. Check local
readiness without exposing credentials:

```bash
uv run evalforge doctor
```

If you started services separately, confirm the API is on `http://127.0.0.1:8000` and the dashboard
uses that same origin.

## Port 8000 or 8501 is already in use

Stop the other local service or set unused loopback ports before starting EvalForge. Keep API and
dashboard configuration in sync. The available settings are documented in [Operations](operations.md).

## There are no benchmarks, prompts, or models

Install the idempotent offline sample data:

```bash
uv run evalforge seed
```

This also applies pending migrations. It is safe to run again in the same local workspace.

## A score is unavailable

This is usually expected. Correctness needs a reference answer; groundedness and hallucination risk
need source context or factual reference evidence. Add the missing evidence to the test case or leave
the metric unavailable. See [Evaluation methodology](evaluation-methodology.md).

## A real-provider run is blocked

Real calls fail closed until the backend has an environment-only credential, an allowed model,
explicit real-run enablement, data-transfer acknowledgement, and spend controls. Unknown pricing
requires an additional acknowledgement. Follow [Operations](operations.md) and do not put a secret
in the dashboard, issue, log, or benchmark.

## The problem remains

Read [Support](../SUPPORT.md), search existing issues, and open the matching issue template with a
minimal reproduction and sanitized output.
