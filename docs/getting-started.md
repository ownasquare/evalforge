# Getting started

This guide takes you from a fresh local clone to a completed offline comparison. No API key,
container runtime, or external model is required.

## 1. Check the prerequisites

Install:

- Python 3.11 or 3.12; and
- [uv](https://docs.astral.sh/uv/).

Confirm both are available:

```bash
python3 --version
uv --version
```

## 2. Start the demo

From the repository folder:

```bash
uv sync --frozen
uv run evalforge demo
```

The demo command applies database migrations, adds sample benchmarks, prompts, and offline model
profiles, then starts both services. It does not make a network call to a model provider.

Open `http://127.0.0.1:8501`. Leave the terminal running while you use the dashboard.

## 3. Run your first evaluation

1. Select **New evaluation**.
2. Choose a sample benchmark.
3. Keep at least two seeded candidates selected.
4. Confirm which prompt/model pair is the baseline.
5. Start the evaluation.

The deterministic profiles intentionally behave differently, so the result should contain a useful
comparison without depending on a changing external service.

## 4. Read the result

Begin with the outcome summary, then check:

- **Compare** for challenger wins, ties, and regressions on shared cases;
- **Case-level evidence in Results** for the exact output, expected answer, source context, and
  metric evidence; and
- latency and cost beside quality rather than folded into a single score.

An unavailable metric is not a failure. It means the test case did not include the evidence that
metric needs. For example, correctness needs a reference answer and groundedness needs context.

## 5. Try your own benchmark

Open **Benchmarks** under **Library**, create a benchmark, and add cases directly or import one of
the formats documented in the [API contract](api.md). Good cases usually include:

- a clear input;
- an expected answer when correctness matters;
- source context when groundedness matters; and
- only the formatting or phrase requirements the product actually needs.

Start with a small set of representative cases. Add edge cases and known regressions as you learn.

## Stop and resume

Press `Ctrl+C` in the demo terminal to stop both services. Your local SQLite data remains under the
configured data directory, so the next `uv run evalforge demo` resumes the same workspace and safely
re-applies the idempotent sample seed.

If startup fails, use the [troubleshooting guide](troubleshooting.md). When you are ready for a real
provider or shared deployment, continue with [Operations](operations.md).
