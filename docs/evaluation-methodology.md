# Evaluation methodology

EvalForge's built-in evaluator is deterministic, versioned, and evidence-producing. It is designed
for regression detection and transparent prompt/model comparison. Lexical metrics do not establish
factual truth or replace human calibration. Provider-neutral evaluator declarations and offline
calibration reports make future judge integrations explicit without turning the default test suite
into a paid or networked workflow.

## Result contract

Every metric records:

- stable name and formula version;
- numeric value when applicable;
- `higher_is_better` or `lower_is_better` direction;
- unit and threshold;
- `applicable`, `not_applicable`, or `error` state;
- pass state when a threshold applies;
- plain-language explanation and structured evidence.

Not-applicable metrics have no numeric value. They are excluded from denominators rather than
silently becoming zero.

## Optional evaluator declarations

Every `AsyncEvaluator` implementation declares before execution:

- stable name and version;
- offline or external execution;
- calls per case;
- no, bounded, or unknown cost behavior;
- exact fields it transmits from input, output, reference, and context;
- maximum cost per case when cost is declared bounded.

Offline evaluators must declare zero calls, zero transmitted fields, and no cost. External
evaluators must declare at least one call and at least one transmitted field. The runner verifies
that returned metric name and version match the declaration. This is a contract foundation, not a
claim that an external judge is registered or calibrated.

## Correctness

Correctness requires a reference answer. After Unicode-aware normalization, exact equality scores
`1.0`. Otherwise the metric combines token F1 and sequence similarity:

```text
correctness = 0.60 * token_f1 + 0.40 * sequence_similarity
```

The evidence includes normalized exact match, token precision/recall/F1, and the similarity term.
This makes paraphrase handling more forgiving than exact match while remaining deterministic.

## Relevance

Relevance uses explicit case keywords when present. It otherwise falls back to meaningful reference
terms, then meaningful input terms. The score combines response coverage of target terms with a
lexical similarity term. This is a documented lexical heuristic, not an embedding or human semantic
judgment.

## Groundedness

Groundedness requires source context. The response is split into claim-like sentences; each claim is
matched to its best context chunk by normalized token overlap. Evidence includes each claim, its best
support score, and the supporting chunk index. Empty context returns `not_applicable`.

The current formula identifier is `claim-support-v2`. Ordered context chunks are preserved from
import through run snapshot and evidence, so the reported chunk index remains auditable.

## Hallucination risk

Hallucination risk is a separate, lower-is-better metric. It weights unsupported claims and applies
additional evidence flags for unsupported numbers, URLs, and factual-looking anchors. It is not
defined as only `1 - groundedness`, so a concise unsupported numeric assertion can carry more risk
than a generic low-overlap sentence. Without context or a factual reference, risk is
`not_applicable`; EvalForge does not pretend to detect hallucinations without evidence.

The current risk formula identifier is `unsupported-anchor-risk-v2`. Reference answers may inform
the evaluator when context is absent, but they are never available as candidate prompt placeholders.

## Phrase coverage and constraints

Required phrase coverage reports the fraction of case-required phrases present after normalization.
Constraint/style adherence starts at `1.0` and records named penalties for configured maximum word
count, required/forbidden phrases, duplicate phrasing, malformed structured output, and other
explicit case rules. JSON validity optionally validates a declared JSON Schema and returns parser or
schema evidence without exposing a stack trace.

The constraint and style formula identifiers are `explicit-constraints-v2` and
`deterministic-style-v2`; newline-delimited bullets are retained as distinct sentences for these
checks.

## Quality summaries

Operational measures such as latency and cost are not averaged into quality. A run may request an
explicit quality weighting policy; the summary averages only applicable selected quality metrics
and records weights, included names, excluded names/reasons, and effective denominator. Comparison
views still show each metric independently.

## Latency, usage, and cost

Real latency uses a monotonic clock around one provider request. Demo latency is deterministic and
labeled synthetic. Provider-reported usage is stored as reported; absent usage remains unavailable
instead of being invented. Pricing is versioned with the run, and cost uses integer micro-USD.
Unknown model pricing remains unavailable rather than becoming `$0`. Quality summaries use only
successfully scored results, while operational latency, reported tokens, and known cost include all
persisted provider responses, including responses whose later scoring failed; each denominator is
shown separately.

Run preflight uses rendered UTF-8 bytes plus a configurable per-request framing margin as a bounded
safety estimate. It intentionally does not claim tokenizer equivalence or billable-token accuracy.

## Offline threshold calibration

`evalforge.evaluation.calibration` compares one declared metric threshold with a human-labeled
calibration set. Each unique item carries a finite score from `0` to `1` and a human pass/fail
decision. Direction-aware prediction produces the confusion matrix, precision, recall, and F1.
Canonical, order-independent JSON yields a calibration-set SHA-256 so a report can be tied to the
exact labels used.

Calibration reports deliberately record:

```text
evidence_kind = offline_statistical_evidence
production_validated = false
```

They do not imply the labels are representative, the threshold generalizes, or a production judge
is accurate. Sample selection, reviewer agreement, task coverage, and deployment shift still need
human governance. The optional CLI turns a versioned label manifest into a private local report:

```bash
uv run evalforge calibrate examples/calibration-labels.json --threshold 0.7 \
  --output-dir ./private-calibration
```

The JSON envelope contains `schema_version`, a dataset identity (`id`, `version`, `sha256`), a metric
identity (`name`, `version`, `direction`), and one or more labels. Each label has an `item_id`, a
finite `score` from `0` to `1`, a `human_passed` decision, and an opaque `reviewer_id`. CSV uses the
exact columns below and repeats identical dataset and metric metadata on every row:

```text
schema_version,dataset_id,dataset_version,dataset_sha256,metric_name,metric_version,direction,item_id,score,human_passed,reviewer_id
```

Use pseudonyms such as `reviewer-01`, not a name or email address. Equivalent labels produce the
same canonical manifest hash regardless of row order or JSON/CSV formatting. The report filename is
derived from its canonical payload SHA-256; writing the same report again is idempotent and returns
`already_exists` after verifying the existing bytes.

Read precision as the share of metric passes that reviewers also passed, recall as the share of
human passes found by the metric, and F1 as their balance. Always inspect the confusion matrix and
sample counts alongside those rates. The command evaluates the threshold you provide; it does not
recommend or approve one.

The proof boundary is exact:

- offline only;
- no provider call;
- no automatic threshold selection;
- no reviewer-agreement claim; and
- no production-validation claim.

The included label files are copyable fixtures, not evidence that a human review was completed.

For a completed dashboard run, turn on **Human calibration** to create the same class of report with
stronger provenance binding. The CSV template follows the visible case order and includes the case
label shown in Results, so reviewers can reliably match each row to the model output. API clients
may request the template as JSON or CSV. EvalForge then verifies every uploaded dataset, metric,
case mapping, result ID, and score against immutable run evidence. Only the derived report, its
hashes, run/candidate linkage, and actor attribution are persisted. Raw decisions and opaque
reviewer IDs remain in the reviewer's private file and are not returned by the API or dashboard.

Each upload remains a separate immutable report. EvalForge does not merge reviewer files, average
decisions, resolve disagreement, or promote a threshold. Use separate reports to preserve distinct
review sets and apply your own documented adjudication policy outside this bounded workflow.

## Calibration guidance

- Create cases from real failure modes, not only happy paths.
- Keep a human-reviewed calibration subset and compare metric decisions against it.
- Prefer pairwise regression decisions and task-specific thresholds over one universal score.
- Review changed or failed cases directly; an aggregate can hide a severe localized regression.
- Version the dataset, prompts, metrics, judge rubric, model, and pricing together.
- Add RAG-focused source extensions only when cases contain retrieval context and the evaluator
  version is stored.

Optional LLM-as-judge evaluation should be treated as a separate provider-backed metric with its
judge model, API mode, rubric bytes/hash, structured schema, and calibration version persisted. It
must declare transferred fields and cost behavior, pass the same external-transfer and spend gates,
and never replace the offline core or execute during default CI. The live contract test skips before
credentials or client creation when no judge is registered; this proves the guard boundary, not
judge quality. The protocol and guarded live contract exist, but no external judge implementation is
registered or calibrated.

## Evidence portability

The `evalforge.run-export.v1` package records the application version, metric versions, disclosure
profile, and immutable run evidence. Canonical JSON is addressed by SHA-256. `content_redacted`
is constructed from a strict safe-provenance allowlist: validated IDs, hashes, versions, statuses,
timestamps, counts, numeric metric summaries, usage, and cost may remain; known content surfaces use
fixed markers and every unknown field is omitted. `full_evidence` retains complete run content and
requires an explicit disclosure choice. Export integrity and provenance make a run reviewable
elsewhere, but do not turn heuristic output into ground truth.
