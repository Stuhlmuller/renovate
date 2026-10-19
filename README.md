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

## Restore the Actions key for rollback

First merge a reviewed homelab GitOps change that suspends the `automation/renovate`
CronJob. Wait for the Renovate Argo Application to sync to that exact homelab
`main` revision and for all Renovate Jobs and Pods to finish. Keep the legacy
Actions workflow absent from companion `main`. From a clean checkout of its
exact reviewed `main` commit, use an operator identity with read access to the
declared SSM SecureString and write access to this repository's Actions secrets:

```sh
python3 -I scripts/restore-renovate-actions-key.py check \
  --expected-sha <reviewed-companion-main-sha> \
  --homelab-sha <reviewed-homelab-main-sha>
python3 -I scripts/restore-renovate-actions-key.py restore \
  --expected-sha <reviewed-companion-main-sha> \
  --homelab-sha <reviewed-homelab-main-sha>
```

The script requires both exact current `main` revisions, the synced Argo source,
a suspended live CronJob, no pending or active Renovate Job or Pod, the fixed
SSM private-key SecureString at a version above its placeholder, and no
existing same-name Actions secret. It works during an ExternalSecret outage.
It verifies AWS account `716182248480` before SSM reads and pins the Actions
secret target to `github.com`. It retrieves only
`/homelab/renovate/github-app/private-key` with decryption and streams the value
to `gh secret set` on stdin. It prints no key or command output. A successful
JSON receipt has `state: restored`, `actions_private_key_present: true`,
`cronjob_suspended: true`, and `renovate_nonterminal_jobs_or_pods: 0`.
If the upload or its post-check fails, it deletes the exact repository secret
and verifies absence. An unverified cleanup reports that historical re-runs
may now write and requires immediate operator attention.

Restore the legacy workflow only through a reviewed PR while the cluster CronJob
stays suspended. Retire the Actions key again before cluster writes resume.
Future credential transfer or rotation also needs the reviewed temporary AWS
transfer role and must repeat the retirement checks after SSM and ExternalSecret
verification.
