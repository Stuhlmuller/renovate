import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/retire-renovate-actions-key.py'
spec = importlib.util.spec_from_file_location('retire_actions_key', SCRIPT)
retire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retire)
SHA = 'a' * 40


class RetirementTest(unittest.TestCase):
    def test_retire_checks_live_state_before_and_after_exact_key_deletion(self):
        output = io.StringIO()
        order = []

        def drain():
            order.append('drain')

        def sources():
            order.append('sources')
            return len([step for step in order if step == 'sources']) == 1

        def command(*args):
            self.assertEqual(args, ('gh', 'secret', 'delete', 'RENOVATE_PRIVATE_KEY',
                                    '--app', 'actions', '--repo',
                                    'github.com/Stuhlmuller/renovate'))
            order.append('delete')
            return ''

        with patch.object(sys, 'argv', [str(SCRIPT), 'retire', '--expected-sha', SHA]), \
                patch.object(retire, 'reviewed_main', side_effect=lambda sha: order.append('main')), \
                patch.object(retire, 'transferred_credentials',
                             return_value=({'issuer': 2, 'key': 2}, '2026-10-18T23:00:00+00:00')), \
                patch.object(retire, 'legacy_runs_drained', side_effect=drain), \
                patch.object(retire, 'private_key_sources', side_effect=sources), \
                patch.object(retire, 'command', side_effect=command), \
                contextlib.redirect_stdout(output):
            retire.main()

        self.assertEqual(order, ['main', 'drain', 'sources', 'main', 'delete', 'sources', 'drain'])
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['state'], 'retired')
        self.assertEqual(receipt['actions_private_key_present'], False)
        self.assertEqual(receipt['legacy_nonterminal_runs'], 0)

    def test_nonterminal_run_blocks_deletion_and_all_pages_are_required(self):
        with patch.object(retire, 'pages', side_effect=lambda endpoint, field: [
                {'workflow_id': retire.WORKFLOW_ID, 'event': 'workflow_dispatch'}]
                if 'status=queued' in endpoint else []):
            with self.assertRaisesRegex(RuntimeError, 'queued run remains'):
                retire.legacy_runs_drained()

        with patch.object(retire, 'json_command', return_value=[
                {'total_count': 2, 'workflow_runs': [{'id': 1}]}]) as api:
            with self.assertRaisesRegex(RuntimeError, 'pagination was incomplete'):
                retire.pages('unused', 'workflow_runs')
        api.assert_called_once_with('gh', 'api', '--hostname', 'github.com',
                                    '--paginate', '--slurp', 'unused')

    def test_dirty_checkout_blocks_retirement(self):
        outputs = {
            ('git', 'symbolic-ref', '--short', 'HEAD'): 'main',
            ('git', 'rev-parse', 'HEAD'): SHA,
            ('git', 'ls-remote', 'https://github.com/Stuhlmuller/renovate.git',
             'refs/heads/main'): f'{SHA}\trefs/heads/main',
            ('git', 'ls-tree', '-r', '--name-only', 'HEAD', '--',
             '.github/workflows/renovate.yml'): '',
            ('git', 'status', '--porcelain', '--untracked-files=all'):
            ' M scripts/retire-renovate-actions-key.py',
        }
        with patch.object(retire, 'command', side_effect=lambda *args: outputs[args]):
            with self.assertRaisesRegex(RuntimeError, 'Local checkout differs'):
                retire.reviewed_main(SHA)

    def test_same_name_organization_or_environment_secret_blocks_retirement(self):
        def pages(endpoint, field):
            if '/actions/organization-secrets' in endpoint:
                return [{'name': retire.SECRET}]
            if '/environments?' in endpoint:
                return [{'name': 'protected'}]
            if '/environments/protected/secrets' in endpoint:
                return []
            return [{'name': retire.SECRET}]

        with patch.object(retire, 'pages', side_effect=pages):
            with self.assertRaisesRegex(RuntimeError, 'Another Actions scope'):
                retire.private_key_sources()

        def environment_pages(endpoint, field):
            if '/actions/organization-secrets' in endpoint:
                return []
            if '/environments?' in endpoint:
                return [{'name': 'protected'}]
            if '/environments/protected/secrets' in endpoint:
                return [{'name': retire.SECRET}]
            return [{'name': retire.SECRET}]

        with patch.object(retire, 'pages', side_effect=environment_pages):
            with self.assertRaisesRegex(RuntimeError, 'Another Actions scope'):
                retire.private_key_sources()

    def test_placeholders_or_stale_externalsecret_block_retirement(self):
        def metadata(version, refresh, key_path=retire.SSM_NAMES[1]):
            def json_command(*args):
                if args[:2] == ('aws', 'sts'):
                    return {'Account': retire.AWS_ACCOUNT_ID}
                if args[0] == 'aws':
                    name = args[args.index('--parameter-filters') + 1].split('Values=')[1]
                    return {'Parameters': [{
                        'Name': name, 'Type': 'SecureString', 'KeyId': 'alias/aws/ssm',
                        'Version': version, 'LastModifiedDate': '2026-10-18T22:00:00Z',
                    }]}
                return {'metadata': {'name': 'renovate', 'namespace': 'automation'},
                        'spec': {
                            'target': {'name': 'renovate'},
                            'secretStoreRef': {'kind': 'ClusterSecretStore', 'name': 'aws-ssm'},
                            'data': [
                                {'secretKey': 'issuer-id',
                                 'remoteRef': {'key': retire.SSM_NAMES[0]}},
                                {'secretKey': 'private-key',
                                 'remoteRef': {'key': key_path}},
                            ],
                        }, 'status': {
                    'conditions': [{'type': 'Ready', 'status': 'True'}],
                    'refreshTime': refresh,
                }}
            return json_command

        def command(*args):
            return retire.API_SERVER if args[1] == 'config' else 'renovate'

        with patch.object(retire, 'command', side_effect=command), \
                patch.object(retire, 'json_command',
                             side_effect=metadata(1, '2026-10-18T23:00:00Z')):
            with self.assertRaisesRegex(RuntimeError, 'placeholder'):
                retire.transferred_credentials()
        with patch.object(retire, 'command', side_effect=command), \
                patch.object(retire, 'json_command',
                             side_effect=metadata(2, '2026-10-18T21:00:00Z')):
            with self.assertRaisesRegex(RuntimeError, 'has not refreshed'):
                retire.transferred_credentials()

        with patch.object(retire, 'command', return_value='https://wrong.example:6443'):
            with self.assertRaisesRegex(RuntimeError, 'does not target'):
                retire.transferred_credentials()
        with patch.object(retire, 'command', side_effect=command), \
                patch.object(retire, 'json_command',
                             side_effect=metadata(2, '2026-10-18T23:00:00Z',
                                                  '/wrong/private-key')):
            with self.assertRaisesRegex(RuntimeError, 'no longer maps'):
                retire.transferred_credentials()

        with patch.object(retire, 'command', side_effect=lambda *args:
                          retire.API_SERVER if args[1] == 'config' else ''), \
                patch.object(retire, 'json_command',
                             side_effect=metadata(2, '2026-10-18T23:00:00Z')):
            with self.assertRaisesRegex(RuntimeError, 'target Secret is missing'):
                retire.transferred_credentials()

        with patch.object(retire, 'command', side_effect=command), \
                patch.object(retire, 'json_command',
                             side_effect=metadata(2, '2026-10-18T23:00:00Z')):
            versions, refresh = retire.transferred_credentials()
        self.assertEqual(versions[retire.SSM_NAMES[1]], 2)
        self.assertEqual(refresh, '2026-10-18T23:00:00+00:00')

    def test_wrong_aws_account_blocks_ssm_metadata_read(self):
        with patch.object(retire, 'command', return_value=retire.API_SERVER), \
                patch.object(retire, 'json_command',
                             return_value={'Account': '000000000000'}) as aws:
            with self.assertRaisesRegex(RuntimeError, 'declared homelab account'):
                retire.transferred_credentials()
        aws.assert_called_once_with('aws', 'sts', 'get-caller-identity',
                                    '--region', 'us-west-2', '--output', 'json')


if __name__ == '__main__':
    unittest.main()
