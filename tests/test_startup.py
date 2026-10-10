"""Exercise the actual guarded launcher, without loading the firmware plugin."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class StartupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = self.p = Path(self.temp.name)
        (p / 'card').mkdir()
        self.env = dict(os.environ, ND_HOST_TEST='1', ND_TEST_CARD=str(p / 'card'),
                        ND_TEST_DEMO=str(p / 'demo'), ND_TEST_SERVICE=str(p / 'service'),
                        ND_TEST_PLUGIN=str(p / 'plugin.so'), ND_TEST_LOG=str(p / 'service.log'),
                        ND_TEST_CA=str(p / 'ca.pem'), ND_EVENTS=str(p / 'events'))
        self.exe('demo', '#!/bin/sh\nprintf "demo:%s\\n" "$LD_PRELOAD" >> "$ND_EVENTS"\nsleep 1\n')
        self.exe('service', '#!/bin/sh\nprintf "service:%s:%s:%s\\n" "$*" "$SSL_CERT_FILE" "$GOMEMLIMIT" >> "$ND_EVENTS"\nexec sleep 30\n')
        (p / 'plugin.so').write_text('placeholder')
        (p / 'ca.pem').write_text('placeholder')
        (p / 'card/config.json').write_text('{}')
    def exe(self, name, text):
        f = self.p / name
        f.write_text(text)
        f.chmod(0o755)
    def run_launcher(self):
        result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/launch-navidrome.sh')],
                                env=self.env, capture_output=True, text=True, timeout=6)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.p / 'events').read_text()
    def test_disabled_starts_stock_without_service(self):
        self.assertEqual(self.run_launcher(), 'demo:\n')
    def test_opt_in_starts_service_and_plugin_only_for_demo(self):
        (self.p / 'card/enable').touch()
        events = self.run_launcher()
        self.assertIn('demo:' + self.env['ND_TEST_PLUGIN'], events)
        self.assertIn('service:-config ' + str(self.p / 'card/config.json'), events)
        self.assertIn(':24MiB', events)
    def test_failed_service_falls_back_without_loading_plugin(self):
        (self.p / 'card/enable').touch()
        self.exe('service', '#!/bin/sh\nexit 1\n')
        self.assertEqual(self.run_launcher(), 'demo:\n')
        self.assertIn('using stock application', (self.p / 'service.log').read_text())
    def test_failed_extension_restarts_stock_application(self):
        (self.p / 'card/enable').touch()
        self.exe('demo', '#!/bin/sh\nprintf "demo:%s\\n" "$LD_PRELOAD" >> "$ND_EVENTS"\n[ -n "$LD_PRELOAD" ] && exit 127\nexit 0\n')
        events = self.run_launcher()
        self.assertIn('demo:' + self.env['ND_TEST_PLUGIN'], events)
        self.assertTrue(events.endswith('demo:\n'))
    def test_missing_dependency_falls_back_to_stock(self):
        (self.p / 'card/enable').touch()
        (self.p / 'plugin.so').unlink()
        self.assertEqual(self.run_launcher(), 'demo:\n')
    def test_missing_config_falls_back_to_stock(self):
        (self.p / 'card/enable').touch()
        (self.p / 'card/config.json').unlink()
        self.assertEqual(self.run_launcher(), 'demo:\n')

if __name__ == '__main__':
    unittest.main()
