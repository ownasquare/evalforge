# Security policy

## Report a vulnerability privately

For a GitHub-hosted release, open the repository's **Security** tab, choose **Advisories**, and then
**Report a vulnerability**. Do not publish an exploit, secret, provider response, proprietary
benchmark, or other sensitive evidence in a public issue. If the private report button is missing,
wait for the maintainer to publish a private contact instead of disclosing the issue publicly.

Maintainers must enable **Settings → Code security → Private vulnerability reporting** before
announcing a public release.

A useful report includes the affected version or commit, expected impact, a minimal reproduction,
and any mitigation you have already tested. Remove credentials and private model content before
attaching logs or exports.

## Supported versions

| Version | Support status |
| --- | --- |
| `v0.2.0` | Supported public beta |
| `v0.1.x` | Unsupported |
| `main` | Supported development version |

EvalForge does not currently publish a long-term-support release. Security fixes may land on
`main` before a new beta release is cut.

## Deployment boundary

The default demo binds to loopback, uses a local identity, disables real-provider calls, and needs
no secret. EvalForge also includes OIDC workspace roles, PostgreSQL-backed workers, and provider
safeguards for shared environments, but those features do not make an arbitrary deployment secure
by themselves.

Before exposing EvalForge to a network, operators are responsible for TLS, trusted-host settings,
OIDC configuration, tenant provisioning, database security, secret management, backups, monitoring,
and an environment-specific security review. See [the security design](docs/security.md) and
[operations guide](docs/operations.md).

## Provider and evidence safety

- Real calls are opt-in and require server-side allowlists plus explicit transfer and spend
  acknowledgements.
- The dashboard never receives provider keys.
- Exported evidence can contain prompt, context, reference, and model output content. Prefer the
  content-redacted profile unless full evidence is required and approved.
- Never assume provider billing is exactly-once after an interrupted external request.
