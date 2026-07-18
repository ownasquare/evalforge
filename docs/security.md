# Security model

## Boundary

EvalForge is a single-user, loopback-first evaluation tool. It is not an authenticated multi-tenant
service. The secure default is local binding, real-provider calls disabled, server-side endpoints and
allowlists, no stored credentials, and deterministic offline operation.

## Protected assets

- provider API keys and compatible-gateway credentials;
- prompts, contexts, references, and generated outputs;
- run provenance, usage, cost, and request identifiers;
- imported datasets and exported evaluation packages.

## Controls

### Credentials and providers

Keys are Pydantic secret values resolved only by FastAPI. They are not ORM fields, response schema
fields, dashboard inputs, exports, or ordinary log context. Run requests cannot select arbitrary
base URLs. Model IDs are checked against server allowlists, closing the obvious SSRF and accidental
provider-routing paths.

### Paid execution

Real calls require a server enable flag, configured key, allowlisted model, explicit API mode,
preflight limits, and a per-run acknowledgment. Maximum cases, variants, calls, output tokens,
concurrency, timeout, rendered size, padded input estimate, estimated known cost, and upload sizes
are bounded. Unpriced models require a separate acknowledgment. Ambiguous failures never cause an
automatic cross-endpoint fallback.

### Untrusted content

Dataset text, prompt variables, model output, CSV cells, and provider messages are untrusted. Prompt
rendering accepts only `{input}` and `{context}`; evaluator references cannot leak into model input.
JSON metadata recursively rejects non-finite numbers and credential- or endpoint-like keys before
persistence. SQLAlchemy parameterizes database values.
Streamlit renders outputs as text rather than unsafe HTML. JSON Schema validation rejects external
references, preventing evaluation-time network resolution. CSV export prefixes spreadsheet formula
markers. Public errors use stable sanitized codes and exclude provider response bodies.

### Browser and network

CORS permits only configured dashboard origins, credentials are disabled, and security headers
limit framing, MIME sniffing, and referrer leakage. Trusted-host validation defaults to loopback,
the API service name, and the test client. Local services bind to `127.0.0.1`; container ports
publish on loopback. Network exposure requires authentication and TLS work that this repository
does not claim. An explicitly configured headerless compatible-provider profile emits no
authorization header.

### Logging and retention

Logs include request/run identifiers, event names, status, timing, and error classes. Prompt,
reference, context, output, authorization headers, keys, and raw provider error bodies are omitted by
default. The operator owns database/export retention because evaluation data may contain proprietary
or personal content.

## Residual risks

- Lexical metrics can disagree with human judgment or be gamed by token overlap.
- A model output can contain harmful or sensitive content even when rendered as plain text.
- A local machine user with filesystem access can read the SQLite database and environment file.
- Provider requests transmit selected prompt/context data to the configured service.
- SQLite and the in-process worker are not safe horizontal-scaling primitives.
- This project does not provide tenant isolation, user authentication, or a compliance certification.

See the root [security policy](../SECURITY.md) for responsible reporting.
