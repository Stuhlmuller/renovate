# renovate

Renovate bot configuration repository

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
