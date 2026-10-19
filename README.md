# renovate

Renovate bot configuration repository

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
It deletes
only `RENOVATE_PRIVATE_KEY`, verifies absence, then checks runs again because a
re-run could have queued just before deletion. A successful JSON receipt has
`state: retired`, `actions_private_key_present: false`, and
`legacy_nonterminal_runs: 0`; it contains only public resource names, version
numbers, timestamps, and the reviewed SHA. Do not enable cluster writes until
that post-deletion receipt is recorded. The command is idempotent and never
reads a secret value.

For rollback, suspend or restore full dry-run mode in homelab GitOps and verify
no active or pending cluster write Job before restoring the legacy workflow by
reviewed PR. A reviewed, fixed-source secret reinjection path must restore the
Actions private-key secret before the legacy workflow can write again. Future
credential transfer or rotation likewise needs temporary reinjection of that
secret and the reviewed temporary AWS transfer role; retire both again after
SSM and ExternalSecret verification.
