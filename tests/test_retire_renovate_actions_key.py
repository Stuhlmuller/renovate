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
                                    '--repo', 'Stuhlmuller/renovate'))
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

        self.assertEqual(order, ['main', 'drain', 'sources', 'delete', 'sources', 'drain'])
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
                {'total_count': 2, 'workflow_runs': [{'id': 1}]}]):
            with self.assertRaisesRegex(RuntimeError, 'pagination was incomplete'):
                retire.pages('unused', 'workflow_runs')

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
        def metadata(version, refresh):
            def json_command(*args):
                if args[0] == 'aws':
                    name = args[args.index('--parameter-filters') + 1].split('Values=')[1]
                    return {'Parameters': [{
                        'Name': name, 'Type': 'SecureString', 'KeyId': 'alias/aws/ssm',
                        'Version': version, 'LastModifiedDate': '2026-10-18T22:00:00Z',
                    }]}
                return {'metadata': {'name': 'renovate'}, 'status': {
                    'conditions': [{'type': 'Ready', 'status': 'True'}],
                    'refreshTime': refresh,
                }}
            return json_command

        with patch.object(retire, 'json_command', side_effect=metadata(1, '2026-10-18T23:00:00Z')):
            with self.assertRaisesRegex(RuntimeError, 'placeholder'):
                retire.transferred_credentials()
        with patch.object(retire, 'json_command', side_effect=metadata(2, '2026-10-18T21:00:00Z')):
            with self.assertRaisesRegex(RuntimeError, 'has not refreshed'):
                retire.transferred_credentials()


if __name__ == '__main__':
    unittest.main()
