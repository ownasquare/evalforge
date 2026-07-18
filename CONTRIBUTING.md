# Contributing

## Setup

Use Python 3.11 or 3.12 and uv:

```bash
uv sync --all-groups
uv run alembic upgrade head
uv run evalforge seed
```

## Development contract

- Keep Streamlit an API-only client.
- Keep provider credentials in backend settings and never in requests or fixtures.
- Add new end-to-end coverage with Playwright; use Streamlit AppTest for page/component behavior.
- Add a metric version whenever its math, normalization, threshold semantics, direction, or evidence
  shape changes.
- Return `not_applicable` for missing evidence instead of inventing a score.
- Preserve immutable snapshots and migration compatibility.
- Keep live/paid tests explicitly marked and excluded from default CI.

The 80% branch-coverage gate targets the API, persistence, evaluation engine, provider adapters,
dashboard client/state/components, and CLI. Streamlit page-rendering modules are omitted from the
coverage denominator because they are exercised through Streamlit AppTest and browser interaction
proof instead of line coverage.

## Before opening a change

```bash
make check
```

Add or update documentation for API, metric, security, operating, and proof-boundary changes. A
rendered UI change also needs desktop and mobile browser evidence with console and interaction checks.
The dedicated CI E2E job starts the real API and Streamlit processes, then runs the seeded
Playwright smoke. Keep it deterministic and credential-free.
