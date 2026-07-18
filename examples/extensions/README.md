# Source extension examples

These examples demonstrate EvalForge's typed contracts with no network calls or credentials:

- [`custom_adapter.py`](custom_adapter.py) implements and registers a demo-only model adapter.
- [`custom_evaluator.py`](custom_evaluator.py) implements a declared offline evaluator.
- [`custom_export_sink.py`](custom_export_sink.py) implements an idempotent in-memory export sink.

They are verified by `tests/contract/test_extension_examples.py`.

The adapter example deliberately ignores `expected_output`. Reference answers are evaluator-only
evidence and must never be used to generate, improve, or externally transmit a candidate response.

The examples are not automatically loaded by the dashboard. Read
[Extending EvalForge](../../docs/extending.md) before wiring one into a source build.
