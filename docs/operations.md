# Operations

## Local development

```bash
cp .env.example .env
uv sync --all-groups
uv run alembic upgrade head
uv run evalforge seed
make api
```

Start `make ui` in a second terminal. The native launchers load and validate all settings before
binding; local identity mode is rejected if either service is configured beyond loopback.
`evalforge doctor` checks configuration, migration state, database connectivity, provider
capability, and execution limits without printing secrets.

The deterministic local topology is:

```text
EVALFORGE_AUTH_MODE=local
EVALFORGE_EXECUTOR_MODE=embedded_single
```

## Database lifecycle

Alembic is the schema authority:

```bash
uv run alembic current
uv run alembic upgrade head
uv run alembic check
```

Do not use `metadata.create_all()` as a migration substitute outside isolated tests. Back up the
database before schema changes. The default `.data/evalforge.db` and its WAL files must remain on a
local filesystem.

For SQLite, stop the writer and copy the database plus any WAL/SHM state together, or use SQLite's
online backup API; copying only the main file while writes continue is not a consistent backup. For
PostgreSQL, use the platform's native logical or physical backup and test restore procedure. Record
the Alembic revision and application version with each backup and restore only into a compatible
schema path. JSON, CSV, and versioned run packages are portable evidence artifacts, not database
backups: they do not contain complete identity, membership, audit, queue, or operational state.

Revision `0003_identity_tenant_scope` adds identity, memberships, workspace scope, and audit history,
then backfills existing Phase 2 data into the stable local workspace. Its downgrade refuses to
discard nonlocal identity or audit data and is regression-tested for an exact populated-`0002`
round trip. Revision `0004_durable_execution_leases` adds persisted claim, lease, heartbeat, and
attempt evidence. SQLite migration connections temporarily suspend foreign-key enforcement only
around Alembic batch reconstruction, validate `foreign_key_check`, and restore enforcement.

## Execution topologies

| Mode | Database | Meaning |
|---|---|---|
| `embedded_single` | SQLite or PostgreSQL | API embeds one database-discovering worker; local default. |
| `api_only` | PostgreSQL recommended | API persists work but does not claim it. |
| `database_worker` | PostgreSQL | Dedicated process polls, claims, heartbeats, and executes. |

Worker timing controls are `EVALFORGE_WORKER_POLL_INTERVAL_SECONDS`,
`EVALFORGE_WORKER_LEASE_SECONDS`, and `EVALFORGE_WORKER_HEARTBEAT_SECONDS`. Configuration requires
two heartbeat intervals to fit strictly inside the lease. Use the same migration head, provider
configuration, and server safety limits across API and workers.

For PostgreSQL:

```bash
uv sync --all-groups --extra postgres
# EVALFORGE_DATABASE_URL=postgresql+psycopg://user:password@host/database
uv run alembic upgrade head
uv run evalforge seed
uv run evalforge doctor
```

Bare `postgresql://` URLs are rejected. Local PostgreSQL 17 proof covers packaged migrations,
schema drift, seed/doctor, atomic claim contention, lifecycle, and database-clock eligibility. It
does not prove hosted failover, backups, connection-pool sizing, or production availability.

## Identity operations

Shared mode requires HTTPS OIDC settings and explicit workspace provisioning. Operator commands
are idempotent where safe and emit audit evidence:

```bash
uv run evalforge workspace-create --slug research --name "Research"
uv run evalforge membership-provision --workspace research --issuer https://id.example/ \
  --subject user-123 --role editor --display-name "Research editor"
uv run evalforge membership-revoke --workspace research --issuer https://id.example/ \
  --subject user-123
```

Use the exact configured issuer, including a trailing slash. Revocation suspends history rather than
deleting provenance. The current source has local signed-token and denial proof, but a real
identity-provider login must be validated in the selected hosted environment before exposure.

### Streamlit OAuth secret

The dashboard OAuth client is separate from backend JWT validation. The backend
`EVALFORGE_OIDC_*` settings define which access tokens FastAPI accepts; Streamlit also needs a
server-side OAuth client so `st.login()` can obtain that access token. Create a non-committed TOML
file with this exact shape:

```toml
[auth]
redirect_uri = "https://dashboard.example/oauth2callback"
cookie_secret = "replace-with-at-least-32-random-bytes"
expose_tokens = ["access"]

[auth.evalforge]
client_id = "replace-at-deploy-time"
client_secret = "replace-at-deploy-time"
server_metadata_url = "https://id.example/.well-known/openid-configuration"
```

The named provider must match `EVALFORGE_DASHBOARD_OIDC_PROVIDER`. Do not expose an ID or refresh
token. The redirect must end in `/oauth2callback`; shared-runtime redirect and metadata URLs must use
HTTPS. Keep the client secret and cookie secret in the deployment's secret manager, not `.env`, Git,
an image layer, or ordinary container environment output.

For Compose, set `EVALFORGE_STREAMLIT_AUTH_SOURCE_FILE` to that host secret path. Compose mounts it
read-only at `/run/secrets/evalforge_streamlit_auth.toml`; `scripts/start_dashboard.py` validates it
and passes it to Streamlit through `--secrets.files`. Missing, unreadable, weak, malformed, or
incomplete configuration stops startup before Streamlit binds. Native OIDC operators can instead
set `EVALFORGE_STREAMLIT_AUTH_FILE` to an absolute path and use the same validated launcher.

## Health and recovery

- `/health/live` proves only that the API process answers.
- `/health/ready` proves database and migration readiness plus local executor state.
- In `api_only`, readiness deliberately reports the external worker as unobserved.
- `/_stcore/health` proves only the Streamlit process answers.
- `/api/v1/capabilities` reports safe auth, provider, disclosure, limit, and execution state.

The database row is the queue authority. A worker lease contains owner, random token, epoch, expiry,
and heartbeat. Writes are fenced by that evidence. If renewal fails, the worker stops writing and
does not release the ambiguous lease; normal expiry/takeover handles recovery. Returned provider
evidence is retained before scoring. Never automatically replay a billing-ambiguous request.

## Deterministic demo and direct routes

`evalforge seed` is idempotent per workspace. Demo generation, usage, latency, and errors are
SHA-256-derived and labeled synthetic. No network call is made.

The neutral `src/evalforge/streamlit_app.py` entry point keeps Streamlit's route registration
separate from the implementation `dashboard/pages/` package. Direct cold bookmarks to Home, Runs,
Compare, New evaluation, Benchmarks, and Settings are covered by Playwright.

## Real-provider checklist

1. Keep external calls disabled while building and calibrating the benchmark.
2. Review which input, output, reference, and context fields the adapter or judge transmits.
3. Set backend credentials and model allowlists only through secret management.
4. Choose the provider's explicit API mode; do not rely on fallback.
5. Set `EVALFORGE_REAL_RUNS_ENABLED=true` only in the intended environment.
6. Review preflight call, token, known/unknown pricing, applicability, and estimated-cost evidence.
7. Acknowledge external transfer, known cost, and unknown cost separately as applicable.
8. Enter a user spend ceiling below or equal to the server cap.
9. Preserve request IDs, audit events, and a redacted package; use full evidence only when required.

The ceiling is not a provider billing limit. Never test a provider by putting a key in source, a
dashboard field, a URL, a shell command, an export, or a support log. EvalForge does not
automatically retry billable generation, including HTTP 429; a deliberate new run is the only retry
unless a future provider-specific idempotency contract is separately implemented and budgeted.

## Evidence exports

The dashboard defaults to `content_redacted`; `full_evidence` displays a warning and remains disabled
until separately confirmed. The CLI also requires the disclosure profile:

```bash
uv run evalforge export-package RUN_ID --disclosure-profile content_redacted \
  --output-dir ./private-exports
```

In OIDC mode add `--workspace WORKSPACE_SLUG`. The local sink uses private permissions, rejects
symbolic-link destinations, names files by the canonical payload hash, validates existing content,
and returns whether it created or reused the package.

## Containers

The API and dashboard images run non-root with a read-only root filesystem, dropped capabilities,
`no-new-privileges`, loopback-published ports, and bounded temporary storage. Compose requires
production-shaped OIDC values and fails closed when they are absent:

```bash
EVALFORGE_OIDC_ISSUER=https://id.example/ \
EVALFORGE_OIDC_AUDIENCE=evalforge \
EVALFORGE_OIDC_JWKS_URL=https://id.example/.well-known/jwks.json \
EVALFORGE_PUBLIC_BASE_URL=https://evalforge.example \
EVALFORGE_API_URL=https://evalforge.example \
EVALFORGE_TRUSTED_HOSTS='["evalforge.example","127.0.0.1","api"]' \
EVALFORGE_STREAMLIT_AUTH_SOURCE_FILE=/secure/evalforge-streamlit-auth.toml \
docker compose config
```

The dashboard uses `EVALFORGE_PUBLIC_BASE_URL` as its HTTPS API origin; bearer tokens are never sent
to the plaintext Compose service name. This phase proves both image builds, fail-closed startup
contracts, and configuration validation. It does not claim a Compose runtime with a real IdP,
hosted TLS, or production readback.

## Release and production boundary

Before public or production use, add and prove TLS ingress, secret rotation, abuse/rate limiting,
storage encryption, backup/restore, retention and deletion automation, operational alerts, and a
real IdP login/logout/role journey. Before treating external scores as release gates, perform an
explicitly authorized provider run and human-reviewed calibration. A remote GitHub Actions run,
hosted deployment, and production readback require their own immutable evidence records.
