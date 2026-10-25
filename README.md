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

## Cluster writer cutover

The legacy `.github/workflows/renovate.yml` uses the repository Actions secret
`RENOVATE_PRIVATE_KEY`. Deleting that workflow from `main` stops new schedule
and manual dispatch events, but GitHub can re-run an older run with its original
commit for 30 days. Repository secrets are read when a run queues, so the old
private-key secret must also be retired before cluster writes start. See
[GitHub's re-run rules](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)
and [secret read timing](https://docs.github.com/en/actions/reference/security/secrets).

After the protected credential transfer succeeds, both declared SSM parameters
have versions above the placeholders, and the `renovate` ExternalSecret refreshes,
merge the reviewed deletion of the entire legacy writer workflow. Wait for its
last scheduled run and every manual run to finish. From the exact reviewed
companion `main` commit, check then retire only the old repository private-key
secret:

```sh
python3 -I scripts/retire-renovate-actions-key.py check --expected-sha <reviewed-main-sha>
python3 -I scripts/retire-renovate-actions-key.py retire --expected-sha <reviewed-main-sha>
```

The fixed-target script checks a clean current `main`, workflow absence, the
declared Kubernetes API server, both SSM SecureString versions, the exact
ExternalSecret mappings and target Secret, a later Ready refresh, every
nonterminal legacy run state, and same-name organization/environment secrets.
It verifies AWS account `716182248480` before SSM metadata reads and pins
GitHub API and Actions secret commands to `github.com`. It deletes only
`RENOVATE_PRIVATE_KEY`, verifies absence, then checks runs again because a
re-run could have queued just before deletion. A successful JSON receipt has
`state: retired`, `actions_private_key_present: false`, and
`legacy_nonterminal_runs: 0`; it contains only public resource names, version
numbers, timestamps, and the reviewed SHA. Do not enable cluster writes until
that post-deletion receipt is recorded. The command is idempotent and never
reads a secret value.

For rollback, suspend the cluster CronJob through reviewed homelab GitOps, wait
for Argo to sync that revision, and verify all Renovate Jobs and Pods are
terminal before restoring the legacy workflow by reviewed PR. Before restoring
its Actions key, require a fresh credential-free GitHub-hosted probe of Harbor
DNS, normal TLS, and registry endpoint reachability. A local or in-cluster
probe does not prove the old runner's route; a 401 proves only reachability.
If the hosted probe is unavailable or fails, keep the key absent and repair
the cluster runner. A reviewed, fixed-source secret reinjection path must
restore the Actions private-key secret before the legacy workflow can write again.
Future credential transfer or rotation likewise needs temporary reinjection of that
secret and the reviewed temporary AWS transfer role; retire both again after
SSM and ExternalSecret verification.
