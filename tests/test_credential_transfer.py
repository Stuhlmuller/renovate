import importlib.util
import json
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/renovate-credential-transfer.py'
spec = importlib.util.spec_from_file_location('credential_transfer', SCRIPT)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class CredentialTransferTest(unittest.TestCase):
    def test_payload_is_private_and_targets_declared_securestring(self):
        observed = []

        def fake_command(args):
            payload = Path(args[args.index('--cli-input-json') + 1].removeprefix('file://'))
            observed.append((json.loads(payload.read_text()), stat.S_IMODE(payload.stat().st_mode)))
            return '{"Version": 2}'

        with patch.object(transfer, 'command', fake_command):
            version = transfer.store('/homelab/renovate/github-app/issuer-id', 'Iv1.test')

        self.assertEqual(version, 2)
        self.assertEqual(observed, [({
            'Name': '/homelab/renovate/github-app/issuer-id',
            'Type': 'SecureString',
            'KeyId': 'alias/aws/ssm',
            'Value': 'Iv1.test',
            'Overwrite': True,
        }, 0o600)])

    def test_invalid_or_oversize_values_fail_before_write(self):
        for value in ('', 'REPLACE_ME', 'bad\x00value', 'x' * 4097):
            with self.subTest(value_length=len(value)):
                with self.assertRaises(RuntimeError):
                    transfer.validate_value(value)

        with self.assertRaises(RuntimeError):
            transfer.validate_issuer('123456')
        transfer.validate_issuer('Iv1.test')

    def test_invalid_second_placeholder_prevents_both_writes(self):
        for second_metadata in ([], [{'Type': 'String', 'KeyId': None}]):
            with self.subTest(second_metadata=second_metadata):
                calls = []

                def fake_command(args):
                    calls.append(args)
                    if 'describe-parameters' in args:
                        name = args[args.index('--parameter-filters') + 1].split('Values=')[1]
                        if name.endswith('/issuer-id'):
                            return json.dumps([{'Name': name, 'Type': 'SecureString',
                                                'KeyId': 'alias/aws/ssm'}])
                        return json.dumps([{'Name': name, **item} for item in second_metadata])
                    raise AssertionError('No write may start before both placeholders pass')

                with patch.object(transfer, 'command', fake_command), \
                        patch.object(transfer, 'validate_context'), \
                        patch.object(sys, 'argv', [str(SCRIPT)]), \
                        patch.dict('os.environ', {'RENOVATE_APP_ID': 'Iv1.test',
                                                  'RENOVATE_PRIVATE_KEY': 'private-test'}, clear=False):
                    with self.assertRaises(RuntimeError):
                        transfer.transfer()

                self.assertEqual([args[2] for args in calls],
                                 ['describe-parameters', 'describe-parameters'])


if __name__ == '__main__':
    unittest.main()
