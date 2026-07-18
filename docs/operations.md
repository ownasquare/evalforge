# Operations

## Local development

```bash
cp .env.example .env
uv sync --all-groups
uv run alembic upgrade head
uv run evalforge seed
make api
```

Start `make ui` in a second terminal. Both commands bind to loopback by default. `evalforge doctor`
checks configuration, database connectivity, provider readiness, and execution limits without
printing secrets.

## Database lifecycle

Alembic is the schema authority:

```bash
uv run alembic current
uv run alembic upgrade head
```

Do not use `metadata.create_all()` as a substitute for migrations outside isolated tests. Back up
the database only after stopping the single API/worker process or using SQLite's online backup API.
Keep the database and its WAL files on a local filesystem.

Revision `0002_preflight_context_cost_ack` uses additive DDL and can resume after an interrupted
SQLite attempt that left an Alembic batch table or only some new columns. Regression tests cover a
populated 0001 database with enforced foreign keys; operators should still take a backup before any
schema change.

The default path is `.data/evalforge.db`. The directory is ignored by Git. Dataset and run exports
are portable artifacts; they contain prompts and model outputs and should be handled as potentially
sensitive user data.

For a single-worker PostgreSQL deployment, install the optional driver and use its explicit
SQLAlchemy URL form:

```bash
uv sync --all-groups --extra postgres
# EVALFORGE_DATABASE_URL=postgresql+psycopg://user:password@host/database
uv run evalforge seed
uv run evalforge doctor
```

Bare `postgresql://` URLs are rejected so a configuration cannot silently depend on an uninstalled
or unintended driver. CI boots PostgreSQL, applies the packaged migration, seeds it, and performs a
readiness check; that does not claim local or hosted production validation.

## Health and recovery

- `/health/live` proves the API process can answer.
- `/health/ready` proves database/migration readiness and a live local worker task.
- `/_stcore/health` proves Streamlit process health.
- `/api/v1/capabilities` reports safe provider and limit state.

On API startup, a run left in `running` is marked `interrupted`. Completed case results remain. If a
provider response was committed before scoring or shutdown, its output, request ID, usage, latency,
and known cost remain visible. A request with no committed response is labeled billing-ambiguous
and is not repeated automatically. Create a new run deliberately after reviewing that evidence.

## Deterministic demo

`evalforge seed` is idempotent. It installs curated support, grounded QA, structured output, and
constraint cases plus explainably different demo model profiles. Demo generation, usage, latency,
and errors are SHA-256-derived and labeled synthetic. No network call is made.

Enter the dashboard at `http://127.0.0.1:8501/` and use its navigation. This root-first entry is the
deterministic browser-test contract because Streamlit registers dynamic page routes during the
initial session bootstrap.

## Real-provider checklist

1. Leave real calls disabled while preparing datasets, prompts, and thresholds.
2. Set the backend key and model allowlist in a non-committed environment file.
3. Choose the explicit API mode supported by that provider.
4. Set `EVALFORGE_REAL_RUNS_ENABLED=true`.
5. Review preflight case, variant, call, token, known/unknown price, and applicability counts.
6. Confirm the dashboard acknowledgment immediately before submission.
7. Preserve the request ID and run export when investigating provider behavior.

Never test a provider by adding a key to source code, a dashboard field, a URL, a shell command, or a
support log.

## Containers

`compose.yaml` runs separate API and dashboard containers. Only the one-worker API receives the
SQLite volume and provider environment. Services publish loopback ports, run as a non-root user,
drop Linux capabilities, enable `no-new-privileges`, and use a read-only root filesystem with a
bounded temporary filesystem.

```bash
docker compose up --build -d
docker compose ps
docker compose down
```

The completion record includes a local Docker Compose build and runtime check with healthy API and
dashboard containers, seeded readback, one completed deterministic matrix, and verified hardening
flags. This remains local container proof; it is not hosted or production validation.

## Production evolution

Before multi-user or network exposure, add authentication/authorization, TLS at the ingress, tenant
isolation, encrypted secret management, retention policy, and an abuse/rate-limit layer. Before
horizontal workers, migrate to PostgreSQL and a durable queue/lease, add idempotent claim semantics,
and prove restart/cancellation behavior under concurrency. Hosted and production validation require
their own evidence records.
