# API contract

The development OpenAPI document is available at `/openapi.json` and the interactive explorer at
`/docs`. All application resources are under `/api/v1`.

## System endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/live` | Process liveness; independent of providers. |
| `GET` | `/health/ready` | Database, migration, and local-worker readiness. |
| `GET` | `/api/v1/meta` | Stable non-secret build, database, adapter, and executor metadata. |
| `GET` | `/api/v1/capabilities` | Safe provider availability, limits, metric versions, and execution mode. |
| `GET` | `/api/v1/overview` | Dashboard totals, trend, leaderboard, and recent activity. |

Capabilities expose booleans and allowlisted model identifiers, never secret values.
Metadata lists the always-available deterministic adapter plus only those real-provider adapters
that the backend has actually configured.

## Dataset and case endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`, `POST` | `/api/v1/datasets` | List or create datasets. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/datasets/{dataset_id}` | Read, update, or delete an unreferenced dataset version. |
| `GET`, `POST` | `/api/v1/datasets/{dataset_id}/cases` | List or create cases. |
| `PATCH`, `DELETE` | `/api/v1/cases/{case_id}` | Update or remove one case. |
| `POST` | `/api/v1/datasets/{dataset_id}/imports` | Atomically validate and import UTF-8 JSON or CSV. |
| `GET` | `/api/v1/datasets/{dataset_id}/export` | Export JSON or formula-safe CSV. |

Imports enforce byte and row limits. Any invalid row rejects the complete import and returns
row-numbered errors; valid rows are not partially committed.

## Prompt and model endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`, `POST` | `/api/v1/prompts` | List or create prompt templates. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/prompts/{prompt_id}` | Read, update, or delete an unreferenced prompt version. |
| `GET`, `POST` | `/api/v1/models` | List or create server-approved model profiles. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/models/{model_id}` | Read, update, or delete an unreferenced profile version. |

Prompt templates accept only `{input}` and `{context}` placeholders. Reference answers are
evaluator-only and cannot be injected into a candidate prompt. Model requests cannot contain a
provider base URL or API key.

## Run endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/runs/preflight` | Validate matrix size, capability, applicability, limits, and known cost. |
| `POST` | `/api/v1/runs` | Persist and queue an immutable evaluation matrix. |
| `GET` | `/api/v1/runs` | Filter and page run history. |
| `GET` | `/api/v1/runs/{run_id}` | Read status, progress, candidate identity, and summary. |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Request cancellation without deleting completed evidence. |
| `GET` | `/api/v1/runs/{run_id}/results` | Page case-level output and metric evidence. |
| `GET` | `/api/v1/runs/{run_id}/comparison` | Paired variant deltas, wins/ties/losses, latency, usage, and cost. |
| `GET` | `/api/v1/runs/{run_id}/export` | Export the complete immutable result package. |

`POST /runs` returns `202 Accepted` and a `Location` header. Two prompts, two models, and five cases
produce four variants and twenty planned generation results. Real runs additionally require the
server flag, configured credentials, allowlisted models, and `acknowledge_real_cost: true`. If any
selected model lacks complete input/output pricing, creation also requires
`acknowledge_unknown_cost: true`. Full snapshots are available through the export endpoint rather
than duplicated into list and summary responses.

## Pagination and errors

List endpoints return `items`, `total`, `page`, and `limit`. The limit is bounded by the server.

Errors use a stable envelope:

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

Validation is `422`, missing resources are `404`, idempotency or deletion conflicts are `409`,
disabled capabilities are `403`, size limits are `413`, and unexpected errors are sanitized `500`
responses. Provider error bodies are never copied into the public message.
