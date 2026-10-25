"""Fail-closed contract for the optional, credential-free hosted Harbor probe."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/harbor-route-probe.yml'


def shell_step():
    lines = WORKFLOW.read_text().splitlines()
    starts = [index for index, line in enumerate(lines) if line == '        run: |']
    if len(starts) != 1:
        raise AssertionError('Expected one inline probe step')
    body = []
    for line in lines[starts[0] + 1:]:
        if line and not line.startswith('          '):
            break
        body.append(line[10:] if line else '')
    return '\n'.join(body) + '\n'


class HostedRouteProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harbor-route-probe-')
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name)
        getent = self.bin / 'getent'
        getent.write_text('#!/bin/sh\n[ "${MOCK_DNS:-ok}" = ok ]\n')
        getent.chmod(0o755)
        curl = self.bin / 'curl'
        curl.write_text('''#!/bin/sh
case " $* " in
  *'/manifests/'*)
    [ "${MOCK_MANIFEST_FAIL:-no}" = no ] || exit 7
    code="${MOCK_MANIFEST_CODE:-401}"
    printf 'HTTP/2 %s\\r\\n\\r\\n\\nHTTP_CODE:%s' "$code" "$code"
    ;;
  *)
    [ "${MOCK_ROOT_FAIL:-no}" = no ] || exit 60
    code="${MOCK_ROOT_CODE:-401}"
    if [ "${MOCK_REGISTRY_HEADER:-yes}" = yes ]; then
      printf 'HTTP/2 %s\\r\\nDocker-Distribution-Api-Version: registry/2.0\\r\\n\\r\\nHTTP_CODE:%s' "$code" "$code"
    else
      printf 'HTTP/2 %s\\r\\n\\r\\nHTTP_CODE:%s' "$code" "$code"
    fi
    ;;
esac
''')
        curl.chmod(0o755)

    def run_probe(self, **changes):
        env = os.environ.copy()
        env['PATH'] = f'{self.bin}:{env["PATH"]}'
        env.update(changes)
        return subprocess.run(['bash', '-c', shell_step()], cwd=ROOT,
                              env=env, text=True, capture_output=True, check=False)

    def test_manual_zero_permission_runner_has_no_secret_or_writer_step(self):
        source = WORKFLOW.read_text()
        self.assertIn('  workflow_dispatch:', source)
        self.assertIn('permissions: {}', source)
        self.assertIn('runs-on: ubuntu-latest', source)
        self.assertNotIn('schedule:', source)
        self.assertNotIn('secrets.', source)
        self.assertNotIn('actions/checkout', source)
        self.assertNotIn('renovatebot/github-action', source)
        self.assertIn("--noproxy '*'", source)
        self.assertIn("--proto '=https'", source)
        self.assertNotIn('--insecure', source)

    def test_unauthenticated_registry_challenge_is_route_only(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('harbor_dns=ok', result.stdout)
        self.assertIn('registry_http=401', result.stdout)
        self.assertIn('manifest_http=401', result.stdout)
        self.assertIn('private_package_auth=unverified', result.stdout)
        self.assertNotIn('Docker-Distribution-Api-Version', result.stdout)
        self.assertNotIn('100.99.', result.stdout)

    def test_public_manifest_is_reachable(self):
        result = self.run_probe(MOCK_ROOT_CODE='200', MOCK_MANIFEST_CODE='200')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('harbor_route=pass', result.stdout)

    def test_dns_and_https_failure_do_not_pass(self):
        self.assertNotEqual(self.run_probe(MOCK_DNS='fail').returncode, 0)
        result = self.run_probe(MOCK_ROOT_FAIL='yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('registry_https=failed', result.stdout)
        self.assertNotIn('harbor_route=pass', result.stdout)
        result = self.run_probe(MOCK_MANIFEST_FAIL='yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('manifest_https=failed', result.stdout)

    def test_non_registry_and_missing_manifest_fail_closed(self):
        result = self.run_probe(MOCK_REGISTRY_HEADER='no')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('registry_v2=unverified', result.stdout)
        result = self.run_probe(MOCK_MANIFEST_CODE='404')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('manifest_http=unexpected', result.stdout)


if __name__ == '__main__':
    unittest.main()
