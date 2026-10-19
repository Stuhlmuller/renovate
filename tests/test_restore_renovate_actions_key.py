import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/restore-renovate-actions-key.py'
spec = importlib.util.spec_from_file_location('restore_actions_key', SCRIPT)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)
SHA = 'a' * 40
HOMELAB_SHA = 'b' * 40
KEY = '-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----\n'


def cluster_json(*args):
    if 'application' in args:
        return {'metadata': {'name': 'renovate', 'namespace': 'argocd'},
                'spec': {'sources': [{'repoURL': restore.HOMELAB_REPO,
                                      'targetRevision': 'main',
                                      'path': 'clusters/homelab/apps/renovate'}]},
                'status': {'sync': {'status': 'Synced', 'revisions': [HOMELAB_SHA]}}}
    if 'cronjob' in args:
        return {'metadata': {'name': 'renovate', 'namespace': 'automation'},
                'spec': {'suspend': True}}
    if 'jobs' in args or 'pods' in args:
        return {'items': []}
    raise AssertionError(args)


class RestoreTest(unittest.TestCase):
    def test_cluster_requires_synced_main_suspension_and_drained_jobs_and_pods(self):
        remote = f'{HOMELAB_SHA}\trefs/heads/main'
        with patch.object(restore.retire, 'command', return_value=remote), \
                patch.object(restore.retire, 'json_command', side_effect=cluster_json):
            restore.cluster_writer_stopped(HOMELAB_SHA)

        def stale_app(*args):
            result = cluster_json(*args)
            if 'application' in args:
                result['status']['sync']['revisions'] = [SHA]
            return result

        with patch.object(restore.retire, 'command', return_value=remote), \
                patch.object(restore.retire, 'json_command', side_effect=stale_app):
            with self.assertRaisesRegex(RuntimeError, 'not synced'):
                restore.cluster_writer_stopped(HOMELAB_SHA)

        def unsafe(*args):
            result = cluster_json(*args)
            if 'cronjob' in args:
                result['spec']['suspend'] = False
            return result

        with patch.object(restore.retire, 'command', return_value=remote), \
                patch.object(restore.retire, 'json_command', side_effect=unsafe):
            with self.assertRaisesRegex(RuntimeError, 'not suspended'):
                restore.cluster_writer_stopped(HOMELAB_SHA)

        def pending_job(*args):
            if 'jobs' in args:
                return {'items': [{'kind': 'Job', 'metadata': {'name': 'manual-renovate'},
                                   'spec': {'template': {'spec': {'containers': [
                                       {'name': 'renovate'}]}}}, 'status': {}}]}
            return cluster_json(*args)

        with patch.object(restore.retire, 'command', return_value=remote), \
                patch.object(restore.retire, 'json_command', side_effect=pending_job):
            with self.assertRaisesRegex(RuntimeError, 'Job is active or pending'):
                restore.cluster_writer_stopped(HOMELAB_SHA)

        def pending_pod(*args):
            if 'pods' in args:
                return {'items': [{'kind': 'Pod', 'metadata': {'name': 'renovate-orphan',
                                   'labels': {'app.kubernetes.io/name': 'renovate'}},
                                   'spec': {'containers': [{'name': 'renovate'}]},
                                   'status': {'phase': 'Pending'}}]}
            return cluster_json(*args)

        with patch.object(restore.retire, 'command', return_value=remote), \
                patch.object(restore.retire, 'json_command', side_effect=pending_pod):
            with self.assertRaisesRegex(RuntimeError, 'Pod is active or pending'):
                restore.cluster_writer_stopped(HOMELAB_SHA)

    def test_remote_revision_and_incomplete_inventory_fail_closed(self):
        with patch.object(restore.retire, 'command', return_value=f'{SHA}\trefs/heads/main'):
            with self.assertRaisesRegex(RuntimeError, 'Homelab main moved'):
                restore.cluster_writer_stopped(HOMELAB_SHA)
        with patch.object(restore.retire, 'json_command', return_value={'items': 'partial'}):
            with self.assertRaisesRegex(RuntimeError, 'Cannot verify all'):
                restore.items('jobs')

    def test_private_key_is_fixed_ssm_source_and_version(self):
        response = {'Parameter': {'Name': restore.SSM_KEY, 'Type': 'SecureString',
                                  'Version': 2, 'Value': KEY}}
        with patch.object(restore.retire, 'json_command', return_value=response) as command:
            self.assertEqual(restore.private_key(2), KEY)
        self.assertEqual(command.call_args.args,
                         ('aws', 'ssm', 'get-parameter', '--region', 'us-west-2',
                          '--name', restore.SSM_KEY, '--with-decryption', '--output', 'json'))
        with patch.object(restore.retire, 'json_command', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                restore.private_key(3)

    def test_restore_streams_key_to_only_actions_secret_and_receipt_omits_it(self):
        output = io.StringIO()
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, '', '')

        with patch.object(sys, 'argv', [str(SCRIPT), 'restore', '--expected-sha', SHA,
                                        '--homelab-sha', HOMELAB_SHA]), \
                patch.object(restore, 'guard', side_effect=[2, 2]), \
                patch.object(restore, 'private_key', return_value=KEY), \
                patch.object(restore.retire, 'private_key_sources', return_value=True), \
                patch.object(restore, 'cluster_writer_stopped'), \
                patch.object(restore.subprocess, 'run', side_effect=run), \
                contextlib.redirect_stdout(output):
            restore.main()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ('gh', 'secret', 'set', 'RENOVATE_PRIVATE_KEY',
                                       '--app', 'actions', '--repo', 'Stuhlmuller/renovate'))
        self.assertEqual(calls[0][1]['input'], KEY)
        self.assertTrue(calls[0][1]['capture_output'])
        self.assertNotIn(KEY, output.getvalue())
        receipt = json.loads(output.getvalue())
        self.assertEqual((receipt['state'], receipt['actions_private_key_present']),
                         ('restored', True))


if __name__ == '__main__':
    unittest.main()
