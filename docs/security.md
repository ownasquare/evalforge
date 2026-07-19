# Security model

## Boundary

EvalForge has two explicit identity modes:

- `local` supplies one deterministic Owner and workspace for offline use. Settings reject any
  non-loopback API or dashboard bind in this mode.
- `oidc` is the shared-workspace source path. It requires HTTPS issuer, audience, JWKS URL, and
  public base URL configuration. Resource requests require a valid bearer token and selected active
  workspace membership.

This repository proves both modes locally, including signed OIDC fixtures and cross-tenant denial.
It does not claim a hosted identity-provider journey, TLS ingress, rate limiting, encryption at
rest, or production compliance.

## Protected assets

- provider API keys and compatible-gateway credentials;
- bearer tokens and identity claims;
- prompts, contexts, references, generated outputs, and calibration labels;
- run provenance, usage, cost, request identifiers, and audit history;
- imported datasets and exported evidence packages.

## Controls

### Authentication and workspaces

OIDC accepts only configured RS256/ES256 algorithms and verifies an exact issuer string, audience,
subject, expiration, and bounded clock skew. JWKS retrieval requires HTTPS, does not follow
redirects, limits response bytes and key count, caches keys, and rate-bounds unknown-key refreshes.
Tokens are request-local and are not persisted by the API or written into dashboard URLs.

Streamlit OAuth credentials and cookie-signing material live in a separate server-side TOML secret.
The dashboard launcher requires `expose_tokens = ["access"]` exactly, validates an HTTPS callback and
provider metadata URL, and passes the mounted file through Streamlit's `secrets.files` option. The
client secret, cookie secret, and access token are never accepted through dashboard fields or normal
EvalForge environment settings. Production OIDC also requires an HTTPS dashboard API URL before a
bearer token can be forwarded.

The issuer/subject pair must match an active provisioned user. The requested
`X-EvalForge-Workspace-ID` must match an active membership and active workspace. Viewer, Editor,
Admin, and Owner permissions are enforced by central API dependencies. Repositories and services
also require an immutable workspace context, so hiding a dashboard button is never the security
boundary. Suspended identities and memberships fail closed.

Workspace scope covers every evaluation-domain table and audit event. Cross-tenant reads and
mutations return the same denial shape as unavailable access, limiting object enumeration. Local
operator commands create workspaces and provision or revoke memberships with operator audit events;
those rows intentionally have no authenticated API actor (`actor_user_id` is absent).

### Credentials and providers

Keys are Pydantic secret values resolved only by FastAPI. They are not ORM fields, response fields,
dashboard inputs, exports, or normal log context. Run requests cannot select arbitrary base URLs.
Model IDs are checked against server allowlists, closing obvious SSRF and accidental routing paths.

### Paid execution and data transfer

Real calls require all of the following: a server enable flag, configured provider, allowlisted
model, explicit API mode, external-data-transfer acknowledgment, cost acknowledgment, and a
positive user spend ceiling. Known preflight cost must fit both that ceiling and the server cap.
Unpriced selections require a distinct unknown-cost acknowledgment. These are conservative planning
guards, not a provider-side budget or invoice guarantee.

Maximum cases, variants, calls, output tokens, concurrency, timeout, rendered bytes, padded input
estimate, estimated known cost, and upload sizes are bounded. Ambiguous failures never cause an
automatic cross-endpoint fallback or immediate lease replay. Generic compatible gateways receive
exactly one generation attempt per planned call, including HTTP 429, because rejection-before-
generation semantics cannot be assumed. Preflight therefore reports zero automatic retries and no
hidden request multiplier.

### Untrusted content

Dataset text, prompt variables, model output, CSV cells, provider messages, and imported metadata
are untrusted. Prompt rendering accepts only `{input}` and `{context}`; evaluator references cannot
leak into candidate input. Metadata rejects non-finite values and credential- or endpoint-like keys.
SQLAlchemy parameterizes values. Streamlit renders output as text, JSON Schema rejects external
references, and CSV export prefixes spreadsheet formula markers. Public errors exclude provider
bodies and stack traces.

### Browser, network, and containers

CORS allows only configured dashboard origins and disables credentials. Security headers limit
framing, MIME sniffing, and referrer leakage. Trusted hosts default to loopback, the API service
name, and test client. Native startup scripts validate settings before binding. Container ports
publish on loopback, processes run non-root, capabilities are dropped, `no-new-privileges` is set,
and root filesystems are read-only with bounded temporary storage.

Compose is production-shaped rather than a zero-configuration local demo: it fails closed unless
required OIDC/TLS-facing settings are supplied. That configuration check and image build are not a
substitute for a hosted ingress and identity readback.

Only `/health/live` should be broadly reachable by a load balancer. `/health/ready` and `/metrics`
must remain restricted to the ingress, orchestrator, or monitoring network because they expose
operational state.

### Audit, export, and retention

Mutations and sensitive exports record workspace, actor, action, resource, request ID, and bounded
metadata. Versioned packages are canonicalized and SHA-256-addressed. The dashboard defaults to
content-redacted export and requires a separate confirmation for full evidence. The local sink
writes idempotently and returns a receipt.

Operators still own database, audit, and export retention. Evaluation evidence may contain
proprietary or personal data. Storage encryption, backups, deletion policy, legal hold behavior,
and key management must be selected for the deployment environment.

Run-linked calibration uploads are streamed directly into bounded memory rather than parsed as
multipart files, then verified against immutable result evidence. Raw pass/fail decisions and
opaque reviewer identifiers are discarded; only a content-minimized aggregate report, hashes,
linkage, counts, and actor attribution are stored.
The endpoint requires a matching non-simple CSV or JSON content type, so an unrelated browser
origin cannot mutate loopback local mode with a CORS-safelisted `text/plain` request.
Reviewer files remain sensitive source evidence and must stay outside the repository and under the
operator's access and retention policy. Calibration audit events contain hashes and counts, never
raw labels or reviewer identifiers.

## Residual risks

- Lexical metrics can disagree with human judgment or be gamed by overlap.
- Model outputs can contain harmful, sensitive, or malicious content even when rendered as text.
- A local machine user with filesystem access can read SQLite and environment files.
- External evaluation transmits selected prompt/context data to the configured service.
- Database leases prevent duplicate ownership, not exactly-once provider billing.
- API-only readiness cannot directly observe a separate worker process.
- No abuse/rate-limit layer, automated retention, encryption-at-rest proof, hosted TLS proof, or
  compliance certification is included.

See the root [security policy](../SECURITY.md) for responsible reporting.
