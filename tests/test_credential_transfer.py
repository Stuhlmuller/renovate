import importlib.util
import json
from pathlib import Path
import stat
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


if __name__ == '__main__':
    unittest.main()
