#!/usr/bin/env python3
"""Restore the retired Actions writer key only after the cluster writer stops."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
RETIRE_PATH = ROOT / 'scripts/retire-renovate-actions-key.py'
retire = ModuleType('retire_renovate_actions_key')
retire.__file__ = str(RETIRE_PATH)
exec(compile(RETIRE_PATH.read_bytes(), str(RETIRE_PATH), 'exec'), retire.__dict__)

HOMELAB_REPO = 'https://github.com/Stuhlmuller/homelab.git'
SSM_KEY = '/homelab/renovate/github-app/private-key'


def items(kind):
    result = retire.json_command('kubectl', '-n', 'automation', 'get', kind, '-o', 'json')
    entries = result.get('items')
    if result.get('metadata', {}).get('continue') or not isinstance(entries, list) or \
            not all(isinstance(item, dict) for item in entries):
        raise RuntimeError(f'Cannot verify all Renovate {kind}')
    return entries


def is_renovate(resource):
    metadata = resource.get('metadata', {})
    spec = resource.get('spec', {})
    template = spec.get('template', {})
    pod_spec = template.get('spec', {}) if resource.get('kind') == 'Job' else spec
    containers = pod_spec.get('containers', [])
    volumes = pod_spec.get('volumes', [])
    labels = (metadata.get('labels') or {}) | (template.get('metadata', {}).get('labels') or {})
    return (labels.get('app.kubernetes.io/name') == 'renovate' or
            any(ref.get('name') == 'renovate' and ref.get('kind') == 'CronJob'
                for ref in metadata.get('ownerReferences', [])) or
            any(container.get('name') == 'renovate' or
                '/renovatebot/renovate' in container.get('image', '')
                for container in containers) or
            any(volume.get('secret', {}).get('secretName') == 'renovate'
                for volume in volumes))


def cluster_writer_stopped(homelab_sha):
    if not re.fullmatch(r'[0-9a-f]{40}', homelab_sha):
        raise RuntimeError('Expected exact reviewed homelab main SHA')
    server = retire.command('kubectl', 'config', 'view', '--minify', '-o',
                            'jsonpath={.clusters[0].cluster.server}')
    if server != retire.API_SERVER:
        raise RuntimeError('Kubectl context does not target the declared homelab cluster')
    remote = retire.command('git', 'ls-remote', HOMELAB_REPO, 'refs/heads/main').split()
    if remote != [homelab_sha, 'refs/heads/main']:
        raise RuntimeError('Homelab main moved; review the new commit')

    app = retire.json_command('kubectl', '-n', 'argocd', 'get', 'application',
                              'renovate', '-o', 'json')
    sources = app.get('spec', {}).get('sources')
    resources = app.get('status', {}).get('resources', [])
    sync = app.get('status', {}).get('sync', {})
    if app.get('metadata', {}).get('name') != 'renovate' or \
            app.get('metadata', {}).get('namespace') != 'argocd' or \
            sources != [{'repoURL': HOMELAB_REPO, 'targetRevision': 'main',
                         'path': 'clusters/homelab/apps/renovate'}] or \
            app.get('spec', {}).get('ignoreDifferences') or \
            sync.get('status') != 'Synced' or sync.get('revisions') != [homelab_sha] or \
            not any(item.get('kind') == 'CronJob' and item.get('name') == 'renovate' and
                    item.get('namespace') == 'automation' and item.get('status') == 'Synced'
                    for item in resources):
        raise RuntimeError('Renovate Argo Application is not synced to reviewed homelab main')

    cron = retire.json_command('kubectl', '-n', 'automation', 'get', 'cronjob',
                               'renovate', '-o', 'json')
    if cron.get('metadata', {}).get('name') != 'renovate' or \
            cron.get('metadata', {}).get('namespace') != 'automation' or \
            cron.get('spec', {}).get('suspend') is not True:
        raise RuntimeError('Renovate CronJob is not suspended')

    jobs = items('jobs')
    renovate_jobs = [job for job in jobs if is_renovate(job)]
    for job in renovate_jobs:
        conditions = job.get('status', {}).get('conditions') or []
        terminal = any(item.get('type') in ('Complete', 'Failed') and
                       item.get('status') == 'True' for item in conditions)
        if not terminal or job.get('status', {}).get('active', 0):
            raise RuntimeError('Renovate Job is active or pending')

    job_names = {job.get('metadata', {}).get('name') for job in renovate_jobs}
    for pod in items('pods'):
        owners = pod.get('metadata', {}).get('ownerReferences', [])
        belongs = any(ref.get('kind') == 'Job' and ref.get('name') in job_names
                      for ref in owners)
        if (belongs or is_renovate(pod)) and \
                pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed'):
            raise RuntimeError('Renovate Pod is active or pending')


def private_key_version():
    retire.aws_account()
    result = retire.json_command(
        'aws', 'ssm', 'describe-parameters', '--region', 'us-west-2',
        '--parameter-filters', f'Key=Name,Option=Equals,Values={SSM_KEY}',
        '--output', 'json')
    parameters = result.get('Parameters')
    if not isinstance(parameters, list) or len(parameters) != 1:
        raise RuntimeError('Declared SSM private key is missing')
    parameter = parameters[0]
    if parameter.get('Name') != SSM_KEY or parameter.get('Type') != 'SecureString' or \
            parameter.get('KeyId') != 'alias/aws/ssm' or \
            type(parameter.get('Version')) is not int or parameter['Version'] < 2:
        raise RuntimeError('Declared SSM private key has not replaced its placeholder')
    return parameter['Version']


def private_key(version):
    retire.aws_account()
    result = retire.json_command('aws', 'ssm', 'get-parameter', '--region', 'us-west-2',
                                 '--name', SSM_KEY, '--with-decryption', '--output', 'json')
    parameter = result.get('Parameter', {})
    value = parameter.get('Value')
    if parameter.get('Name') != SSM_KEY or parameter.get('Type') != 'SecureString' or \
            parameter.get('Version') != version or not isinstance(value, str):
        raise RuntimeError('Declared SSM private key does not match reviewed metadata')
    marker = re.match(r'\A-----BEGIN (RSA )?PRIVATE KEY-----\r?\n', value)
    if not marker or not value.rstrip().endswith(
            f'-----END {marker.group(1) or ""}PRIVATE KEY-----'):
        raise RuntimeError('Declared SSM private key is not a PEM key')
    return value


def guard(companion_sha, homelab_sha):
    retire.reviewed_main(companion_sha)
    version = private_key_version()
    retire.legacy_runs_drained()
    cluster_writer_stopped(homelab_sha)
    if retire.private_key_sources():
        raise RuntimeError('Repository Actions private key already exists')
    return version


def remove_after_failed_restore():
    try:
        subprocess.run(
            ('gh', 'secret', 'delete', retire.SECRET, '--app', 'actions',
             '--repo', retire.SECRET_REPO),
            text=True, capture_output=True, cwd=ROOT, timeout=90)
    except BaseException:
        pass
    try:
        if not retire.private_key_sources():
            return
    except BaseException:
        pass
    raise RuntimeError(
        'Actions key absence cannot be verified; historical re-runs may now write')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'restore'))
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--homelab-sha', required=True)
    args = parser.parse_args()

    version = guard(args.expected_sha, args.homelab_sha)
    if args.mode == 'restore':
        value = private_key(version)
        if guard(args.expected_sha, args.homelab_sha) != version:
            raise RuntimeError('SSM private key version changed before restore')
        try:
            result = subprocess.run(
                ('gh', 'secret', 'set', retire.SECRET, '--app', 'actions',
                 '--repo', retire.SECRET_REPO),
                input=value, text=True, capture_output=True, cwd=ROOT, timeout=90)
            if result.returncode or not retire.private_key_sources():
                raise RuntimeError('Actions private key restore was not confirmed')
            retire.reviewed_main(args.expected_sha)
            cluster_writer_stopped(args.homelab_sha)
        except BaseException:
            remove_after_failed_restore()
            raise RuntimeError('Actions key restore was not confirmed; repository secret removed') \
                from None
    print(json.dumps({
        'state': 'ready' if args.mode == 'check' else 'restored',
        'companion_main_sha': args.expected_sha,
        'homelab_main_sha': args.homelab_sha,
        'ssm_private_key_version': version,
        'cronjob_suspended': True,
        'renovate_nonterminal_jobs_or_pods': 0,
        'actions_private_key_present': args.mode == 'restore',
    }, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
