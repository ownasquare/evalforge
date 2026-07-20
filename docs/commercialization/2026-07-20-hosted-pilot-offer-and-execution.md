# EvalForge hosted commercialization pilot

Date: 2026-07-20
Decision status: **Locked for the 14-day pilot**
Implementation baseline: `main` at `5dff2bae6f47fe7dc1d231f3f08dad6b33d4ad72`; the pilot changes described below are the current working-tree implementation pending publication.
External proof status at creation: **No hosted deployment, buyer interview, team commitment, or payment is claimed.**

## Roadmap hierarchy

The Own A Square mission to ship 1,000 focused AI applications remains the public north star. It is
not the active build backlog for this cycle. The active roadmap is one 14-day commercialization
pilot for the existing EvalForge product. No new micro-application should be started until the
pilot receives a written Kill, Continue, or Scale decision.

## Locked offer

| Decision | Pilot contract |
|---|---|
| Buyer | Small AI engineering and product teams shipping LLM-backed features. |
| Core job | Compare at least two prompt or model candidates against the same test set, inspect the evidence, and retain a reviewable result before release. |
| Primary surface | Hosted web application only. The local OSS product remains separately available. |
| Activation | The same user starts a comparison of at least two candidates, reaches a useful completed evaluation, and copies or exports its result evidence. All steps must occur in one workspace and in that order. |
| Time to activation | Under 10 minutes from the durable first authenticated `signup` event to qualifying copy/export engagement. |
| Free promise | The existing MIT-licensed, deterministic, credential-free self-hosted workflow remains useful and prominent. |
| Hosted reason to choose | No installation, managed persistence, a shared workspace, team access, and pilot support. |
| First-cohort commercial path | A **team-payment qualification request** whose submission begins `pending`, not Stripe checkout, payment, or a commercial commitment. |
| Natural paid trigger | Show the team-workspace request only after the user has reviewed or exported a successful result, or when they explicitly seek collaboration. |

The existing product already supports the core loop: choose a test set, choose candidates, review
evidence, and export a review package ([README](../../README.md#the-core-workflow)). The hosted offer
must not weaken or hide the local path.

## First-cohort team pilot qualification request

The first cohort uses a non-financial `TeamPilotRequest` to collect the bounded facts needed for a
later team-payment qualification conversation because no payment-provider integration or
live-money readback is currently proven. The call to action is **Request a hosted team workspace**.

The in-product request deliberately stores only:

- requested seats from 2 to 250;
- evaluation frequency: `weekly`, `several_times_week`, `daily`, or `release_driven`;
- whether a security review is required.

The authenticated principal and workspace supply ownership; the application does not duplicate a
contact name, email address, company name, prompt, or security narrative in this commercial table.
Discovery notes separately capture buyer context with consent. The server, not the browser, owns
request status and any later entitlement. The only request states are `pending`, `canceled`,
`qualified`, and `declined`. The current member-facing API creates `pending` requests and can cancel
a pending request; it does not yet expose an operator qualification or decline transition.
`qualified` is a reserved server-owned disposition, not a payment. A browser query parameter, local
storage value, or unverified webhook must never grant access.

Stripe, card collection, invoices, automatic renewals, refunds, taxes, proration, and cancellation
billing are deferred until a provider, account, price, webhook signature path, and provider-side
readback are separately proven.

## Minimum pilot contracts

Preserve the current self-hosted API and workflow. Reuse the existing workspace, membership, role,
run, result, export, and audit contracts. The implemented commercial concepts are:

1. the code-defined `CommercialPlanRead` catalog for `open_source`, `hosted_trial`, and `team`;
2. `WorkspaceEntitlement`: workspace-scoped access, source, seat limit, status, and effective dates;
3. `TeamPilotRequest`: the first-cohort, non-financial qualification request;
4. append-only `BillingEvent`: idempotent EvalForge state-transition evidence, not provider billing;
5. append-only `ActivationEvent`: content-minimized workspace funnel evidence.

Entitlement checks occur on the API boundary for run preflight and creation only when shared OIDC
and the commercial pilot are both enabled. The dashboard may explain the current plan, start or
cancel a trial, and submit or cancel a team request after a qualifying first success, but it may
not decide access. The API enforces that post-success gate and permits only one pending request per
workspace. Existing run
history, results, and exports remain readable after access expires or is canceled.

## Activation and commercial event contract

All events are workspace-scoped, keyed for replay safety, avoid prompt/output and credential
content, and use server time for durable milestones. Server-authored transitions also retain normal
audit/request attribution where the existing audit contract applies.

| Canonical event | Current emission rule |
|---|---|
| `landing` | One authenticated dashboard session becomes usable; client-originated and workspace-scoped. It is not an anonymous public-page visit. |
| `signup` | The dashboard first observes an authenticated identity/workspace; client-originated, durably first-touch, and deduplicated across later sources/sessions. |
| `core_job_start` | The server accepts and persists a valid evaluation. |
| `evaluation_complete` | The worker completes a comparison with at least two candidates that each produced useful result evidence; canceled and all-error/non-comparison runs do not emit it. |
| `result_engagement` | The server produces an export for a run; this slice does not infer engagement from a page view. |
| `second_use` | The same authenticated user starts a later distinct run in the workspace. |
| `upgrade_view` | The enabled hosted-team offer is opened in Settings; client-originated and deduplicated. |
| `checkout_start` | Reserved for a separately proven provider checkout; it must remain absent in the first cohort. |
| `entitlement_activation` | The server persists a hosted-trial entitlement and returns it on readback. |
| `team_request_submitted` | The server accepts and persists a new `pending` team request. |

Required funnel measures are authenticated entries, first authenticated signups, core-job starts,
qualifying completed evaluations, activated users, second use, upgrade views, submitted team
requests, entitlement activations, and acquisition source. Anonymous public-page visits require a
separate public-boundary analytics source and are not inferred from `landing`. The API reports
workspace event counts, unique actors, first-touch acquisition sources, same-actor
completed-and-engaged run count, request counts, first/last event times, and signup-to-activation
p50/p90 seconds with sample and excluded-actor counts. A submitted `pending` request is not counted
as qualified until a separately implemented operator disposition exists.

## Frozen non-goals

- New applications such as NoteFold, Remindly, or BriefKit.
- All Access or portfolio-wide identity, billing, and entitlement infrastructure.
- Stripe or another live payment provider before separate provider proof.
- CRDT collaboration, mobile applications, browser extensions, advanced scheduling, or WFQ work.
- A new multi-product platform, universal design-system rewrite, or mass branding asset campaign.
- Automatic paid-provider retries, opaque model routing, or client-authoritative access.

## Fourteen-day execution contract

| Days | Work | Exit evidence |
|---|---|---|
| 1–2 | Lock product, buyer, offer, activation, non-goals, five hosted blockers, and decision record. | Canonical decision, this offer, buyer tracker, and exactly-five-blocker audit are committed together. No external outcome is inferred from documentation. |
| 3–5 | Deploy the current FastAPI/Streamlit/PostgreSQL topology; configure HTTPS OIDC; provision two test workspaces; complete one hosted deterministic evaluation. | Immutable deployment identifier; TLS URL; live/ready readback; real login/logout; role and cross-tenant denial proof; completed run, evidence view, and export in the hosted environment. |
| 6–7 | Publish OSS-versus-hosted packaging, add the post-success team-workspace request, and make entitlements server-authoritative. | Local OSS path remains visible; trial and request submit/readback work; cancellation and entitlement readback are audited. Operator qualification remains a separate closure if the pilot needs it. No payment claim is made. |
| 8–10 | Produce a captioned demo and before/after proof; send targeted developer and company messages; conduct focused listening and outreach. | One reviewed demo, one proof artifact, source-tagged distribution log, 10 named contacts, and 5 completed conversation records—or an explicit shortfall. |
| 11–13 | Remove the largest observed activation problem and measure the funnel. | Evidence ties one change to an observed problem; activation and second-use metrics include denominators and acquisition source. |
| 14 | Write the Kill, Continue, or Scale decision. | Decision record cites external activation, buyer evidence, commercial intent, proof boundaries, risks, and the next bounded cycle. |

The hosted MVP must fit within six build days. If the five hosted blockers cannot be closed within
that budget, EvalForge fails hosted feasibility for this cycle and the portfolio decision record
must evaluate the named backup rather than silently widening scope.

## Acceptance gates

### Local OSS gate

- The deterministic, credential-free self-hosted run still works.
- A user can compare candidates, inspect evidence, and export a review package.
- Local proof is reported as local proof, never hosted or production proof.

### Hosted product gate

- A real hosted identity-provider journey and two-workspace isolation test pass.
- A hosted user activates in under 10 minutes without a model-provider credential.
- PostgreSQL persistence survives service restart and an operator proves backup and restore.
- Desktop and mobile Playwright journeys cover login, activation, tenant denial, request submission,
  entitlement readback, and request/trial cancellation semantics.

### Commercial gate

- The OSS/self-host path is prominent and credible.
- The hosted team value is understandable after first success.
- A qualified-team-pilot request can be submitted as `pending`, read back, and canceled; any later
  `qualified` or `declined` disposition must remain server-authoritative and separately proved.
- A hosted trial activates a server-authoritative entitlement. A team request does not activate an
  entitlement or imply payment in this slice.
- No UI or launch copy implies card payment, subscription, or production readiness without proof.

### Continuation gate

Continuation requires at least one external activation, understandable first use, sub-10-minute
value for qualifying sessions, clear hosted/team interest, a credible OSS path, and a working team
request path. Scaling additionally requires repeated activation plus either a real payment under a
separately proven provider path or a highly qualified team commitment.

## Proof boundaries

Evidence is recorded separately as source/configuration, local automated, local browser, hosted,
identity-provider, database-operations, commercial-request, payment-provider, and production proof.
A green source test does not establish a hosted login; a hosted request does not establish payment;
and a local PostgreSQL test does not establish production recovery.
