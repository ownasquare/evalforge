# EvalForge Phase 2 Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the complete Phase 1 evaluator into a calm, trustworthy decision workspace with named evaluations, honest evidence interpretation, actionable results, and a responsive non-generic interface.

**Architecture:** Keep FastAPI as the only system of record and Streamlit as an API-only presentation layer. Extend the existing analytics payload only where the UI needs durable evidence, then reshape the Streamlit shell and pages around setup, review, run, and decision tasks. Preserve immutable snapshots, provider evidence, applicability, cost consent, and local single-user boundaries.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy, Streamlit 1.59, pandas, Plotly where directionally safe, pytest/AppTest, Playwright Browser proof, Ruff, mypy, Bandit.

---

## File map

- Modify `src/evalforge/analytics.py`: add truthful pricing coverage and bounded case-aligned comparison evidence.
- Modify `src/evalforge/dashboard/app.py`: simplify navigation labels, mobile sidebar behavior, and connection presentation.
- Modify `src/evalforge/dashboard/theme.py`: replace the gradient/purple/double-card treatment with restrained neutral workspace tokens.
- Modify `src/evalforge/dashboard/components.py`: add human metric labels, direction/target labels, and compact reusable presentation helpers.
- Modify `src/evalforge/dashboard/pages/overview.py`: remove unsupported global placeholders and emphasize recent work plus a single next action.
- Modify `src/evalforge/dashboard/pages/run_evaluation.py`: collect a run name, preview candidates/baseline, preserve preflight invalidation, and expose a results next step.
- Modify `src/evalforge/dashboard/pages/run_detail.py`: join immutable candidate labels, replace directionally misleading charts, and make result evidence easier to scan.
- Modify `src/evalforge/dashboard/pages/compare.py`: present baseline-versus-challenger evidence without invented winners or confidence.
- Modify `src/evalforge/dashboard/client.py`: expose existing JSON/CSV run export endpoints if the results toolbar is included.
- Modify `tests/dashboard/test_app.py`: prove named-run payloads and the guided preflight contract.
- Modify `tests/dashboard/test_components.py`: prove direction and target copy.
- Modify `tests/dashboard/test_run_detail.py`: prove candidate labeling and metric semantics.
- Create `tests/dashboard/test_overview.py`: prove unsupported panels and false zero-spend claims are gone.
- Create `tests/dashboard/test_compare.py`: prove baseline labels, denominators, deltas, and no fabricated recommendation.
- Modify `tests/integration/test_analytics.py`: prove overview pricing coverage and bounded paired-case evidence.
- Modify `tests/e2e/test_dashboard_smoke.py`: exercise the renamed task flow.
- Modify `docs/api.md`, `README.md`, and `docs/operations.md` only where user-facing contracts or route behavior change.

### Task 1: Lock the product-truth contracts with tests

**Files:**
- Modify: `tests/dashboard/test_app.py`
- Modify: `tests/dashboard/test_components.py`
- Create: `tests/dashboard/test_overview.py`
- Create: `tests/dashboard/test_compare.py`
- Modify: `tests/dashboard/test_run_detail.py`
- Modify: `tests/integration/test_analytics.py`

- [ ] **Step 1: Add a failing named-run setup test**

Extend the existing AppTest matrix fixture so the run name is filled before preflight and both requests must carry the same name:

```python
run_name = app.text_input[0]
run_name.set_value("Grounded support answers — July 18").run()
buttons["Check setup"].click().run()
buttons["Start evaluation"].click().run()
assert submitted[0]["name"] == "Grounded support answers — July 18"
assert preflight_payloads[0] == submitted[0]
```

- [ ] **Step 2: Add failing truthfulness tests**

Cover these exact contracts:

```python
assert format_metric_target("lower_is_better", 0.2) == "≤ 0.200"
assert format_metric_target("higher_is_better", 0.8) == "≥ 0.800"
assert overview_cost_value({"known_cost_micro_usd": None, "known_cost_items": 0}) == "—"
assert comparison_signal({"wins": 1, "ties": 4, "losses": 0}) == "No clear difference"
```

- [ ] **Step 3: Add failing analytics tests**

Create one all-unpriced result set and one known zero-cost deterministic result set. Assert the former returns `known_cost_micro_usd is None` with zero coverage and the latter returns `0` with positive coverage. Add a two-candidate shared-case fixture and assert `paired_case_deltas` contains labels, case identity, scores, signed delta, and outcome.

- [ ] **Step 4: Run the focused tests and confirm the expected failures**

Run:

```bash
uv run pytest tests/dashboard/test_app.py tests/dashboard/test_components.py tests/dashboard/test_overview.py tests/dashboard/test_compare.py tests/dashboard/test_run_detail.py tests/integration/test_analytics.py -q
```

Expected: failures for missing named-run UI, missing direction helpers, false zero-spend semantics, and absent paired-case rows.

### Task 2: Make analytics support honest decisions

**Files:**
- Modify: `src/evalforge/analytics.py`
- Test: `tests/integration/test_analytics.py`

- [ ] **Step 1: Add explicit overview coverage counts**

Compute `known_cost_items`, `quality_applicable_results`, and `quality_passed_results`. Return known cost as `None` when no persisted result has recorded pricing, while preserving integer zero when at least one priced result is truly zero-cost.

```python
"known_cost_micro_usd": int(known_cost) if known_cost_items else None,
"known_cost_items": known_cost_items,
"quality_applicable_results": quality_applicable_results,
"quality_passed_results": quality_passed_results,
```

- [ ] **Step 2: Add bounded pair labels and case evidence**

Enrich every aggregate pair with baseline/challenger labels. Add `paired_case_deltas`, bounded by the existing run call limit, containing:

```python
{
    "case_id": case_id,
    "case_name": case_name,
    "baseline_candidate_id": baseline.id,
    "baseline_candidate": baseline.label,
    "challenger_candidate_id": challenger.id,
    "challenger_candidate": challenger.label,
    "baseline_score": baseline_score,
    "challenger_score": challenger_score,
    "delta": round(challenger_score - baseline_score, 4),
    "outcome": "win" | "tie" | "loss",
}
```

- [ ] **Step 3: Run analytics tests**

Run `uv run pytest tests/integration/test_analytics.py -q`.

Expected: all analytics tests pass and existing denominators remain unchanged.

### Task 3: Replace the generic visual system and shell

**Files:**
- Modify: `src/evalforge/dashboard/theme.py`
- Modify: `src/evalforge/dashboard/app.py`
- Modify: `src/evalforge/dashboard/components.py`
- Test: `tests/dashboard/test_components.py`

- [ ] **Step 1: Replace visual tokens**

Use ink/slate neutrals, white surfaces, and one restrained blue accent. Remove radial gradients, gradient buttons, large shadows, excessive radii, and nested metric-card borders. Keep visible focus rings and reduced-motion behavior.

```css
--ef-accent: #2457D6;
--ef-accent-strong: #173FA3;
--ef-canvas: #F5F6F8;
--ef-surface: #FFFFFF;
--ef-text: #181B20;
--ef-muted: #626A76;
--ef-border: #DDE1E7;
--ef-radius: 0.55rem;
```

- [ ] **Step 2: Fix primary-control contrast and metric nesting**

Set primary button text and descendants to white in all normal states. Style the bordered container as the card and leave `stMetric` transparent so screenshots never show a card inside a card.

- [ ] **Step 3: Simplify the shell**

Use `initial_sidebar_state="auto"`. Rename navigation to task language: Home, Runs, Compare, New evaluation, Benchmarks, Settings. Keep a quiet API status in the sidebar and move the raw origin into a collapsed connection-details expander.

- [ ] **Step 4: Add pure metric semantics helpers**

Implement and test:

```python
def humanize_metric_name(name: str) -> str: ...
def metric_direction_label(direction: str) -> str: ...
def format_metric_target(direction: str, threshold: Any) -> str: ...
```

- [ ] **Step 5: Run component and shell AppTests**

Run `uv run pytest tests/dashboard/test_components.py tests/dashboard/test_app.py -q`.

Expected: helpers and the complete app load without exceptions.

### Task 4: Build the guided named-evaluation workflow

**Files:**
- Modify: `src/evalforge/dashboard/pages/run_evaluation.py`
- Modify: `tests/dashboard/test_app.py`

- [ ] **Step 1: Add the run identity field**

Render a required `Run name` text input with a practical placeholder and a 200-character bound. Include the stripped value in both preflight and create payloads, and include it in the preflight signature so any edit invalidates an old check.

- [ ] **Step 2: Reframe setup around three tasks**

Use plain labels: Name and benchmark, Candidates, Check and start. Show case count, exact prompt/model combinations, execution mode, and mark the first candidate as the baseline used on the Compare page.

- [ ] **Step 3: Keep consent and applicability explicit**

Preserve both paid/unknown pricing acknowledgements verbatim. Render `inapplicable_counts` from preflight as coverage notes; never call unavailable metrics failed.

- [ ] **Step 4: Add a results next action**

When polling reaches a terminal state, keep the selected run and show a clear `Review results` action before rerouting or clearing progress.

- [ ] **Step 5: Run the run-builder AppTests**

Run `uv run pytest tests/dashboard/test_app.py -q`.

Expected: demo, priced real-provider, unknown-price, preflight invalidation, and named-run tests all pass.

### Task 5: Rebuild overview, results, and comparison around decisions

**Files:**
- Modify: `src/evalforge/dashboard/pages/overview.py`
- Modify: `src/evalforge/dashboard/pages/run_detail.py`
- Modify: `src/evalforge/dashboard/pages/compare.py`
- Test: `tests/dashboard/test_overview.py`
- Test: `tests/dashboard/test_run_detail.py`
- Test: `tests/dashboard/test_compare.py`

- [ ] **Step 1: Replace unsupported overview scaffolding**

Remove quality trend, global leaderboard, and false failure-success panels. Render totals actually returned by `/overview`, a single `New evaluation` action, recent named evaluations, and evidence-coverage copy for pricing ambiguity/unavailability.

- [ ] **Step 2: Join candidate identity into run evidence**

Build a map from `run["candidates"]`, pass it into result filtering/rendering, and display immutable candidate labels even when prompt variants share a model. Use case identity and result state in expander titles.

- [ ] **Step 3: Replace the misleading radar and raw-color chart**

Render a direction-aware scorecard table with Metric, Mean, Direction, Target, Applicable results, and Interpretation. Raw hallucination risk remains raw and explicitly says lower is better.

- [ ] **Step 4: Reframe comparison honestly**

Show the baseline, pairwise coverage, wins/ties/losses, mean challenger delta, and a neutral signal. Rename aggregate data `Pairwise summary`; place bounded case rows under `Case evidence`, sortable with regressions first. Never show a winner or confidence that the API did not calculate.

- [ ] **Step 5: Run page tests**

Run:

```bash
uv run pytest tests/dashboard/test_overview.py tests/dashboard/test_run_detail.py tests/dashboard/test_compare.py -q
```

Expected: no misleading unavailable values, no fabricated recommendation, and direction/candidate semantics pass.

### Task 6: Validate the complete Phase 2 release

**Files:**
- Modify: `tests/e2e/test_dashboard_smoke.py`
- Modify: relevant docs named in the file map

- [ ] **Step 1: Run focused formatting, lint, and types**

Run:

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src/evalforge
```

Expected: all pass.

- [ ] **Step 2: Run the full deterministic suite**

Run the repository's exact coverage command with live and E2E tests excluded, then run the marked E2E separately against owned local ports.

Expected: 100% test pass rate and branch coverage at or above 80%.

- [ ] **Step 3: Run security and dependency checks**

Run Bandit and pip-audit using the locked environment.

Expected: no unresolved changed-surface finding.

- [ ] **Step 4: Perform Browser proof**

Use the in-app Browser at 1280×720 and 390×844. Exercise:

```text
Home -> New evaluation -> enter name -> check setup -> start -> review results -> compare
```

Verify page identity, nonblank content, no framework overlay, no relevant console warnings/errors, no horizontal overflow, reachable primary actions, exact candidate labels, direction copy, and clean screenshots.

- [ ] **Step 5: Update durable docs and commit**

Create the Phase 2 completion record under `docs/llm-evaluation-dashboard/`, refresh API/operations wording, create the required repo/global `.handoff.mdc`, stage exact files, and make coherent local commits. Do not push because this repository has no remote.
