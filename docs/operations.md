# Operations

## Local demo

```bash
uv sync --frozen
uv run evalforge demo
```

The demo command applies migrations, idempotently seeds the offline examples, starts the API and
dashboard, waits for both services, and stops both on `Ctrl+C`. It is the recommended adopter path.
No `.env` file or provider key is required.

Contributors who need separate process logs can first install the development groups:

```bash
uv sync --frozen --all-groups
```

Then start these commands in separate terminals:

```bash
uv run evalforge api
uv run evalforge ui
```

Run `uv run evalforge seed` first when working from an empty database. The package launchers load
and validate settings before binding; local identity mode is rejected if either service is
configured beyond loopback. `uv run evalforge doctor` checks configuration, migration state,
database connectivity, provider capability, and execution limits without printing secrets.

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
then backfills existing pre-identity data into the stable local workspace. Its downgrade refuses to
discard nonlocal identity or audit data and is regression-tested for an exact populated-`0002`
round trip. Revision `0004_durable_execution_leases` adds persisted claim, lease, heartbeat, and
attempt evidence. Revision `0005_calibration_reports` adds immutable run-linked calibration
summaries. Revision `0006_commercial_pilot` adds workspace entitlements, append-only billing and
activation evidence, and pending team-pilot qualification requests. SQLite migration connections temporarily
suspend foreign-key enforcement only around Alembic batch reconstruction, validate
`foreign_key_check`, and restore enforcement.

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

Bare platform-provided `postgresql://` URLs are normalized internally to
`postgresql+psycopg://`; explicit `postgresql+psycopg://` URLs remain supported. Local PostgreSQL 17
proof covers packaged migrations, schema drift, seed/doctor, atomic claim contention, lifecycle,
and database-clock eligibility. It does not prove hosted failover, backups, connection-pool sizing,
or production availability.

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

On a host that provides secret environment bindings but no secret-file mount, set
`EVALFORGE_DASHBOARD_PUBLIC_BASE_URL` plus all four
`EVALFORGE_DASHBOARD_OIDC_CLIENT_ID`, `EVALFORGE_DASHBOARD_OIDC_CLIENT_SECRET`,
`EVALFORGE_DASHBOARD_OIDC_SERVER_METADATA_URL`, and
`EVALFORGE_DASHBOARD_OIDC_COOKIE_SECRET` values. The launcher validates them, writes a temporary
mode-`0600` TOML file, clears those values before Streamlit starts, and removes the file on exit.
The dashboard public URL supplies the exact `/oauth2callback` redirect. Do not print these values or
use this fallback as evidence that the identity provider was successfully exercised.
The launcher also replaces inherited provider keys and the metrics token with non-rehydrating
sentinels and replaces any credential-bearing database URL with an isolated in-memory SQLite URL;
the dashboard is an API client and receives none of those server-only credentials.

## Hosted commercialization pilot

The OSS workflow remains the default and does not require commercial state. The hosted pilot is
available only with both shared OIDC and:

```text
EVALFORGE_COMMERCIAL_PILOT_ENABLED=true
EVALFORGE_HOSTED_TRIAL_DAYS=14
EVALFORGE_HOSTED_TRIAL_SEAT_LIMIT=5
```

Run `uv run evalforge migrate` once as the deployment migration authority before starting API or
worker processes. It applies the packaged Alembic chain, verifies database readiness, emits only
the safe database backend, and exits nonzero on failure. Hosted API and worker services set
`EVALFORGE_AUTO_MIGRATE=false`; the worker honors that flag and performs readiness-only startup so
parallel services cannot race Alembic.

Plans are code-defined; they are not a provider catalog. The server stores at most one current
entitlement per workspace, append-only `billing_events`, append-only content-minimized
`activation_events`, and team-pilot requests. A trial starts in `trialing`; its effective state can
be `trialing`, `active`, `expired`, or `canceled`. Team requests use only `pending`, `canceled`,
`qualified`, or `declined`. The member-facing pilot API currently creates `pending` requests and
allows an administrator to cancel a pending request. There is no operator qualification endpoint,
invoice, subscription, webhook, Stripe checkout, or live-money activation in this first slice.
A new request requires a same-actor successful comparison plus post-completion export engagement,
and the server permits only one pending request per workspace.

The stable activation vocabulary is `landing`, `signup`, `core_job_start`,
`evaluation_complete`, `result_engagement`, `second_use`, `upgrade_view`, `checkout_start`,
`entitlement_activation`, and `team_request_submitted`. Only `landing`, `signup`, and
`upgrade_view` may originate at the dashboard API boundary. Client idempotency keys are
server-namespaced by actor, first-touch events are deduplicated, and ingestion is capped at 100
client events per actor per UTC day. Run acceptance, qualifying two-candidate completion,
post-completion export engagement, repeat use, trial entitlement activation, and request submission
are server-authored. `checkout_start` is reserved in the schema but rejected during this
no-provider cohort.

The funnel readback groups authenticated `signup` actors by content-safe acquisition source and
computes nearest-rank p50/p90 seconds from each actor's first `signup` to that same actor's first
post-completion engagement with a qualifying comparison they requested. It returns the sample size
and signed-up actors excluded for missing activation; a percentile without its numerator and
exclusions is not acceptance evidence. Anonymous visits are not inferred from the authenticated
`landing` event.

When the pilot is enabled, run preflight and creation require active workspace access and a
membership count within the entitlement's seat limit. Cancellation, history, result reads, and
exports remain accessible after expiry or cancellation. This preserves evidence and keeps plan
enforcement server-authoritative. The dashboard's access card is a readback of this state, not the
authority. PostgreSQL run admission and commercial mutations serialize on the workspace row and
revalidate active membership and role inside the locked transaction. Commercial history endpoints
return at most the newest 100 rows; funnel aggregation still uses the complete workspace history.

Treat the following proof layers separately:

- schema, route, dashboard, and configuration existence is source proof;
- passing local tests and local browser flows is local proof;
- a remote URL and deployment identifier are hosted proof;
- a real login/logout and two-workspace denial journey are identity-provider proof;
- restart persistence plus backup/restore is managed-database proof;
- a submitted request is commercial-intent evidence, not qualification, entitlement, or payment;
- no payment-provider or live-money proof exists in this cohort.

## Health and recovery

- `/health/live` proves only that the API process answers.
- `/health/ready` proves database and migration readiness plus local executor state.
- `/metrics` remains open only in local mode. Shared OIDC mode fails closed until the API receives
  `EVALFORGE_METRICS_BEARER_TOKEN`, then requires the exact Bearer token. Never bind that server
  secret into the dashboard service.
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
separate from the implementation `dashboard/pages/` package. Direct cold bookmarks to Home,
Results, Compare, New evaluation, Benchmarks, Models, and Settings are covered by browser tests.

## Human calibration reports

Human-label calibration is optional and never contacts a model provider. For a completed run, open
**Results → Human calibration**, choose one candidate and metric, then download the generated CSV
template. Fill only the decision and opaque reviewer columns, keep that file private, and import it
with the threshold you want to evaluate. Rows follow the case order shown in Results; fill
`human_passed` and an anonymous `reviewer_id`.

The API streams the raw CSV or JSON body into at most 2 MiB of memory, validates every uploaded case
mapping, identity, and score against immutable result rows, then discards it without multipart temp
files. It stores only the derived aggregate report and hashes. It does not retain raw reviewer
decisions or identifiers, choose a threshold, approve a model, or claim production validation.
Editors import; Viewers can download templates and read report history.

The CLI remains available when a report should stay entirely outside the application database:

```bash
uv run evalforge calibrate examples/calibration-labels.json --threshold 0.7 \
  --output-dir ./private-calibration
```

The command validates the complete input before writing, names the report from its canonical
SHA-256, creates it with private permissions, and returns `already_exists` when an identical report
is already present. A conflicting, modified, or symbolic-link destination fails closed. Keep the
output directory private and use opaque reviewer IDs such as `reviewer-01`; do not place reviewer
names, email addresses, credentials, or source content in those identifiers.

Both workflows are offline only. They do not read provider settings, contact a provider, choose a
threshold automatically, measure reviewer agreement, or validate a production deployment. Every
report records those boundaries explicitly. Operators own retention for uploaded source files,
database reports, and local CLI outputs. See
[Evaluation methodology](evaluation-methodology.md#offline-threshold-calibration) for the schema and
interpretation guidance.

## Real-provider checklist

### Connect your first provider locally

Keep the offline demo working first, then make a private local settings file:

```bash
cp .env.example .env
```

Open `.env` in your editor. For OpenAI Responses, set `EVALFORGE_OPENAI_API_KEY`, keep only the
models you intend to use in `EVALFORGE_OPENAI_MODEL_ALLOWLIST`, and change
`EVALFORGE_REAL_RUNS_ENABLED` to `true`. For an OpenAI-compatible endpoint, use the corresponding
`EVALFORGE_COMPATIBLE_*` fields instead; a local gateway may use `auth_mode=none` and leave its key
blank. The ignored `.env` file is loaded by the backend, while provider credentials are deliberately
removed from the dashboard process.

Verify the configuration without exposing the key, then launch normally:

```bash
uv run evalforge doctor
uv run evalforge demo
```

In the dashboard, open **Models**, add an allowlisted model, then choose **New evaluation**. Review
the preflight call count, token estimate, transfer disclosures, and spend ceiling before starting.
Afterward, use **Results** to inspect case evidence and create a redacted export when you need to
share the run. Never paste a provider key into the dashboard, a command, a URL, an export, or a
support message.

1. Keep external calls disabled while building and calibrating the benchmark.
2. Review which input, output, reference, and context fields the adapter or judge transmits.
3. Set backend credentials and model allowlists only through secret management.
4. Confirm the provider's server-published API mode; adapters must not rely on fallback.
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
EVALFORGE_TRUSTED_HOSTS='["evalforge.example"]' \
EVALFORGE_STREAMLIT_AUTH_SOURCE_FILE=/secure/evalforge-streamlit-auth.toml \
docker compose config
```

The dashboard uses `EVALFORGE_PUBLIC_BASE_URL` as its HTTPS API origin; bearer tokens are never sent
to the plaintext Compose service name. The shared identity environment also passes that URL to the
API process so production OIDC validation cannot fall back to a loopback HTTP default. The
automated contract covers both image builds, fail-closed startup contracts, and configuration
validation. It does not claim a Compose runtime with a real IdP, hosted TLS, or production readback.

### Render Blueprint reference topology

[`render.yaml`](../render.yaml) is an intended pilot topology, not deployment evidence. It declares:

- an API web service built from `Dockerfile.api`, with `/health/ready`, `api_only` execution, and
  `evalforge migrate` as the pre-deploy command;
- a dashboard web service built from `Dockerfile.dashboard`, with `/_stcore/health` and the
  environment-to-temporary-file OAuth launcher path;
- a dedicated `database_worker` process built from the API image;
- a private-network PostgreSQL 17 database; and
- explicit `sync: false` bindings for public URLs, allowed hosts/origins, OIDC configuration, and
  dashboard OAuth credentials, plus an API-only metrics Bearer token.

The Blueprint deliberately keeps real model-provider calls disabled and uses the pending team-pilot
request path with `live_money=false`. Before creation, review service names, region, plans, public
URLs, CORS origins, trusted hosts, issuer/audience/JWKS values, dashboard metadata URL, and all
secret bindings. Render creation, build/deploy identifiers, HTTPS readback, real OIDC, remote worker
observation, restart persistence, backup/restore, alerts, and rollback must each be captured as
separate hosted evidence. The presence or static validation of `render.yaml` proves none of them.

## Release and production boundary

Before public or production use, add and prove TLS ingress, secret rotation, abuse/rate limiting,
storage encryption, backup/restore, retention and deletion automation, operational alerts, and a
real IdP login/logout/role journey. Before treating external scores as release gates, perform an
explicitly authorized provider run and human-reviewed calibration. A remote GitHub Actions run,
hosted deployment, and production readback require their own immutable evidence records.
