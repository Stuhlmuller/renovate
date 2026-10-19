#!/usr/bin/env python3
"""Retire the old Actions writer key after the cluster has its SSM copy."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
REPO = 'Stuhlmuller/renovate'
WORKFLOW_ID = 199183772
SECRET = 'RENOVATE_PRIVATE_KEY'
API_SERVER = 'https://10.1.0.199:6443'
SSM_NAMES = (
    '/homelab/renovate/github-app/issuer-id',
    '/homelab/renovate/github-app/private-key',
)
NONTERMINAL = ('requested', 'pending', 'queued', 'waiting', 'in_progress', 'action_required')


def command(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f'{args[0]} check failed; command output withheld')
    return result.stdout.strip()


def json_command(*args):
    return json.loads(command(*args))


def pages(endpoint, field):
    data = json_command('gh', 'api', '--paginate', '--slurp', endpoint)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict) or \
            not isinstance(data[0].get('total_count'), int):
        raise RuntimeError('GitHub returned invalid pagination')
    items = []
    for page in data:
        values = page.get(field)
        if not isinstance(values, list):
            raise RuntimeError('GitHub returned invalid pagination')
        items.extend(values)
    if len(items) != data[0]['total_count']:
        raise RuntimeError('GitHub pagination was incomplete')
    return items


def reviewed_main(expected_sha):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise RuntimeError('Expected exact reviewed main SHA')
    if command('git', 'symbolic-ref', '--short', 'HEAD') != 'main' or \
            command('git', 'rev-parse', 'HEAD') != expected_sha:
        raise RuntimeError('Run only from the reviewed companion main checkout')
    current_main = command('git', 'ls-remote', 'https://github.com/Stuhlmuller/renovate.git',
                           'refs/heads/main').split()
    if current_main != [expected_sha, 'refs/heads/main']:
        raise RuntimeError('Companion main moved; review the new commit')
    if command('git', 'ls-tree', '-r', '--name-only', 'HEAD', '--',
               '.github/workflows/renovate.yml'):
        raise RuntimeError('Legacy writer workflow remains in reviewed main')
    if command('git', 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Local checkout differs from reviewed main')


def timestamp(value):
    if not isinstance(value, str):
        raise RuntimeError('Missing metadata timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise RuntimeError('Metadata timestamp lacks timezone')
    return parsed.astimezone(timezone.utc)


def transferred_credentials():
    server = command('kubectl', 'config', 'view', '--minify', '-o',
                     'jsonpath={.clusters[0].cluster.server}')
    if server != API_SERVER:
        raise RuntimeError('Kubectl context does not target the declared homelab cluster')
    versions = {}
    modified = []
    for name in SSM_NAMES:
        result = json_command('aws', 'ssm', 'describe-parameters', '--region', 'us-west-2',
                              '--parameter-filters', f'Key=Name,Option=Equals,Values={name}',
                              '--output', 'json')
        parameters = result.get('Parameters')
        if not isinstance(parameters, list) or len(parameters) != 1:
            raise RuntimeError('Declared Renovate SSM parameter is missing')
        parameter = parameters[0]
        if parameter.get('Name') != name or parameter.get('Type') != 'SecureString' or \
                parameter.get('KeyId') != 'alias/aws/ssm' or \
                type(parameter.get('Version')) is not int or parameter['Version'] < 2:
            raise RuntimeError('Renovate SSM credential has not replaced its placeholder')
        versions[name] = parameter['Version']
        modified.append(timestamp(parameter.get('LastModifiedDate')))
    resource = json_command('kubectl', '-n', 'automation', 'get', 'externalsecret',
                            'renovate', '-o', 'json')
    spec = resource.get('spec', {})
    mappings = [(item.get('secretKey'), item.get('remoteRef', {}).get('key'))
                for item in spec.get('data', [])]
    expected = list(zip(('issuer-id', 'private-key'), SSM_NAMES))
    if resource.get('metadata', {}).get('namespace') != 'automation' or \
            spec.get('target', {}).get('name') != 'renovate' or \
            spec.get('secretStoreRef') != {'kind': 'ClusterSecretStore', 'name': 'aws-ssm'} or \
            sorted(mappings) != sorted(expected) or spec.get('dataFrom'):
        raise RuntimeError('Renovate ExternalSecret no longer maps the declared SSM paths')
    conditions = resource.get('status', {}).get('conditions', [])
    refresh = timestamp(resource.get('status', {}).get('refreshTime'))
    if resource.get('metadata', {}).get('name') != 'renovate' or \
            not any(item.get('type') == 'Ready' and item.get('status') == 'True'
                    for item in conditions) or refresh <= max(modified):
        raise RuntimeError('Renovate ExternalSecret has not refreshed after transfer')
    if command('kubectl', '-n', 'automation', 'get', 'secret', 'renovate', '-o',
               'jsonpath={.metadata.name}') != 'renovate':
        raise RuntimeError('Renovate target Secret is missing')
    return versions, refresh.isoformat()


def legacy_runs_drained():
    for status in NONTERMINAL:
        endpoint = f'repos/{REPO}/actions/runs?status={status}&per_page=100'
        runs = pages(endpoint, 'workflow_runs')
        if any(run.get('workflow_id') == WORKFLOW_ID and
               run.get('event') in ('schedule', 'workflow_dispatch') for run in runs):
            raise RuntimeError(f'Legacy Renovate {status} run remains')


def private_key_sources():
    endpoint = f'repos/{REPO}'
    repository = pages(f'{endpoint}/actions/secrets?per_page=100', 'secrets')
    organization = pages(f'{endpoint}/actions/organization-secrets?per_page=100', 'secrets')
    environments = pages(f'{endpoint}/environments?per_page=100', 'environments')
    duplicates = [entry for entry in organization if entry.get('name') == SECRET]
    for environment in environments:
        name = environment.get('name')
        if not isinstance(name, str) or not name:
            raise RuntimeError('GitHub returned an invalid environment')
        secrets = pages(f'{endpoint}/environments/{quote(name, safe="")}/secrets?per_page=100',
                        'secrets')
        duplicates.extend(entry for entry in secrets if entry.get('name') == SECRET)
    if duplicates:
        raise RuntimeError('Another Actions scope exposes the legacy private key')
    return any(entry.get('name') == SECRET for entry in repository)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'retire'))
    parser.add_argument('--expected-sha', required=True)
    args = parser.parse_args()
    reviewed_main(args.expected_sha)
    versions, refresh = transferred_credentials()
    legacy_runs_drained()
    present = private_key_sources()
    if args.mode == 'retire':
        reviewed_main(args.expected_sha)
        if present:
            command('gh', 'secret', 'delete', SECRET, '--repo', REPO)
        if private_key_sources():
            raise RuntimeError('Legacy Actions private key still exists')
        # A re-run queued immediately before deletion could retain the old secret.
        legacy_runs_drained()
    receipt = {
        'state': 'ready' if args.mode == 'check' else 'retired',
        'main_sha': args.expected_sha,
        'ssm_versions': versions,
        'externalsecret_refresh': refresh,
        'actions_private_key_present': present if args.mode == 'check' else False,
        'legacy_nonterminal_runs': 0,
    }
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
