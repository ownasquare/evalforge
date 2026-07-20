# EvalForge hosted pilot: exactly-five-blocker audit

Date: 2026-07-20
Audit baseline: `main` at `5dff2bae6f47fe7dc1d231f3f08dad6b33d4ad72`; status below includes the current unpublished pilot implementation.
Decision: **The hosted pilot is not yet externally proven. Work is frozen to the five blockers below.**

## Proof classification

| Class | Meaning |
|---|---|
| Source/configuration | Code, schema, configuration, or static contract exists. |
| Local automated | Tests ran against local or CI fixtures/services. |
| Local browser | A real local Streamlit-to-FastAPI browser journey ran. |
| Hosted | The selected remote runtime was deployed and read back by URL and immutable deployment ID. |
| Provider | A real identity, payment, model, or infrastructure provider was exercised and read back. |
| Production operations | Recovery, monitoring, retention, security controls, and ongoing service behavior were proven in the target environment. |

Current pilot validation reports 460 passing non-E2E tests at 85.03% branch-aware coverage, 13
passing local Playwright E2E tests, and a real local browser comparison/export with desktop and
mobile screenshots. Four PostgreSQL tests skip without an explicit disposable database URL; the
new hosted Playwright specification also stays opt-in without private live fixtures. Live OIDC and
production deployment remain separate unproved gates.

## Audit summary

| # | Required closure | Current evidence | Missing proof | Status |
|---|---|---|---|---|
| 1 | Hosted runtime and HTTPS ingress | Hardened containers, fail-closed Compose, and an intended Render Blueprint topology. | Blueprint creation, immutable deployment, TLS URL, secret binding, abuse controls, and remote health/readback. | Source prepared; hosted proof open |
| 2 | Live OIDC and workspace isolation | OIDC validation, roles, provisioning commands, workspace scoping, and local denial tests exist. | Real login/logout, membership lifecycle, two-workspace browser denial, token expiry, and provider readback in the hosted environment. | Open |
| 3 | Production PostgreSQL and worker recovery | PostgreSQL-compatible migrations, database leases, worker modes, and CI service-container coverage exist. | Managed database, pooled connections, external-worker observation, restart persistence, backup/restore, retention, and failure recovery in the target environment. | Open |
| 4 | Team request and server-authoritative entitlement | Plan catalog, workspace entitlements, post-success pending/canceled team requests, append-only receipts, serialized run-start enforcement, API routes, dashboard readback, focused local tests, and local browser plan/readback proof now exist. | Rendered hosted trial/request controls, operator-owned `qualified`/`declined` disposition if required, and hosted readback. No live billing is proven or selected. | Local automated/browser boundary present; hosted proof open |
| 5 | Hosted operational and activation proof | Request IDs, protected metrics, safe logs, health endpoints, run evidence, exports, bounded/namespaced event ingestion, 13 passing local Playwright tests, one local browser activation, and the canonical funnel contract exist. | Hosted alerts, operational runbook, sub-10-minute external activation evidence, mobile hosted journey, and immutable hosted acceptance record. | Local automated/browser proof present; hosted/external proof open |

## 1. Hosted runtime and HTTPS ingress

### Existing foundation

- The API and dashboard images run non-root with read-only filesystems, dropped capabilities, and
  loopback-published service ports ([Operations](../operations.md#containers)).
- Compose requires HTTPS OIDC values, a public base URL, trusted hosts, and a mounted Streamlit auth
  secret ([compose.yaml](../../compose.yaml)).
- The [Render Blueprint](../../render.yaml) declares separate API, dashboard, worker, and PostgreSQL
  resources, explicit secret inputs, readiness checks, and a pre-deploy migration command.
- The contract is intentionally limited: it does not claim a runtime with a real IdP, hosted TLS, or
  production readback ([Operations](../operations.md#containers)).

### Closure work

Choose one target host; provision API, dashboard, and ingress; bind secrets without printing them;
configure trusted origins/hosts and rate limits; apply the migration; and record deployment identity,
artifact digest, public URLs, configuration version, and rollback target.

### Exit proof

- HTTPS certificate and hostname readback.
- `/health/live`, `/health/ready`, Streamlit health, and capabilities readback from outside the host.
- One hosted deterministic evaluation through the public dashboard.
- Restart and rollback evidence tied to an immutable deployment identifier.

Source/configuration proof is present. Hosted and production proof are absent.

## 2. Live OIDC and workspace isolation

### Existing foundation

- OIDC validates an exact HTTPS issuer and audience using asymmetric JWTs; provisioned users and
  memberships resolve to Viewer, Editor, Admin, or Owner roles
  ([Architecture](../architecture.md#identity-and-authorization)).
- Workspace scope persists on evaluation resources and audit events; repository tests reject
  cross-workspace identifiers ([Architecture](../architecture.md#identity-and-authorization)).
- Operations explicitly say that current source has local signed-token and denial proof while a real
  identity-provider login still needs hosted validation
  ([Operations](../operations.md#identity-operations)).

### Closure work

Configure the real identity provider and Streamlit OAuth client; create two pilot workspaces; provision
at least Owner, Editor, and Viewer identities; verify revocation and token expiry; and validate that a
resource identifier from workspace A is indistinguishable from missing in workspace B.

### Exit proof

Playwright records login, logout, workspace selection, permitted role actions, denied role actions,
cross-tenant denial, revoked membership, and reauthentication against the hosted provider. The IdP
configuration and membership audit history are read back without exposing tokens.

Source and local automated proof are present. Hosted identity-provider proof is absent.

## 3. Production PostgreSQL and worker recovery

### Existing foundation

- PostgreSQL supports embedded, API-only, and database-worker modes with fenced database leases
  ([Architecture](../architecture.md#sqlite-and-postgresql-topologies)).
- Local PostgreSQL proof covers migrations, schema drift, contention, lifecycle, and database-clock
  eligibility, but not hosted failover, backup, pool sizing, or production availability
  ([Operations](../operations.md#execution-topologies)).

### Closure work

Provision a managed PostgreSQL instance; set bounded connection pools; deploy the API and at least one
observed worker; apply the exact migration head; define backup, retention, restore, and rotation
procedures; then exercise lease takeover and service restart with deterministic jobs.

### Exit proof

- Migration and application versions read back from the target database.
- A completed run survives API/dashboard restart.
- Worker observation and lease takeover are visible without duplicate result writes.
- A backup restores into an isolated target and the restored run/export can be read.

Source, CI-service, and local compatibility proof are present. Managed-database and production
recovery proof are absent.

## 4. Team request and server-authoritative entitlement

### Existing foundation

Existing `workspaces`, `workspace_memberships`, ordered roles, and `audit_events` provide tenant and
authorization foundations ([Architecture](../architecture.md#persistence-model)). The pilot source
now adds a code-defined plan catalog, one current `WorkspaceEntitlement` per workspace,
`TeamPilotRequest`, append-only `BillingEvent` and `ActivationEvent` records, authenticated API
routes, Settings controls/readback, and a server-side entitlement dependency on run preflight and
creation. The local OSS workflow bypasses commercial gating. Current request states are exactly
`pending`, `canceled`, `qualified`, and `declined`.

### Closure work

Retain focused validation of trial start/cancel, entitlement enforcement, tenant isolation,
idempotent request submit/cancel, one-pending-request enforcement, append-only receipts, audit
attribution, and fresh readback while adding rendered and hosted proof. The first cohort uses a team
payment-qualification request, not Stripe or another live-money provider. The current
member path creates `pending` and can move it to `canceled`; if operator qualification is needed in
this cycle, expose only authenticated server-owned `qualified` and `declined` transitions and prove
them separately. Do not infer a team entitlement from request submission.

### Exit proof

- Authenticated Admin request submission follows one same-actor two-candidate activation and the
  chosen privacy and abuse controls.
- Duplicate submissions are idempotent or explicitly reconciled.
- Unauthorized dispositions and client-authored entitlements are rejected.
- Trial activation creates one workspace entitlement; trial cancellation stops new evaluations
  while prior results and exports remain readable; a fresh session reads the same state.
- A pending request can be canceled and read back. Any future qualification or decline stays
  server-authoritative and does not imply money movement.
- UI and copy make clear that no charge was collected.

Commercial source contracts, focused local automated validation, and local browser proof of the OSS
plan/readback boundary are present. Hosted control rendering, hosted readback, external intent, and
payment-provider proof are absent until recorded. Payment-provider proof is deliberately out of
scope for the first cohort.

## 5. Hosted operational and activation proof

### Existing foundation

- Every API request has a request ID and logs exclude credentials and evaluation content
  ([Architecture](../architecture.md#observability)).
- Liveness, readiness, Streamlit health, capabilities, worker observation, and audit evidence are
  deliberately separate signals ([Operations](../operations.md#health-and-recovery)).
- The source uses the exact events `landing`, `signup`, `core_job_start`,
  `evaluation_complete`, `result_engagement`, `second_use`, `upgrade_view`, `checkout_start`,
  `entitlement_activation`, and `team_request_submitted`; `checkout_start` is reserved and must stay
  absent in the no-live-money first cohort.
- Local Playwright covers the seeded evaluation, direct routes, browser history, and key mobile
  layouts ([browser suite](../../tests/e2e/test_dashboard_smoke.py)). The opt-in
  [hosted acceptance specification](../../tests/e2e/test_hosted_commercial_pilot.py) covers live
  OIDC, tenant denial, qualifying activation, commercial readback/cancellation, and mobile Settings
  only when explicit private hosted fixtures are supplied.

### Closure work

Configure availability/error alerts and an operator runbook; preserve deployment and incident IDs;
validate the privacy-bounded pilot event contract; exercise the complete hosted activation journey
on desktop and mobile; and report activation time, second use, request conversion, acquisition
source, and exclusions with denominators.

### Exit proof

- Alert trigger and recovery readback, plus an operator-owned response path.
- Hosted Playwright journey from real login through evaluation, evidence engagement, team request,
  any implemented server-owned disposition, entitlement readback, and request/trial cancellation.
- At least one external user's sub-10-minute activation, reported separately from internal proof.
- Immutable acceptance record separating local, hosted, provider, database-recovery, commercial, and
  production evidence.

Observability, funnel source, one real local browser activation, 13 local Playwright passes, and
focused local automated validation are present. Hosted operations, external activation, and
production acceptance remain absent until recorded.

## Scope and feasibility rule

Only these five closures are pilot build work. Everything else remains frozen. If the minimum hosted
slice cannot close all five within six build days, record the failed exit criteria and return to the
portfolio decision record; do not solve the overrun by adding a platform, payment provider, or new
application.
