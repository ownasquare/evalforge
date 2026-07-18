# Extending EvalForge

EvalForge exposes small Python contracts for model generation, optional evaluators, and evidence
export. These are currently **source-level extension points**: EvalForge does not scan installed
packages, load entry points, or offer a dashboard plugin installer. Shipping an extension requires
wiring it into a source build and testing the complete path.

Tested, minimal implementations live in [`examples/extensions/`](../examples/extensions/README.md).

## Choose the right extension point

| Goal | Contract | Current runtime wiring |
| --- | --- | --- |
| Call another model backend | `ModelAdapter` | Register it in `AdapterRegistry` and wire configuration/model validation into the application container. |
| Experiment with another judge | `AsyncEvaluator` | Call it through `run_evaluator`; the main run service does not automatically include external evaluators. |
| Send evidence somewhere else | `ExportSink` | Implement the protocol, then replace or extend the CLI/API export wiring that currently uses `LocalFileSink`. |
| Add a built-in deterministic metric | `MetricRegistry` | Add a versioned implementation and connect it to run configuration, aggregation, docs, and tests. |

That distinction matters: satisfying a protocol proves shape and behavior, not that the dashboard
can discover or configure the object.

## Add a model adapter

A model adapter implements one asynchronous method:

```python
async def generate(self, request: GenerationRequest) -> GenerationResponse: ...
```

Start with [`custom_adapter.py`](../examples/extensions/custom_adapter.py). Then:

1. Put the adapter in an importable module and add its client dependency deliberately.
2. Register one stable provider name with `AdapterRegistry.register()` during container setup.
3. Add server-side settings and an allowlist for every supported model/API mode.
4. Keep credentials in backend settings. `GenerationRequest` must never carry a secret or base URL.
5. Generate only from the intended prompt fields. Never use or transmit `expected_output`; it is a
   gold/reference answer reserved for evaluation. Do not forward evaluator-only metadata or duplicate
   raw context unless the provider contract deliberately requires it and preflight discloses it.
6. Return normalized usage, latency, request identity, finish reason, and sanitized errors.
7. Add contract tests with a fake transport. Keep paid network tests explicitly marked `live`.
8. Expose the provider in model-profile administration only after readiness and preflight agree.

An adapter must make exactly the request selected by `api_mode`; it must not silently fall back to a
different provider API after a failure. Surface ambiguous billable outcomes instead of replaying them.

## Add an evaluator

`AsyncEvaluator` declares what it sends and what it may cost before it runs. An offline evaluator
declares zero calls, no transmitted fields, and no cost. An external evaluator declares its call
count, transmitted fields, and bounded or unknown cost behavior.

[`custom_evaluator.py`](../examples/extensions/custom_evaluator.py) shows an offline word-limit
check. Execute evaluators through `run_evaluator()` so result names and versions are verified against
the declaration.

The primary evaluation service currently scores runs through `MetricRegistry`; it does not discover
or invoke `AsyncEvaluator` implementations. To include an evaluator in normal runs, add explicit
service configuration, preflight accounting, user acknowledgement for external data/cost, result
persistence, and UI/API coverage. Do not describe a standalone evaluator as installed until that
wiring exists.

## Add an export sink

An `ExportSink` accepts an immutable `ExportPackage` and returns an `ExportReceipt`. The receipt's
hash is the idempotency identity. [`custom_export_sink.py`](../examples/extensions/custom_export_sink.py)
shows an in-memory teaching implementation.

A production sink should:

- validate the package type and content hash;
- use the package hash as an idempotency key;
- return a stable, non-secret location;
- preserve the chosen disclosure profile;
- fail without silently downgrading integrity or disclosure; and
- avoid logging prompt, context, output, or credentials.

The CLI currently creates `LocalFileSink` directly. A new sink needs explicit configuration and
wiring there (or in a new API route), plus retry and readback tests appropriate to the destination.

## Add a built-in metric

Built-in metrics are not loaded through a plugin registry. To add one:

1. Give it a stable name and version in `METRIC_VERSIONS`.
2. Return `not_applicable` when required evidence is absent.
3. Keep scores finite and between 0 and 1, with an explicit direction and threshold.
4. Include concise, inspectable evidence and a plain-language reason.
5. Add default configuration only after deciding its weight and applicability semantics.
6. Cover exact, boundary, missing-evidence, Unicode, and adversarial cases.
7. Document the formula and limitations in [Evaluation methodology](evaluation-methodology.md).

Change the version whenever math, normalization, direction, threshold meaning, or evidence shape
changes. Do not overwrite the meaning of historical run snapshots.

## Validate an extension

Run the example contract tests and the full local gate:

```bash
uv run --all-groups pytest tests/contract/test_extension_examples.py -q
make check
```

For a runtime-wired adapter or sink, also prove configuration failure, credential redaction,
readiness, cancellation/interruption behavior, and the user-visible path that selects it.
