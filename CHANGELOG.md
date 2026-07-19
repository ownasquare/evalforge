# Changelog

All notable user-facing changes to EvalForge are recorded here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Offline JSON/CSV human-label ingestion and deterministic, idempotent calibration reports through
  `evalforge calibrate`.

### Changed

- No changes yet.

## [0.1.0] - 2026-07-18

### Added

- FastAPI service and Streamlit dashboard for benchmark, prompt, model, run, result, and comparison
  workflows.
- Deterministic offline model profiles and explainable built-in metrics.
- SQLite local mode and PostgreSQL-backed shared-worker support.
- Local identity and OIDC workspace roles.
- Content-redacted and full-evidence export packages with integrity hashes.
- Explicit real-provider transfer, allowlist, and spend safeguards.
- Automated unit, contract, integration, AppTest, PostgreSQL, and Playwright coverage.

### Changed

- Simplified first-run guidance and public project documentation.
- Added tested source-extension examples and community templates.

[Unreleased]: https://github.com/ownasquare/evalforge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ownasquare/evalforge/releases/tag/v0.1.0
