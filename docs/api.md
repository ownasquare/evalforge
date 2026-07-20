# API contract

The development OpenAPI document is available at `/openapi.json` and the interactive explorer at
`/docs`. Application resources are under `/api/v1`.

## Authentication and workspace selection

In `local` mode the API resolves the deterministic local Owner and permits the sole workspace to be
selected implicitly. Local mode is valid only on loopback.

In `oidc` mode protected requests require both:

```http
Authorization: Bearer <access-token>
X-EvalForge-Workspace-ID: <workspace-uuid>
```

The token must match the configured issuer and audience, and the user, workspace, and membership
must all be active. The workspace header remains required even when the user has one shared
workspace. Access tokens never belong in URLs, request bodies, logs, exports, or persisted records.

Roles are cumulative:

| Role | Access |
|---|---|
| Viewer | Read resources, analytics, results, comparisons, and exports. |
| Editor | Viewer access plus datasets, cases, prompts, preflight, run creation, and cancellation. |
| Admin | Editor access plus model-profile mutation and hosted-pilot access management. |
| Owner | Admin access and reserved workspace-governance authority. |

## System and session endpoints

| Method | Path | Minimum role | Purpose |
|---|---|---|---|
| `GET` | `/health/live` | Public | Process liveness; independent of providers. |
| `GET` | `/health/ready` | Public | Database, migration, executor-role, and worker-observation truth. |
| `GET` | `/metrics` | Ingress-internal | Prometheus-format process counters; intentionally omitted from OpenAPI. |
| `GET` | `/api/v1/session` | Signed in | Current safe identity and available workspaces. |
| `GET` | `/api/v1/workspaces` | Signed in | Active memberships and roles. |
| `GET` | `/api/v1/meta` | Viewer | Non-secret build, database, adapter, auth, and executor metadata. |
| `GET` | `/api/v1/capabilities` | Viewer | Safe provider availability, limits, consent requirements, metric versions, and execution mode. |
| `GET` | `/api/v1/overview` | Viewer | Workspace totals, pricing-evidence coverage, and recent runs. |

Capabilities expose booleans, limits, and allowlisted model identifiers, never secret values.
`api_only` readiness reports `worker: external_unobserved` and `worker_observed: false`; it does not
pretend that a separate worker is healthy.

The `commercial` capability object reports whether the pilot is enabled, whether the process is in
shared OIDC mode, the trial duration and seat limit, `payment_path=qualified_team_request`, and
`live_money=false`. These are source/runtime settings, not evidence that a public host, payment
provider, or customer activation exists.

Only `/health/live` is intended for unrestricted load-balancer use. In a shared deployment,
`/health/ready` and `/metrics` can reveal operational state and must be restricted to the ingress,
orchestrator, or monitoring network.

## Hosted-pilot endpoints

The commercial contract is deliberately workspace-scoped and small. It preserves the complete
local OSS workflow: in `local` mode the entitlement readback returns active `open_source` access and
run creation is never commercially gated. Trial and team-request mutations fail closed unless the
server uses `oidc` authentication with `EVALFORGE_COMMERCIAL_PILOT_ENABLED=true`.

| Method | Path | Minimum role | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/commercial/plans` | Viewer | Read the code-defined `open_source`, `hosted_trial`, and `team` offer. |
| `GET` | `/api/v1/commercial/entitlement` | Viewer | Read the workspace plan, status, seat limit, period, and `can_start_runs` decision. |
| `POST` | `/api/v1/commercial/trial` | Admin | Start the workspace's one hosted trial. |
| `POST` | `/api/v1/commercial/trial/cancel` | Admin | Cancel the hosted trial and stop new hosted evaluations while preserving existing evidence access. |
| `GET`, `POST` | `/api/v1/commercial/team-requests` | Admin | List requests or create one post-activation team-pilot qualification request. |
| `POST` | `/api/v1/commercial/team-requests/{request_id}/cancel` | Admin | Cancel one pending request. |
| `GET` | `/api/v1/commercial/billing-events` | Admin | Read append-only access-transition receipts; these are not proof of provider billing. |
| `GET`, `POST` | `/api/v1/commercial/events` | Admin (GET) / Viewer (POST) | List workspace events or record an allowed content-minimized client milestone. |
| `GET` | `/api/v1/commercial/funnel` | Admin | Read workspace-scoped event counts, unique actors, signup acquisition sources, activated runs, activation-duration sample/exclusions/p50/p90, request counts, and time bounds. |

Every commercial `POST` requires a non-empty `Idempotency-Key` of at most 128 characters. The key is
scoped to the authenticated workspace; client event keys are additionally server-namespaced to the
actor so they cannot collide with worker/authored lifecycle evidence. Replaying the same request returns the existing state;
reusing a key for different request content is a conflict. Commercial reads return
`Cache-Control: private, no-store`.

A team request accepts `requested_seats` from 2 to 250, `evaluation_frequency` of `weekly`,
`several_times_week`, `daily`, or `release_driven`, and a `security_review_required` boolean. Its
persisted status is one of `pending`, `canceled`, `qualified`, or `declined`. The current member API
creates `pending` requests and can cancel only `pending` requests; operator qualification and
decline transitions are not exposed by this first slice. A workspace must first have a qualifying
same-actor completed-and-exported comparison, and only one request may remain `pending`. No request
charges a card, activates an entitlement, or proves a commercial commitment.

The canonical activation names are `landing`, `signup`, `core_job_start`,
`evaluation_complete`, `result_engagement`, `second_use`, `upgrade_view`, `checkout_start`,
`entitlement_activation`, and `team_request_submitted`. The client-event route accepts only
`landing`, `signup`, and `upgrade_view`; run acceptance, qualifying comparison
completion, export engagement, repeat use, entitlement activation, and team-request submission are
recorded by the server. `checkout_start` remains part of the stable event vocabulary but the API
rejects it while the first cohort has no provider checkout and `live_money=false`. `signup` and
`upgrade_view` are durable first-touch events per actor/workspace; authenticated `landing` events
are bounded to 100 client events per actor per UTC day.

Commercial event metadata is content-minimized and rejects fields whose names contain token,
secret, password, authorization, prompt, context, output, email, or subject. `activation_events`
and `billing_events` are append-only. The billing event provider is currently EvalForge itself and
records trial/request state transitions; it does not represent Stripe, an invoice, or money
movement.

The three append-only history list endpoints return at most the newest 100 rows in this pilot
slice. The funnel endpoint calculates over the complete workspace history.

## Dataset and case endpoints

| Method | Path | Minimum role | Purpose |
|---|---|---|---|
| `GET`, `POST` | `/api/v1/datasets` | Viewer / Editor | List or create datasets. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/datasets/{dataset_id}` | Viewer / Editor | Read, update, or delete an unreferenced dataset version. |
| `GET`, `POST` | `/api/v1/datasets/{dataset_id}/cases` | Viewer / Editor | List or create cases. |
| `PATCH`, `DELETE` | `/api/v1/cases/{case_id}` | Editor | Update or remove one case. |
| `POST` | `/api/v1/datasets/{dataset_id}/imports` | Editor | Atomically validate and import UTF-8 JSON or CSV. |
| `GET` | `/api/v1/datasets/{dataset_id}/export` | Viewer | Export JSON or formula-safe CSV. |

Imports enforce byte and row limits. Any invalid row rejects the complete import and returns
row-numbered errors. Every parent and child lookup is constrained to the selected workspace.

### Benchmark import format

JSON may be either a list of case objects or an object with a `cases` list. Start from the
[customer-support JSON example](../examples/customer-support.json). Each case accepts:

| Field | Required | Meaning |
|---|---|---|
| `input_text` or `input` | Yes | The user input sent through the selected prompt. |
| `external_id` or `name` | No | A stable, readable case name; generated when omitted. |
| `expected_output` or `reference` | No | Trusted evaluator-only answer for correctness. |
| `context_text`, `context`, or `context_chunks` | No | Grounding evidence. `context` may be a string or list. |
| `required_phrases` | No | JSON list of phrases that must appear. |
| `constraints_json` or `criteria` | No | Object with format or length requirements. |
| `tags` | No | JSON list of labels. |
| `metadata_json` or `metadata` | No | Object for optional metadata, including `relevance_keywords`. |
| `relevance_keywords` | No | JSON list merged into metadata for relevance scoring. |

CSV uses one case per row. `input_text` is the only required column. Use the same canonical field
names shown above and encode list/object cells as JSON text. The exported column set is
`external_id,input_text,context_text,context_chunks,expected_output,required_phrases,constraints_json,tags`;
`metadata_json` and `relevance_keywords` are also accepted on import. Start from the
[customer-support CSV example](../examples/customer-support.csv). Files must be UTF-8. Import is
atomic: one invalid row means no rows are added.

## Prompt and model endpoints

| Method | Path | Minimum role | Purpose |
|---|---|---|---|
| `GET`, `POST` | `/api/v1/prompts` | Viewer / Editor | List or create prompt templates. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/prompts/{prompt_id}` | Viewer / Editor | Read, update, or delete an unreferenced prompt version. |
| `GET`, `POST` | `/api/v1/models` | Viewer / Admin | List or create server-approved model profiles. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/models/{model_id}` | Viewer / Admin | Read, update, or delete an unreferenced profile version. |

Prompt templates accept only `{input}` and `{context}`. References are evaluator-only. Model
requests cannot contain a provider base URL or API key.

## Run endpoints

| Method | Path | Minimum role | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/runs/preflight` | Editor + hosted entitlement when enabled | Validate matrix, capability, disclosures, limits, applicability, and cost. |
| `POST` | `/api/v1/runs` | Editor + hosted entitlement when enabled | Persist and queue an immutable evaluation matrix. |
| `GET` | `/api/v1/runs` | Viewer | Filter and page workspace run history. |
| `GET` | `/api/v1/runs/{run_id}` | Viewer | Read status, progress, candidate identity, and summary. |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Editor | Request cancellation without deleting completed evidence. |
| `GET` | `/api/v1/runs/{run_id}/results` | Viewer | Page output and metric evidence. |
| `GET` | `/api/v1/runs/{run_id}/comparison` | Viewer | Baseline/challenger summaries and bounded paired deltas. |
| `GET` | `/api/v1/runs/{run_id}/export` | Viewer | Export JSON, formula-safe CSV, or a versioned package. |
| `GET` | `/api/v1/runs/{run_id}/calibrations/template` | Viewer | Download a candidate/metric label template derived from stored results. |
| `POST` | `/api/v1/runs/{run_id}/calibrations` | Editor | Validate private human labels and persist an immutable aggregate report. |
| `GET` | `/api/v1/runs/{run_id}/calibrations` | Viewer | Page content-minimized history; optionally filter by candidate and metric before pagination. |
| `GET` | `/api/v1/runs/{run_id}/calibrations/{report_id}` | Viewer | Read one immutable calibration report. |

`POST /runs` returns `202 Accepted` and a `Location` header. `Idempotency-Key` is optional and is
scoped to the active workspace. Real-provider creation additionally requires:

- `acknowledge_external_data_transfer: true`;
- `acknowledge_real_cost: true`;
- positive `spend_limit_micro_usd`;
- `acknowledge_unknown_cost: true` when any selected model has incomplete pricing.

Known estimated cost must not exceed the user ceiling or the server cap. Unknown pricing never
becomes zero. The ceiling is a preflight safety control, not a provider billing guarantee.
Requester identity and audit attribution always come from the authenticated workspace context;
the accepted `requested_by` body field is descriptive run metadata and is not an authorization or
identity claim.

Preflight reports `automatic_provider_retries: 0`; `maximum_provider_request_count` equals the
logical `provider_call_count`. Generation does not automatically retry 429 or other provider
failures because a generic compatible gateway cannot prove that an upstream billable request was
never created.

When the hosted pilot is enabled, preflight and run creation require an active `trialing` or `active`
workspace entitlement whose active membership count does not exceed its seat limit. A missing,
expired, canceled, or over-seat entitlement returns `402 entitlement_required`. Run history,
results, exports, cancellation, and audit evidence stay readable so losing future run access does
not erase prior evidence. This gate is disabled for the local OSS workflow.

### Run-linked human calibration

Template and import requests identify one `candidate_id` and configured `metric_name`. Templates
contain case order and case labels that match Results, plus immutable result IDs and stored metric
scores for completed, applicable results; the reviewer fills only `human_passed` and an opaque
`reviewer_id`. Imports also provide a finite
`selected_threshold` from `0` to `1`. Import the CSV or JSON file as the raw request body and set
the `format` query parameter to `csv` or `json`; this avoids multipart temp-file spooling.

The API does not trust the uploaded identities or scores. It verifies the run is terminal, the
candidate belongs to it, the dataset and metric identities match its snapshots, and every item ID
and score match a completed applicable result before calculating a report. JSON and CSV uploads are
streamed into bounded memory at 2 MiB and discarded after validation. An identical
run/candidate/manifest/threshold import is idempotent; the response
reports whether the immutable record was created or already existed.

Raw labels and reviewer identifiers are processed in memory and discarded. List and detail
responses contain only linkage, hashes, sample/reviewer counts, confusion counts, precision,
recall, F1, threshold, and the explicit `offline_statistical_evidence` /
`production_validated=false` boundary. There is no update, delete, approval, or automatic threshold
selection endpoint.

## Versioned evidence packages

Use:

```text
GET /api/v1/runs/{run_id}/export?format=package&disclosure_profile=content_redacted
GET /api/v1/runs/{run_id}/export?format=package&disclosure_profile=full_evidence
```

The default is `content_redacted`. The media type is
`application/vnd.evalforge.run-export+json`; `X-EvalForge-Payload-SHA256` contains the canonical
payload hash. The `evalforge.run-export.v1` payload records application version, disclosure
profile, metric versions, and immutable run evidence. Full evidence can contain inputs, references,
context, prompts, outputs, provider metadata, and metric evidence and therefore requires an
explicit dashboard or CLI disclosure choice. Export actions are audited with request ID, format,
profile, and package hash.

## Pagination and errors

List endpoints return `items`, `total`, `page`, and bounded `limit`. Errors use a stable envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request did not match the API contract.",
    "retryable": false,
    "request_id": "req_01...",
    "details": []
  }
}
```

Missing or invalid authentication is `401`; a hosted entitlement requirement is `402`; denied
workspace or role access is `403`; validation is `422`; missing in-scope resources are `404`;
idempotency or deletion conflicts are `409`; disabled capabilities are `403`; size limits are `413`;
and unexpected failures are sanitized `500` responses. Cross-workspace IDs never reveal whether
the object exists.
