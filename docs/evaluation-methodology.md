# Evaluation methodology

EvalForge's built-in evaluator is deterministic, versioned, and evidence-producing. It is designed
for regression detection and transparent prompt/model comparison. Lexical metrics do not establish
factual truth or replace human calibration.

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

## Calibration guidance

- Create cases from real failure modes, not only happy paths.
- Keep a human-reviewed calibration subset and compare metric decisions against it.
- Prefer pairwise regression decisions and task-specific thresholds over one universal score.
- Review changed or failed cases directly; an aggregate can hide a severe localized regression.
- Version the dataset, prompts, metrics, judge rubric, model, and pricing together.
- Add RAG-focused plugins only when cases contain retrieval context and the plugin version is stored.

Optional LLM-as-judge evaluation should be treated as a separate provider-backed metric with its
judge model, API mode, rubric bytes/hash, structured schema, and calibration version persisted. It
must not replace the offline core or execute during default CI.
