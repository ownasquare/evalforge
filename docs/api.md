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
| Admin | Editor access plus model-profile mutation. |
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

Only `/health/live` is intended for unrestricted load-balancer use. In a shared deployment,
`/health/ready` and `/metrics` can reveal operational state and must be restricted to the ingress,
orchestrator, or monitoring network.

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
| `POST` | `/api/v1/runs/preflight` | Editor | Validate matrix, capability, disclosures, limits, applicability, and cost. |
| `POST` | `/api/v1/runs` | Editor | Persist and queue an immutable evaluation matrix. |
| `GET` | `/api/v1/runs` | Viewer | Filter and page workspace run history. |
| `GET` | `/api/v1/runs/{run_id}` | Viewer | Read status, progress, candidate identity, and summary. |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Editor | Request cancellation without deleting completed evidence. |
| `GET` | `/api/v1/runs/{run_id}/results` | Viewer | Page output and metric evidence. |
| `GET` | `/api/v1/runs/{run_id}/comparison` | Viewer | Baseline/challenger summaries and bounded paired deltas. |
| `GET` | `/api/v1/runs/{run_id}/export` | Viewer | Export JSON, formula-safe CSV, or a versioned package. |

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

Missing or invalid authentication is `401`; denied workspace or role access is `403`; validation is
`422`; missing in-scope resources are `404`; idempotency or deletion conflicts are `409`; disabled
capabilities are `403`; size limits are `413`; and unexpected failures are sanitized `500`
responses. Cross-workspace IDs never reveal whether the object exists.
