# renovate

Renovate bot configuration repository

`Renovate Credential Transfer` copies the existing GitHub App client ID and
private key from this repository's Actions secrets to two fixed homelab SSM
SecureStrings. Run it only after the homelab SSM placeholders and temporary
operator-owned AWS transfer role are applied. Dispatch from `main` with its
exact current SHA. The workflow validates the App credentials, then writes
through the dedicated role; it never prints secret values. Verify SSM versions
and the homelab ExternalSecret before retiring the transfer role through the
homelab operator plan. The current Renovate runner remains active until the
cluster dry-run parity and separate one-writer cutover are complete.

## Temporary cache canary

`renovate.json` watches `scripts/fixtures/renovate-cache-canary.txt` through the
`github-releases` datasource for this repository. The canary alone does not
auto-merge. It measures retained cache reuse and normal release refresh in the
homelab Renovate dry run.

The manually dispatched `renovate-cache-canary.yml` workflow accepts only the
exact current `main` SHA and release `v0.0.1` or `v0.0.2`. Publish `v0.0.1`
before the first dry run. After two scheduled Pods confirm a cache hit for the
same datasource key, publish `v0.0.2` and wait for normal cache refresh. Do not
publish private cache contents. After the evidence is recorded, remove the
fixture, `renovate.json`, and workflow in a reviewed PR; confirm no canary
update proposal remains active.
