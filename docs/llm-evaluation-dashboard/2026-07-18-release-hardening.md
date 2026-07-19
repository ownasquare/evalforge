# EvalForge release hardening completion

Date: 2026-07-18

## Outcome

EvalForge now has a protected contribution path and an immutable `v0.1.0` public-beta release.
The work removes GitHub's Node.js 20 action-runtime warning, pins every third-party action to a
reviewed full commit SHA, adds a repeatable release gate, enables dependency/security intake, and
keeps hosted identity, paid-provider calibration, and production deployment claims explicitly
separate.

## Source and hosted changes

- Release-hardening pull request: [#1](https://github.com/ownasquare/evalforge/pull/1)
- Release commit: `3c770fd8cc88974f54054237cba3cac60a1e7ce9`
- CI action pins:
  - `actions/checkout` `v7.0.0` at `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`
  - `astral-sh/setup-uv` `v8.3.2` at `11f9893b081a58869d3b5fccaea48c9e9e46f990`
  - `docker/setup-buildx-action` `v4.2.0` at `bb05f3f5519dd87d3ba754cc423b652a5edd6d2c`
  - `docker/build-push-action` `v7.3.0` at `53b7df96c91f9c12dcc8a07bcb9ccacbed38856a`
- New release workflow builds wheel and source distribution, installs the wheel on Python 3.11 and
  3.12, installs the source distribution on Python 3.11, generates `SHA256SUMS`, and publishes
  through a draft before release immutability locks the tag and assets.
- Version/changelog contracts keep `pyproject.toml`, `evalforge.__version__`, runtime settings,
  `.env.example`, release notes, action pins, and release behavior aligned.

## Repository governance and security

- Ruleset `19157340` protects `main` from deletion and non-fast-forward updates, requires linear
  history, requires pull requests with resolved conversations, and requires all six stable CI
  contexts. Zero approvals avoids deadlocking a one-maintainer repository; the owner bypass is
  restricted to pull requests.
- Merge commits are disabled; squash and rebase remain available; merged branches are deleted.
- GitHub Actions requires full SHA pins and keeps the default token read-only.
- Dependabot alerts and automatic security fixes are enabled. The dependency graph exposes an SBOM
  with 132 packages, and monthly GitHub Actions update PRs are capped at three.
- Private vulnerability reporting, secret scanning, and push protection remain enabled.
- Repository-level immutable releases are enabled for future releases.

## Release evidence

- Release: [EvalForge v0.1.0](https://github.com/ownasquare/evalforge/releases/tag/v0.1.0)
- Release ID: `356225674`; draft `false`; prerelease `true`; immutable `true`
- Annotated tag object: `6ab635f2d2f0db42276a2f6ea4b88e43c96f1616`
- Tag target: `3c770fd8cc88974f54054237cba3cac60a1e7ce9`
- Release workflow: [run 29667281011](https://github.com/ownasquare/evalforge/actions/runs/29667281011)
- Wheel: `evalforge_dashboard-0.1.0-py3-none-any.whl`, 187,982 bytes,
  SHA-256 `4d2af07785d21ae65e99e6be2c975d5d109e6600a955fac006eaeb5d993267d2`
- Source distribution: `evalforge_dashboard-0.1.0.tar.gz`, 427,702 bytes,
  SHA-256 `e6aba4ff1e7cbccfdc3f31fcd75902e0a34a16de39705bbce442bd0dcd97db96`
- Checksum file: `SHA256SUMS`, 212 bytes,
  SHA-256 `dcc9f0d11cc6aaf7ce454980618e8444c82126638623891e1a488b87798f20c8`

The initial automated release and the immutable republish used byte-identical assets. GitHub's
asset digests, the downloaded checksum file, independent streamed hashes, and the clean local
build all matched.

## Validation evidence

- Local quality gate: Ruff, format, mypy, Bandit, and pip-audit passed.
- Full deterministic suite: 365 passed, 3 PostgreSQL checks skipped without a local test URL,
  14 live/E2E tests deselected by the default suite, and 84.22% branch coverage.
- Identity/provider/operations-focused suite: 71 passed.
- Package proof: clean wheel installs on Python 3.11 and 3.12 and a clean source-distribution
  install on Python 3.11; all core CLI help surfaces passed.
- PR CI: [run 29667094946](https://github.com/ownasquare/evalforge/actions/runs/29667094946),
  all six jobs passed and the Node.js 20 warning was absent.
- Protected-main CI: [run 29667198442](https://github.com/ownasquare/evalforge/actions/runs/29667198442),
  all six jobs passed at the release commit.
- Release workflow: 17 contract tests, artifact builds/installs/checksums, and release creation all
  passed with no annotations.

## Remaining environment-specific lanes

These are tracked publicly without crowding the core dashboard workflow:

- [Hosted OIDC acceptance](https://github.com/ownasquare/evalforge/issues/2)
- [Human-label ingestion and provider calibration](https://github.com/ownasquare/evalforge/issues/3)
- [Production reference deployment and recovery proof](https://github.com/ownasquare/evalforge/issues/4)

No real IdP, paid provider, production deployment, PyPI upload, or container-registry publication
was performed. Those remain separate acceptance boundaries rather than hidden release claims.
