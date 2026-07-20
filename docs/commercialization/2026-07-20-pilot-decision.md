# Own A Square commercialization pilot decision

Date: 2026-07-20
Decision status: **EvalForge primary; Dataset Foundry backup**
External proof status: **This is a product-selection record, not a hosted, payment, or customer proof claim.**

## Roadmap decision

The 1,000-application system remains the Own A Square mission and public north star. It is not the
active build backlog for this cycle. The active roadmap is the 14-day commercialization pilot for
one existing product. No NoteFold, Remindly, BriefKit, All Access, or other new application enters
scope during this experiment.

EvalForge is the primary pilot because it combines a clear company buyer, a recurring pre-release
evaluation job, an immediate credential-free demonstration, and existing workspace, role,
PostgreSQL, durable-run, audit, export, provider-control, and OIDC contracts. Dataset Foundry is the
backup because its data-team workflow and review/export demonstration are strong, while its hosted
worker, provider-cost, storage, and multi-tenant operating burden is higher.

## Rubric

Every factor is rated from 1 (poor) to 5 (excellent). The scoring sheet defines these weights:

| Code | Factor | Weight |
|---|---|---:|
| C | Company/team buyer fit | 5 |
| P | Problem severity | 5 |
| D | Demoability | 4 |
| H | Hosted feasibility | 4 |
| T | Time-to-value | 4 |
| X | Differentiation from a ChatGPT workaround | 3 |
| O | Open-source distribution leverage | 3 |
| S | Seat-billing naturalness | 3 |
| R | Current readiness | 3 |
| M | Low maintenance | 2 |

The reproducible formula is:

```text
total = (C*5) + (P*5) + (D*4) + (H*4) + (T*4)
      + (X*3) + (O*3) + (S*3) + (R*3) + (M*2)
```

The weights sum to 36, so the maximum is 180. Company fit, demoability, and hosted feasibility are
hard gates: each must score at least 4. A larger raw total cannot override a failed gate.

## Fifteen-candidate record

Scores are ordered `C/P/D/H/T/X/O/S/R/M`. The candidate set contains the 15 product repositories
reviewed for the pilot; supporting folders, examples, documentation-only folders, and unrelated
historical repositories are excluded.

| Eligible rank | Product | Repository | Scores | Total | Hard gate | Decision evidence |
|---:|---|---|---|---:|---|---|
| 1 | EvalForge | `llm-evaluation-dashboard` | `5/4/5/5/5/4/5/5/4/4` | **167** | PASS | Explicit team evaluation job, deterministic first run, and mature tenant and run contracts. Hosted IdP and production operations remain unproved. |
| 2 | Dataset Foundry | `dataset-foundry` | `5/4/5/4/4/4/5/4/5/3` | **157** | PASS | Strong data-team generation, review, and export loop. A narrow single-team pilot is feasible, but managed workers, object storage, provider cost, and tenant operations add risk. |
| 3 | TraceLedger | `llm-observability-platform` | `5/5/4/4/4/4/5/4/4/3` | **155** | PASS | Severe LLM FinOps pain, metadata-only ingestion, SDK, alerts, and PostgreSQL foundations. Identity, workspace seats, and production deployment are missing. |
| — | PatchScope | `patchscope` | `5/5/5/3/5/3/5/4/4/3` | **156** | FAIL H=3 | Excellent code-review pain and evidence demo, but shared hosting of sensitive source needs hardened tenant storage, private-repository identity, and abuse controls. |
| — | Relay | `human-in-the-loop-agent` | `5/5/5/2/4/5/5/5/4/2` | **155** | FAIL H=2 | Compelling approval-first company workflow and natural seats, but no hosted tenancy and high-risk external-write connectors. |
| — | Handoff Forge | `ai-harness-handoff-system` | `4/4/5/3/5/5/5/4/4/4` | **154** | FAIL H=3 | Distinctive continuity workflow, but value depends on private local files, installed coding tools, and a single-user architecture. |
| — | Document Intelligence | `multimodal-document-intelligence` | `5/4/5/3/5/4/5/4/4/2` | **152** | FAIL H=3 | Strong enterprise-document workflow; hosted identity, object storage, tenant retrieval, and distributed jobs remain substantial work. |
| — | Codebase Intelligence | portfolio candidate record | `5/4/5/3/5/3/5/4/4/3` | **151** | FAIL H=3 | Clear repository question-answering value; accounts, isolation, TLS, distributed limiting, and managed secrets are absent. |
| — | Context Loom | `context-loom` | `4/3/5/2/5/5/5/3/4/3` | **140** | FAIL H=2 | Immediate context-fidelity proof, but direct access to local project folders and local harnesses is central to the value. |
| — | Research Desk | `autonomous-research-system` | `4/4/5/3/4/3/4/4/3/2` | **134** | FAIL H=3 | Useful cited-report loop, but SQLite, provider/search operations, broad competition, and dependency licensing weaken a fast hosted pilot. |
| — | Personal Library | portfolio candidate record | `4/4/4/3/4/3/5/4/3/2` | **133** | FAIL H=3 | Valuable cited team knowledge, but currently single-user/single-host with sensitive-document and multi-service operating burdens. |
| — | Atlas Agent | `atlas-agent` | `4/3/4/2/3/3/5/4/4/2` | **123** | FAIL H=2 | Mature local agent workspace, but broad positioning, tool risk, provider dependence, durable user state, and absent auth make shared hosting unsafe in this cycle. |
| — | TicketTune | `ticket-tune` | `4/4/3/2/2/4/5/2/4/2` | **117** | FAIL D=3, H=2 | Meaningful value requires training, hardware, approved data, and deployment; compute or service billing is more natural than seats. |
| — | Grok Workspace Tools | `grok-workspace-tools` | `2/2/5/1/5/4/4/1/5/2` | **110** | FAIL C=2, H=1 | Polished extension demo, but it is an individual, Grok-DOM-dependent browser tool rather than a hosted company workspace. |
| — | Privacy-First Local LLM | `privacy-first-local-llm` | `3/5/3/1/2/3/5/1/4/1` | **105** | FAIL C=3, D=3, H=1 | Hosted SaaS conflicts with the local/private promise; model downloads and hardware also slow first value. |

PatchScope's raw score of 156 exceeds TraceLedger's 155, but PatchScope is disqualified by hosted
feasibility. Eligibility is evaluated before total-score ordering.

## Locked primary and backup

### Primary: EvalForge

- Buyer: small AI engineering and product teams shipping LLM-backed features.
- Core job: compare at least two prompt or model candidates against the same test set and inspect
  the evidence before release.
- Primary surface: hosted web application.
- Activation: start a comparison, complete it, inspect result evidence, and copy or export the
  result in the same workspace.
- Target time to activation: under 10 minutes.
- Free promise: the existing useful OSS and self-hosted workflow remains prominent.
- Hosted value: no installation, managed persistence, shared workspace access, team seats, and
  pilot support.

### Backup: Dataset Foundry

Activate the backup only if EvalForge fails company fit, demoability, or hosted feasibility, or if
the hosted EvalForge MVP cannot fit within six build days. Switching candidates requires a written
update to this record; it must not happen through silent scope expansion.

## Evidence and risk boundary

The blank scoring sheet and 14-day plan were reviewed as rendered image-based PDFs in the source
workspace. Repository READMEs plus current completion and handoff records supplied implementation
evidence. For EvalForge, the most important in-repository sources are the [README](../../README.md),
[operations guide](../operations.md), [architecture](../architecture.md), and the
[v0.3.0 release record](../llm-evaluation-dashboard/2026-07-19-v0.3.0-release.md).

This decision does not prove a deployed host, live OIDC login, production PostgreSQL recovery,
model-provider authorization, payment, qualified team commitment, external activation, or buyer
conversation. Those remain separate execution and acceptance gates.
