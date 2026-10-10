"""Boot snapshots must be useful with SD-only access and exclude credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.card = self.base / 'card'; self.card.mkdir()
        self.proc = self.base / 'proc'; self.proc.mkdir()
        (self.proc / 'sys/kernel').mkdir(parents=True)
        (self.proc / 'sys/kernel/osrelease').write_text('5.10.21+\n')
        (self.proc / 'cpuinfo').write_text('system type\t: Ingenic X2000\ncpu model\t: XBurst2\nisa\t: mips32r2\nSerial\t: private-serial\n')
        (self.proc / 'meminfo').write_text('MemTotal: 131072 kB\nMemAvailable: 65000 kB\nPrivateLabel: private-metadata\n')
        (self.proc / 'mtd').write_text('dev: size erasesize name\nmtd0: 04000000 00020000 "rootfs"\n')
        (self.proc / 'cmdline').write_text('root=/dev/mtdblock0 password=private-password ieee754=2008 token=private-token\n')
        (self.proc / 'mounts').write_text('/dev/mmcblk0p1 /mnt/mmc vfat rw,noexec,username=private-username 0 0\n')
        (self.card / 'config.json').write_text('{"password":"private-password"}')
        (self.card / 'service.log').write_text('private-app-log')
        self.env = dict(os.environ, ND_HOST_TEST='1', ND_TEST_PROC=str(self.proc),
                        SECRET_SHOULD_NOT_BE_READ='private-environment')
        shutil.copyfile(ROOT / 'scripts/collect-diagnostics.sh', self.card / 'collect-diagnostics.sh')
    def run_collector(self):
        return subprocess.run(['/bin/sh', str(ROOT / 'scripts/collect-diagnostics.sh'), str(self.card)],
                              env=self.env, capture_output=True, text=True, timeout=4)
    def test_no_capture_without_opt_in(self):
        self.assertNotEqual(self.run_collector().returncode, 0)
        self.assertFalse((self.card / 'diagnostics').exists())
    def test_opt_in_snapshot_filters_secrets_and_reports_actual_capabilities(self):
        (self.card / 'diagnostics-enable').touch()
        self.assertEqual(self.run_collector().returncode, 0)
        output = (self.card / 'diagnostics/boot.txt').read_text()
        for expected in ('5.10.21+', 'Ingenic X2000', 'mips32r2', '131072 kB', '04000000', 'ieee754=2008', 'filesystem=vfat', 'execution=noexec'):
            self.assertIn(expected, output)
        self.assertNotIn('private-', output)
        self.assertFalse(list((self.card / 'diagnostics').glob('.boot.*')))
        self.assertLess(len(output), 8192)
    def test_snapshot_failure_cannot_prevent_stock_startup(self):
        (self.card / 'diagnostics-enable').touch()
        (self.card / 'diagnostics').symlink_to(self.base / 'outside')
        self.exe('demo', '#!/bin/sh\necho stock-started\n')
        self.env.update(ND_TEST_CARD=str(self.card), ND_TEST_DEMO=str(self.base / 'demo'),
                        ND_TEST_SERVICE=str(self.base / 'unused'), ND_TEST_PLUGIN=str(self.base / 'unused'),
                        ND_TEST_LOG=str(self.base / 'unused'), ND_TEST_CA=str(self.base / 'unused'))
        result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/launch-navidrome.sh')],
                                env=self.env, capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0)
        self.assertIn('stock-started', result.stdout)
    def test_diagnostics_can_run_with_extension_disabled(self):
        (self.card / 'diagnostics-enable').touch()
        self.exe('demo', '#!/bin/sh\nexit 0\n')
        self.env.update(ND_TEST_CARD=str(self.card), ND_TEST_DEMO=str(self.base / 'demo'),
                        ND_TEST_SERVICE=str(self.base / 'unused'), ND_TEST_PLUGIN=str(self.base / 'unused'),
                        ND_TEST_LOG=str(self.base / 'unused'), ND_TEST_CA=str(self.base / 'unused'))
        result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/launch-navidrome.sh')],
                                env=self.env, capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0)
        self.assertIn('extension_state=disabled_or_incomplete', (self.card / 'diagnostics/boot.txt').read_text())
    def exe(self, name, contents):
        path = self.base / name; path.write_text(contents); path.chmod(0o755)

if __name__ == '__main__': unittest.main()
