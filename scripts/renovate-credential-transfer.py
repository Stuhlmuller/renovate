#!/usr/bin/env python3
"""Move the existing Renovate App secrets to fixed SSM SecureStrings."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REGION = 'us-west-2'
PARAMETERS = (
    ('/homelab/renovate/github-app/issuer-id', 'RENOVATE_APP_ID'),
    ('/homelab/renovate/github-app/private-key', 'RENOVATE_PRIVATE_KEY'),
)


def command(args):
    env = {key: value for key, value in os.environ.items()
           if key not in {'RENOVATE_APP_ID', 'RENOVATE_PRIVATE_KEY'}
           and not key.startswith('AWS_ENDPOINT_URL')}
    env.update(AWS_IGNORE_CONFIGURED_ENDPOINT_URLS='true', AWS_PAGER='')
    result = subprocess.run(args, capture_output=True, text=True, timeout=60,
                            cwd=ROOT, env=env)
    if result.returncode:
        raise RuntimeError('Private command output withheld')
    return result.stdout


def validate_context():
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != 'Stuhlmuller/renovate'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'):
        raise RuntimeError('Reviewed companion workflow required')
    sha = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch('[0-9a-f]{40}', sha) or sha != os.environ.get('EXPECTED_SHA'):
        raise RuntimeError('Invalid dispatch revision')
    if command(['git', 'rev-parse', 'HEAD']).strip() != sha:
        raise RuntimeError('Checkout mismatch')
    if command(['git', 'ls-remote', 'https://github.com/Stuhlmuller/renovate.git',
                'refs/heads/main']).split() != [sha, 'refs/heads/main']:
        raise RuntimeError('Stale dispatch')


def validate_value(value):
    if not value or value == 'REPLACE_ME' or '\x00' in value or len(value.encode()) > 4096:
        raise RuntimeError('Missing or invalid credential')


def store(name, value):
    with tempfile.TemporaryDirectory(prefix='renovate-ssm-') as directory:
        path = Path(directory) / 'parameter.json'
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump({'Name': name, 'Type': 'SecureString', 'KeyId': 'alias/aws/ssm',
                       'Value': value, 'Overwrite': True}, stream)
        response = json.loads(command(['aws', 'ssm', 'put-parameter', '--region', REGION,
                                       '--cli-input-json', f'file://{path}', '--output', 'json']))
    version = response.get('Version')
    if type(version) is not int or version < 2:
        raise RuntimeError('Expected a predeclared SecureString placeholder')
    return version


def transfer():
    if len(sys.argv) != 1:
        raise RuntimeError('No command-line inputs supported')
    values = [(name, os.environ.pop(variable, '')) for name, variable in PARAMETERS]
    for _, value in values:
        validate_value(value)
    validate_context()
    for name, value in values:
        version = store(name, value)
        print(f'{name} updated to SSM version {version}')


if __name__ == '__main__':
    try:
        transfer()
    except Exception:
        print('Renovate credential transfer failed; private details withheld.', file=sys.stderr)
        sys.exit(1)
