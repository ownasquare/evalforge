# EvalForge buyer discovery tracker and interview guide

Date opened: 2026-07-20
Pilot target: 10 relevant contacts and 5 completed buyer conversations
Current external status: **0 contacts selected, 0 outreach messages sent, 0 replies, and 0 conversations completed.**

This document is the evidence record for discovery. Empty fields and `Not started` are intentional;
they must not be converted into inferred activity. Do not record credentials, private model inputs,
customer prompts, or non-consensual interview recordings.

## Target profile

Prioritize people who can describe or influence a recurring team evaluation workflow:

- engineering leaders at small companies shipping LLM features;
- AI/ML engineers who compare prompt or model candidates before release;
- product leaders accountable for LLM quality or regression risk;
- developer-tool or platform leads responsible for shared test evidence;
- founders with a small team and an active LLM product, not idea-stage consumers.

Disqualify a lead when there is no company/team use case, no recurring evaluation job, no authority or
access to the workflow, or the only request is unrelated consulting.

## Ten-contact tracker

Do not count a row as contacted until a message is actually sent through an identified channel.

| ID | Person | Company/team | Role | Why this workflow may fit | Source/channel | First touch (UTC) | Status | Reply/evidence link | Owner |
|---|---|---|---|---|---|---|---|---|---|
| T01 |  |  |  |  |  |  | Not started |  |  |
| T02 |  |  |  |  |  |  | Not started |  |  |
| T03 |  |  |  |  |  |  | Not started |  |  |
| T04 |  |  |  |  |  |  | Not started |  |  |
| T05 |  |  |  |  |  |  | Not started |  |  |
| T06 |  |  |  |  |  |  | Not started |  |  |
| T07 |  |  |  |  |  |  | Not started |  |  |
| T08 |  |  |  |  |  |  | Not started |  |  |
| T09 |  |  |  |  |  |  | Not started |  |  |
| T10 |  |  |  |  |  |  | Not started |  |  |

Allowed status values: `Not started`, `Selected`, `Sent`, `Replied`, `Scheduled`, `Completed`,
`Declined`, `No response`, and `Disqualified`. Record dates and evidence links instead of relying on
memory. A social reply is not a buyer conversation unless the interview evidence below is captured.

## Five-conversation ledger

| Slot | Target ID | Participant/role | Scheduled (UTC) | Completed (UTC) | Consent to notes | Evidence location | Status |
|---|---|---|---|---|---|---|---|
| C01 |  |  |  |  |  |  | Not started |
| C02 |  |  |  |  |  |  | Not started |
| C03 |  |  |  |  |  |  | Not started |
| C04 |  |  |  |  |  |  | Not started |
| C05 |  |  |  |  |  |  | Not started |

## Interview guide

Target length: 20–30 minutes. Spend most of the call on the current behavior and last real example;
show the product only after the workflow is understood.

### Opening

1. Confirm the participant's role and whether they influence the team's LLM release workflow.
2. Ask permission to take written notes. Do not record audio or video without explicit permission.
3. Explain that this is product discovery, not a sales commitment or paid-service activation.

### Current workflow

1. Tell me about the last prompt or model change your team evaluated before release.
2. What test cases did you use, who ran them, and where were the results kept?
3. How did you compare candidates and decide whether the change was safe to ship?
4. Who needed to review or approve the evidence?
5. How often does this happen in a typical month?

### Pain and alternatives

1. What took the most time or caused the most uncertainty in that last evaluation?
2. What breaks when results live in notebooks, spreadsheets, scripts, or individual accounts?
3. What have you tried already? What do you pay for today, if anything?
4. What would make a shared evaluation record meaningfully better than your present process?
5. What would make you reject a hosted tool even if the workflow looked useful?

### Security and operations

1. Which data could never be sent to a hosted service?
2. Do you require a particular identity provider, role model, audit trail, retention period, region,
   vendor review, or data-processing agreement?
3. Would credential-free deterministic evaluation be useful for a first trial?
4. Would your team bring its own model-provider credentials later, and under what controls?

### Hosted and team value

1. Who would need seats in a shared workspace?
2. Is no-install managed persistence valuable enough to change the current workflow? Why or why not?
3. What result would a pilot need to produce in its first 10 minutes?
4. Would you request a hosted team pilot if it required qualification but no card today?
5. If the pilot worked, who could approve a paid team plan and what evidence would they need?

### Close

1. Summarize the workflow and pain back to the participant and ask what was misunderstood.
2. Ask whether a follow-up pilot is appropriate; do not manufacture urgency.
3. Ask for one relevant referral only when the participant sees a real fit.

## Conversation capture template

Duplicate this section once per completed conversation. A scheduled or cancelled call does not count.

### Conversation C__

- Target ID:
- Participant role and team size:
- Date/time (UTC):
- Note consent:
- Evidence location:
- Most recent real evaluation example:
- Frequency of the job:
- Current tools and workflow:
- Largest recurring pain:
- Consequence of getting it wrong:
- Existing spend or approved budget process:
- Security and compliance constraints:
- Hosted interest and reason:
- Expected seat band:
- Sub-10-minute success definition:
- Qualified-team-request interest:
- Decision maker and procurement path:
- Strongest direct wording, paraphrased unless quotation permission is recorded:
- Contradicting or disconfirming evidence:
- Qualification: Unscored
- Next step:

## Qualification rubric

Score only after a completed conversation. Use `0` when evidence is absent, not an optimistic guess.

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| Recurrence | No recurring evaluation | Occasional/ad hoc | Weekly or release-linked job |
| Pain | No meaningful consequence | Friction but tolerable | Delay, risk, rework, or blocked release |
| Company fit | Individual curiosity | Team relevance unclear | Active team workflow and responsible owner |
| Hosted value | Prefers local only | Conditional interest | Clear no-install/shared-persistence value |
| Seat value | One user only | Possible collaborator | Multiple named roles or reviewers |
| Commercial path | No approval path | Path uncertain | Decision maker/process and timing identified |

Interpretation: 9–12 is qualified, 6–8 merits follow-up, and 0–5 is not qualified for this pilot. A
high score does not equal payment. A submitted `TeamPilotRequest` begins in `pending`; it is a
commercial-intent signal only until a server-owned qualification, separately proven agreement, and
payment path exist.

## Discovery checkpoint

After 10 actual first touches and 5 completed conversations, summarize:

- contacts selected / messages sent / replies / completed conversations;
- repeated workflow and pain patterns, including counts and counterexamples;
- existing alternatives and spend evidence;
- security constraints that affect hosted feasibility;
- hosted interest, seat interest, qualified requests, and explicit rejections;
- the single largest activation risk to test on Days 11–13.

If five conversations are not completed, record the shortfall and channel evidence. Do not widen the
product scope or call the discovery milestone complete.
