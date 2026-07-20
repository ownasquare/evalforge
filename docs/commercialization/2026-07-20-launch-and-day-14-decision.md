# EvalForge pilot launch, distribution, and Day-14 decision record

Date opened: 2026-07-20
Status: **Local OSS captioned demo proof recorded; no hosted demo, launch message publication, outreach, external activation, qualified team request, or payment is claimed.**

The Own A Square 1,000-app mission is the long-term brand story. This launch is deliberately about
one proved job: comparing prompts or models against the same test set before shipping. Every asset
must keep the open-source/self-host path visible and present the hosted team workspace as an optional
pilot, not a replacement or a production-readiness claim.

## Launch claim boundary

Allowed claims must be supported by the current product or new recorded proof:

- EvalForge is MIT-licensed and can run a deterministic demo locally without a model-provider key.
- A user can compare candidates, inspect evidence, and export a review package.
- Hosted availability, real identity-provider behavior, production PostgreSQL recovery, external
  user activation, and team entitlements may be claimed only after their separate evidence exists.
- The first cohort offers a pending team-pilot qualification request. Submission does not mean the
  request is qualified, accepted, paid, or entitled.

## Captioned demo: 55-second storyboard

Use a clean workspace and non-sensitive deterministic data. Burn captions into the export and review
the final frame-by-frame render before distribution.

| Time | Visual | Caption/narration | Evidence required |
|---|---|---|---|
| 0–5s | Two prompt/model candidates and one test set | “Changing a prompt is easy. Proving the change is safer to ship is harder.” | No customer data. |
| 5–12s | EvalForge workspace and New evaluation | “EvalForge compares candidates against the same test cases.” | Hosted URL only if hosted proof passed; otherwise label local demo. |
| 12–22s | Select at least two candidates and start | “Start with the credential-free deterministic workflow—no model API key required.” | Settings/capabilities confirm deterministic mode. |
| 22–32s | Completed evaluation and candidate summary | “See correctness, relevance, groundedness, hallucination risk, speed, and known cost evidence.” | Completed run and visible candidate labels. |
| 32–43s | Open per-case result evidence and comparison | “Inspect exactly where a candidate improved or regressed.” | Real persisted result; no mock browser response. |
| 43–50s | Copy or export the review package | “Keep a reviewable artifact for the release decision.” | Export receipt or copy interaction. |
| 50–55s | OSS link plus optional hosted-team request | “Self-host the open-source project, or request a managed team workspace.” | Correct repository link; request CTA only after its path is proven. |

Do not splice a local run into a hosted frame without labeling the boundary. Do not show provider
keys, bearer tokens, private prompts, email addresses, or unredacted identity-provider dashboards.

## Before/after proof card

Complete with observed evidence. Leave unknown values blank rather than estimating them.

| Field | Before | After | Evidence |
|---|---|---|---|
| Workflow | Not observed; buyer baseline still required | One local comparison from setup through evidence export | [Local demo proof](2026-07-20-local-demo-proof.md) |
| Tools/tabs used | Not observed | One EvalForge browser tab plus local API readback | [Local demo proof](2026-07-20-local-demo-proof.md) |
| Time from start to reviewable evidence | Not observed | 47.863 seconds from first run event to result engagement | Local funnel timestamps in [demo proof](2026-07-20-local-demo-proof.md) |
| Candidate/test-case consistency | Not observed | Two model candidates shared the same five cases; 10 of 10 results persisted | [Completed comparison frame](../assets/commercialization/2026-07-20/evalforge-local-results-export-desktop-1440x1000.png) |
| Result sharing/export | Not observed | JSON preparation succeeded and the browser observed a download | [Export frame](../assets/commercialization/2026-07-20/evalforge-local-results-export-desktop-1440x1000.png) |
| Team access | Not observed | Local single-owner workspace only; hosted team access unproved | [Desktop Settings frame](../assets/commercialization/2026-07-20/evalforge-local-settings-desktop-1440x1000.png) |
| Known limitations | Not observed | Deterministic local data, synthetic latency/cost, no hosted/IdP/payment/customer proof | [Local demo proof](2026-07-20-local-demo-proof.md) |

The card must name whether the proof is internal local, internal hosted, or external user evidence.
It must not turn synthetic deterministic latency or cost into a customer outcome.

## Asset checklist

| Asset | Owner | Evidence link | Status |
|---|---|---|---|
| Captioned 55-second demo | Own A Square | [Local OSS captioned sequence](2026-07-20-local-demo-proof.md) | Local proof complete; hosted recording open |
| Desktop poster frame | Own A Square | [Completed comparison](../assets/commercialization/2026-07-20/evalforge-local-results-export-desktop-1440x1000.png) | Local proof complete |
| Mobile hosted-flow frame | Own A Square | [Local Settings at 390×844](../assets/commercialization/2026-07-20/evalforge-local-settings-mobile-390x844.png) | Local layout complete; hosted flow blocked on deployment |
| Before/after proof card | Own A Square | [Evidence above](#beforeafter-proof-card) | Local “after” complete; buyer “before” open |
| OSS/self-host landing section | Own A Square | [Repository README](../../README.md) | Source copy complete; publication pending |
| Hosted-team request landing section | Own A Square | [Desktop Settings frame](../assets/commercialization/2026-07-20/evalforge-local-settings-desktop-1440x1000.png) | Source/UI complete; hosted action unproved |
| Developer launch message |  |  | Draft only |
| Company/team launch message |  |  | Draft only |
| Source-tagged outreach list |  |  | Not started |
| Public Own A Square GitHub profile | Own A Square owner | <https://github.com/ownasquare> | Blocked — the authenticated CLI token lacks profile-write `user` scope, and the in-app browser is signed out |

### Public profile alignment

Current readback on 2026-07-20 still describes Own A Square primarily as a consulting business. The
intended replacement bio is:

> Own A Square builds focused, open-source AI tools. EvalForge helps teams compare prompts and models
> before release. Hosted team pilot in progress.

This is approved launch copy, not mutation proof. Do not mark the profile asset complete until an
authorized profile edit is followed by public unauthenticated readback. The current CLI identity is
`ownasquare`, but its token has repository scopes only and no profile-write `user` scope. The
in-app browser is signed out, so this execution did not change the public profile.

## Draft launch messages

These are drafts, not publication evidence. Add only links and claims that have passed their proof
gate.

### Developer-focused

> I built EvalForge to make one release question easier: did this prompt or model candidate actually
> improve on the same test cases? The open-source workflow runs locally with deterministic examples
> and no model API key, then lets you inspect per-case evidence and export a review package. I’m also
> opening a small pilot for teams that want to request a managed shared workspace. Try the OSS project:
> [repository link]. If your team repeats this workflow, request the hosted pilot: [request link].

### Company/team-focused

> Teams shipping LLM features often compare prompts and models across scripts, spreadsheets, and
> individual accounts. EvalForge gives the team one test set, a side-by-side candidate comparison,
> inspectable result evidence, and a reviewable export. The self-hosted project remains free and open
> source. We’re recruiting a small first cohort for managed team workspaces with shared persistence
> and support—no card is collected in the qualification request. [proof/demo link] [request link]

## Distribution plan for Days 8–10

Targeted listening comes before volume. Each interaction must respond to an observed problem; generic
promotion does not count as a useful reply.

| Day | Action | Minimum | Actual | Evidence/source | Status |
|---|---|---:|---:|---|---|
| 8 | Finalize demo, proof card, landing copy, and source tags | 4 launch assets | 0 |  | Not started |
| 9 | Listen for active prompt/model evaluation discussions and write useful replies | 5 useful replies | 0 |  | Not started |
| 9 | Participate in a limited number of relevant communities under their rules | 1–2 communities | 0 |  | Not started |
| 10 | Send personalized team outreach | 10 contacts total | 0 |  | Not started |
| 10 | Complete buyer conversations | 5 by checkpoint | 0 |  | Not started |

A useful reply must answer the original question, include concrete evaluation guidance, disclose
affiliation when EvalForge is linked, and avoid false urgency. Track each LinkedIn, GitHub, community,
email, or direct-referral source independently.

## Distribution and funnel log

| Date/time UTC | Channel | Source/campaign | Audience | Asset/message | `landing` | `signup` | `core_job_start` | Activated runs | `second_use` | `team_request_submitted` | `qualified` | Evidence |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  |  |  |  |  |  |  |  |  |  |  |  |  |

Report unique counts, bot/internal exclusions, attribution window, and denominator. Do not merge an
internal test account into external activation. An authenticated `landing` event is not evidence of
an anonymous visit, so public traffic requires separate privacy-safe web analytics. Acquisition source
is durable first-touch from the actor's first `signup`. A team request is not qualified until the
discovery rubric and a server-owned `qualified` disposition are recorded. An activated run is a run
ID where the same authenticated actor first records a qualifying `evaluation_complete` and then a
server-recorded export as `result_engagement`; the comparison must contain successful result evidence
for at least two candidates. A page view alone is not result engagement.

## Days 11–13 activation experiment

Make one primary change based on the largest observed friction. Do not stack unrelated features and
then claim causality.

- Observation and evidence:
- Affected funnel step:
- Baseline dates, numerator, denominator, p50, and p90:
- Chosen change:
- Why this is the smallest adequate change:
- Release/deployment identifier:
- Post-change dates, numerator, denominator, p50, and p90:
- External participant count:
- Result and confidence:
- Regressions or counterevidence:
- Keep, revise, or revert:

## Day-14 evidence ledger

Complete every row before choosing a decision.

| Signal | Result | Numerator/denominator | Evidence | Proof class | Confidence |
|---|---|---|---|---|---|
| Targeted contacts reached |  |  |  | External distribution |  |
| Buyer conversations completed |  |  |  | External discovery |  |
| External `signup` actors |  |  |  | Hosted |  |
| External `core_job_start` events |  |  |  | Hosted |  |
| External activations |  |  |  | Hosted |  |
| Activation p50/p90 |  |  |  | Hosted |  |
| External `second_use` actors |  |  |  | Hosted |  |
| `upgrade_view` actors |  |  |  | Hosted |  |
| Team-pilot requests submitted (`pending`) |  |  |  | Commercial request |  |
| Qualified team requests |  |  |  | Discovery + commercial request |  |
| Accepted team commitments |  |  |  | Commercial commitment |  |
| Entitlements activated/read back |  |  |  | Hosted API |  |
| Payments collected |  |  |  | Payment provider |  |
| Security objections |  |  |  | External discovery |  |
| Largest activation problem |  |  |  | Mixed |  |

Payments collected should remain zero/none unless a separately authorized provider transaction and
provider-side readback exist. A `pending` or `qualified` request must never be relabeled as payment;
the only request states are `pending`, `canceled`, `qualified`, and `declined`.

## Decision rules

### Kill

Choose Kill when real, targeted distribution produces no external activation or the buyer interviews
do not establish a recurring company pain. Also choose Kill when the hosted hard gates remain
infeasible inside the pilot budget. Preserve the OSS project; stop commercial build work; record what
was learned and which assumptions failed.

### Continue

Choose Continue when external activation and hosted/team interest exist, but packaging, activation,
security, or the commitment path needs one more bounded cycle. Name one bottleneck, one experiment,
one timebox, and a stricter next decision date.

### Scale

Choose Scale only after repeated external activation plus either a provider-proven payment or a highly
qualified team commitment with decision maker, seats, timing, security fit, and a concrete commercial
next step. Internal tests, social engagement, waitlist entries, or one unqualified request are not
sufficient.

## Written decision

- Decision date:
- Decision owner:
- Selected outcome: **Unselected — Kill / Continue / Scale**
- One-sentence rationale:
- Evidence supporting the decision:
- Evidence against the decision:
- External activation and second-use summary:
- Buyer-pain and security summary:
- Commercial request/payment truth:
- Hosted and production proof still missing:
- Scope to stop immediately:
- Next bounded action and deadline:
- Decision review date:

The record is incomplete until an outcome is selected and every cited result has a durable evidence
location. Silence, an unfinished deployment, or internal enthusiasm is not a Continue or Scale
decision.
