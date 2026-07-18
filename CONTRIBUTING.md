# Contributing to EvalForge

Thanks for helping make LLM evaluation easier to trust and easier to use. Small, focused changes
are welcome, including documentation fixes, test cases, metrics, provider adapters, accessibility
improvements, and workflow simplification.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## Set up a development environment

Use Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --frozen --all-groups
uv run evalforge seed
uv run evalforge doctor
```

Run the API and dashboard together with:

```bash
uv run evalforge demo
```

Install the optional browser dependencies only when you need end-to-end tests:

```bash
uv sync --frozen --all-groups --extra e2e
uv run --all-groups --extra e2e playwright install chromium
```

## Pick the right place for a change

- Dashboard pages and components live in `src/evalforge/dashboard/`.
- API routes live in `src/evalforge/api/`; business rules belong in services and repositories.
- Deterministic metrics live in `src/evalforge/evaluation/metrics.py`.
- Provider adapters live in `src/evalforge/evaluation/adapters/`.
- Database changes require a migration under `src/evalforge/migrations/versions/`.
- Public behavior belongs in `docs/`, not in internal execution notes.

The [extension guide](docs/extending.md) explains the current adapter, evaluator, and export seams.
These are source-level integration points, not automatically discovered plugins.

## Development rules

- Keep Streamlit as an API-only client; it must not open the database or receive provider secrets.
- Keep provider credentials in backend settings and out of requests, fixtures, logs, and issues.
- Return `not_applicable` when evidence is missing instead of inventing a score.
- Change a metric version when its math, direction, threshold meaning, or evidence shape changes.
- Preserve immutable run snapshots and migration compatibility.
- Keep live or paid tests explicitly marked and excluded from the default test run.
- Use Streamlit AppTest for page behavior and Playwright for end-to-end browser journeys.
- Keep the main workflow calm: advanced controls should use progressive disclosure and short help
  text rather than permanent explanatory panels.

## Test your change

Run the narrowest relevant test while developing, then run the full local gate:

```bash
make check
```

The default suite is deterministic and does not call a paid provider. A visible UI change also
needs a desktop and mobile browser check with no console errors or horizontal overflow.

## Open a pull request

Keep each pull request focused and include:

- the user problem and the chosen behavior;
- tests or a clear reason tests are not needed;
- documentation for user-visible, API, metric, security, or operating changes;
- screenshots for meaningful UI changes; and
- an explicit note when validation was local-only or excluded paid-provider/hosted proof.

Use the pull request checklist as a final review. Never include secrets, private evaluation data,
machine-specific paths, generated handoffs, or temporary proof files.
