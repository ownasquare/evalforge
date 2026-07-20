# Hosted commercialization acceptance

`test_hosted_commercial_pilot.py` is an opt-in live acceptance specification. It does not run in
the default test suite, and a collected or skipped test is not hosted proof.

The suite covers five hosted boundaries:

1. an IdP-only Playwright storage state begins on EvalForge's signed-out welcome page, the unique
   Sign in action completes the live credentialless OIDC callback, and Sign out reaches the
   configured signed-out state;
2. two live OIDC identities can read their own workspace while the primary identity receives HTTP
   403 when it supplies the foreign workspace header;
3. the primary owner starts a disposable hosted trial, completes a seeded deterministic evaluation
   with at least two candidates, prepares an evidence export, and receives a new activation readback;
4. a pending team-workspace request is read back and canceled, then the hosted trial is canceled and
   both entitlement receipts are read back from the server; and
5. the commercial Settings surface fits a 390 by 844 viewport without horizontal document overflow.

The commercial journey is intentionally mutating. Use only a resettable acceptance workspace. It
ends by canceling the pending request and trial, and it makes a best-effort cleanup call if a browser
assertion fails after either mutation.

## Required private inputs

Set all three environment variables explicitly:

```text
EVALFORGE_HOSTED_DASHBOARD_URL=https://dashboard.example.test
EVALFORGE_HOSTED_API_URL=https://api.example.test
EVALFORGE_HOSTED_ACCEPTANCE_FIXTURE=/secure/path/hosted-acceptance.json
```

The fixture file is JSON. Keep it, the Playwright storage state, and token files outside the
repository. Paths in the fixture may be absolute or relative to the fixture file.

```json
{
  "owner_storage_state": "owner-storage-state.json",
  "owner_login_storage_state": "owner-idp-sso-storage-state.json",
  "owner_access_token_file": "owner-access-token.txt",
  "foreign_access_token_file": "foreign-access-token.txt",
  "owner_display_name": "Pilot Owner",
  "primary_workspace_id": "11111111-1111-4111-8111-111111111111",
  "primary_workspace_name": "EvalForge acceptance A",
  "foreign_workspace_id": "22222222-2222-4222-8222-222222222222",
  "foreign_workspace_name": "EvalForge acceptance B",
  "post_logout_visible_text": "Sign in",
  "post_logout_url_pattern": "^https://dashboard[.]example[.]test(?:/.*)?$",
  "minimum_candidate_count": 2,
  "run_timeout_seconds": 240,
  "allow_commercial_mutation": true
}
```

Each token file must contain one current OIDC access token on one line. `owner_storage_state` is an
already-authenticated EvalForge session used by the mutating journey. `owner_login_storage_state`
must contain an already-authorized IdP SSO session but no EvalForge app session: the test requires
the initial EvalForge welcome page and clicks Sign in without typing credentials. Both states and
the owner token must represent the same primary identity. That identity must be an owner or
administrator of the primary workspace and must not be a member of the foreign workspace. The
foreign identity must be an active member of the foreign workspace. The primary workspace must
start with no entitlement or pending team request, and its seeded defaults must produce at least
two successful deterministic candidates. Never commit credentials, token files, or captured
browser state.

The suite reuses caller-provided sessions; it never types, invents, or discovers usernames,
passwords, tokens, browser profiles, or identity-provider settings.

## Run

Install the repository's existing E2E extra and Chromium runtime, then run only the hosted module:

```bash
uv run --all-groups --extra e2e playwright install chromium
uv run --all-groups --extra e2e pytest -m e2e tests/e2e/test_hosted_commercial_pilot.py --browser chromium
```

Without the URLs or fixture, each test skips with the missing input named. A live pass is still only
the recorded hosted environment and fixture at that time; it does not establish production,
live-money, backup/restore, or external-buyer proof.
