# Human-label ingestion and offline calibration evidence

## Outcome

EvalForge now has a CLI-first path for turning versioned JSON or CSV human decisions into a
deterministic threshold-calibration report. The workflow is intentionally optional and offline: it
does not add dashboard navigation, read provider settings, contact a model provider, or select a
threshold for the operator.

Keeping calibration in one explicit command preserves the main product loop—choose a test set,
compare candidates, review the result—while still giving advanced adopters a copyable and auditable
workflow.

## Changed surfaces

- `src/evalforge/evaluation/calibration_io.py` defines strict label manifests, canonical hashing,
  deterministic report packaging, and private idempotent output.
- `src/evalforge/cli.py` exposes the offline `evalforge calibrate` command.
- `tests/unit/test_calibration_io.py` and `tests/unit/test_cli_calibration.py` cover validation,
  determinism, output safety, idempotency, and the no-provider boundary.
- `examples/calibration-labels.json` and `examples/calibration-labels.csv` provide equivalent
  five-row fixtures for `examples/customer-support.json`, whose SHA-256 is
  `8d8f0d74572749536603faef69fbc4862e117a4a6350c76e8e76f810e8492c70`.
- `tests/contract/test_public_project_contract.py` requires both examples, ties them to the source
  dataset hash, and verifies equivalent manifests, report boundaries, and safe public fields.
- `README.md`, `docs/getting-started.md`, `docs/evaluation-methodology.md`, and
  `docs/operations.md` add progressive, optional guidance without expanding the core workflow.
- `CHANGELOG.md` records the user-facing addition.

## Local evidence

The local parser, report, adoption, and project-contract slice passed:

- `uv run --all-groups pytest -q tests/unit/test_calibration_io.py tests/contract/test_public_project_contract.py tests/contract/test_project_contract.py` — 31 passed
- `uv run --all-groups ruff check tests/contract/test_public_project_contract.py`
- `uv run --all-groups ruff format --check tests/contract/test_public_project_contract.py`
- `git diff --check`

The copyable labels are fixture-backed local evidence. They are not records of a real human review,
provider execution, hosted deployment, or production behavior.

## Evidence boundary

Every generated report states `evidence_kind = offline_statistical_evidence` and
`production_validated = false`. The workflow makes:

- no provider call;
- no automatic threshold selection;
- no reviewer-agreement claim; and
- no production-validation claim.

Reviewer identifiers are constrained opaque pseudonyms rather than names or email addresses. An
actual calibration decision still requires representative sampling, real human review, reviewer
governance, and a documented release policy. Paid-provider comparison and production acceptance
remain separate, explicitly authorized work.

This record contains local evidence only. Exact pull request, protected-main SHA, and hosted CI
evidence can be appended by the publishing owner after merge.
