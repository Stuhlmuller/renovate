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

The scheduled and manual `Renovate` triggers share one concurrency group, so
they cannot run as two writers at once. Before creating an App token, the
workflow makes one anonymous, TLS-verified Docker manifest lookup against the
public Harbor mirror Postgres digest used by the homelab restore drill. A fresh
empty Docker config prevents credential reuse. The 45-second probe emits only a
pass/fail status and stops the run if the GitHub-hosted route fails. It checks
reachability of that manifest, not all Renovate Docker lookups; a successful
probe does not replace an error-free nine-repository Renovate run for parity.
Update the probe digest if the homelab restore-drill image changes.
Revert the workflow change through a reviewed PR if the probe itself needs to
be removed.
