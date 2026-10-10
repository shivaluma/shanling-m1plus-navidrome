"""Exercise SD setup with real local Subsonic HTTP, never user credentials."""
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sd_configure', ROOT / 'scripts/configure.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ConfigureIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Network/copy tests run without proprietary firmware in CI.
        # A built payload is used when available; otherwise these are explicitly
        # non-executable unit-test bytes, with the same four-file structure.
        try:
            cls.payload = setup.payload_sources()
            cls.built_payload = True
        except ValueError:
            cls.built_payload = False
            cls.payload = [(name, ('unit-test fixture: ' + name).encode(), mode)
                           for name, mode in [('bin/navidrome-service', 0o755),
                                              ('lib/libnavidrome_ui.so', 0o644),
                                              ('ca-certificates.crt', 0o644),
                                              ('collect-diagnostics.sh', 0o644)]]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nd-configure-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sd = self.base / 'sd'; self.sd.mkdir()
        self.project = self.base / 'project'
        (self.project / 'scripts').mkdir(parents=True)
        report = {}
        for name, content, mode in self.payload:
            dest = self.project / 'dist/sdcard/navidrome' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            report[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}
        (self.project / 'dist/build-report.json').write_text(json.dumps({'sd_payload_files': report}))
        self.addCleanup(patch.stopall)
        patch.object(setup, '__file__', str(self.project / 'scripts/configure.py')).start()
        self.username = 'test-' + secrets.token_hex(8)
        self.password = secrets.token_hex(24)
        self.mode = 'ok'; self.requests = []; self.foreign_hits = 0
        owner = self
        class Foreign(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                owner.foreign_hits += 1
                self.send_response(200); self.end_headers()
        self.foreign = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Foreign)
        class Subsonic(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                u = urlsplit(self.path); q = parse_qs(u.query)
                # Fail safely without exposing credential values in unittest output.
                authenticated = (u.path == '/rest/ping.view' and q.get('u') == [owner.username]
                    and q.get('v') == ['1.16.1'] and q.get('f') == ['json']
                    and q.get('c') == ['ShanlingNavidromeSetup']
                    and self.headers.get('User-Agent') == setup.USER_AGENT
                    and self.headers.get('Accept') == 'application/json'
                    and set(q) == {'u', 't', 's', 'v', 'f', 'c'}
                    and len(q.get('s', [''])[0]) == 32
                    and q.get('t') == [hashlib.md5((owner.password + q.get('s', [''])[0]).encode()).hexdigest()]
                    and owner.password not in self.path)
                owner.requests.append({'authenticated': authenticated, 'salt': q.get('s', [''])[0]})
                if owner.mode == 'http-forbidden':
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(owner.password.encode())
                    return
                if owner.mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://127.0.0.1:%d/foreign?%s' % (owner.foreign.server_port, u.query))
                    self.end_headers(); return
                if owner.mode == 'schema': body = {'subsonic-response': []}
                elif owner.mode == 'error-schema': body = {'subsonic-response': {'status': 'failed', 'error': []}}
                elif owner.mode in ('rejected', 'secret-code') or not authenticated:
                    body = {'subsonic-response': {'status': 'failed', 'error': {
                        'code': owner.password if owner.mode == 'secret-code' else 40,
                        'message': owner.password}}}
                elif owner.mode == 'large': body = None
                else: body = {'subsonic-response': {'status': 'ok', 'version': '1.16.1'}}
                raw = json.dumps(body).encode() if body is not None else b' ' * (1024 * 1024 + 1)
                self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Subsonic)
        for srv in (self.server, self.foreign):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        self.config = {'server_url': 'http://127.0.0.1:%d/' % self.server.server_port,
                       'username': self.username, 'password': self.password, 'allow_http': True}

    def run_setup(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        args = ['configure.py', '--sd', str(self.sd), '--server', self.config['server_url'], '--allow-http', *extra]
        with patch('sys.argv', args), patch('builtins.input', return_value=self.username), \
             patch.object(setup.getpass, 'getpass', return_value=self.password), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try: setup.main(); code = 0
            except SystemExit as e: code = e.code
        self.assertTrue(self.password not in out.getvalue() + err.getvalue(), 'setup exposed transient credential')
        return code, out.getvalue(), err.getvalue()

    def old_install(self):
        folder = self.sd / 'navidrome'; folder.mkdir()
        (folder / 'enable').write_text('old-marker\n')
        (folder / 'config.json').write_bytes(b'{"old":"configuration"}\n')
        return folder

    def test_live_authenticated_ping_fresh_salts_and_no_password_query(self):
        setup.verify_login(self.config); setup.verify_login(self.config)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(r['authenticated'] for r in self.requests))
        self.assertNotEqual(self.requests[0]['salt'], self.requests[1]['salt'])

    def test_foreign_redirect_never_receives_authentication(self):
        self.mode = 'redirect'
        with self.assertRaisesRegex(ValueError, 'another origin'): setup.verify_login(self.config)
        self.assertEqual(self.foreign_hits, 0)

    def test_upstream_messages_and_error_codes_are_redacted(self):
        for mode in ('rejected', 'secret-code'):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(ValueError) as result: setup.verify_login(self.config)
                self.assertTrue(self.password not in str(result.exception), 'upstream response leaked a credential')

    def test_http_error_reports_status_without_echoing_body_or_authenticated_url(self):
        self.mode = 'http-forbidden'
        with self.assertRaisesRegex(ValueError, 'HTTP 403') as result:
            setup.verify_login(self.config)
        message = str(result.exception)
        self.assertNotIn(self.password, message)
        self.assertNotIn(self.username, message)
        self.assertNotIn('rest/ping.view', message)
        self.assertTrue(self.requests[0]['authenticated'])

    def test_invalid_schema_and_oversized_response_fail_safely(self):
        for mode in ('schema', 'error-schema', 'large'):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(ValueError): setup.verify_login(self.config)

    def test_verified_payload_copied_before_enable_marker(self):
        folder = self.old_install(); observed = []
        real_write = setup.atomic_write
        def ordered_write(path, content, mode=0o600):
            path = Path(path)
            self.assertFalse((folder / 'enable').exists(), 'enable marker must be removed before mutation')
            if path.name == 'enable':
                for name, expected, permissions in self.payload:
                    dest = folder / name
                    self.assertEqual(hashlib.sha256(dest.read_bytes()).digest(), hashlib.sha256(expected).digest())
                    self.assertEqual(dest.stat().st_mode & 0o777, permissions)
                cfg = json.loads((folder / 'config.json').read_text())
                self.assertTrue(cfg == self.config, 'written config differs from verified inputs')
                self.assertEqual((folder / 'config.json').stat().st_mode & 0o777, 0o600)
            observed.append(path.relative_to(folder).as_posix())
            real_write(path, content, mode)
        with patch.object(setup, 'atomic_write', side_effect=ordered_write):
            code, out, err = self.run_setup()
        self.assertEqual(code, 0, err)
        self.assertTrue(self.requests and self.requests[0]['authenticated'])
        self.assertEqual(observed[-2:], ['config.json', 'enable'])
        self.assertEqual((folder / 'enable').read_text(), 'native-v1\n')
        self.assertFalse(list(folder.rglob('.*-*')), 'temporary atomic writes were left behind')

    def test_failed_login_preserves_old_configuration_and_enable(self):
        folder = self.old_install(); self.mode = 'rejected'
        code, out, err = self.run_setup()
        self.assertEqual(code, 1)
        self.assertEqual((folder / 'config.json').read_bytes(), b'{"old":"configuration"}\n')
        self.assertEqual((folder / 'enable').read_text(), 'old-marker\n')
        self.assertFalse((folder / 'bin').exists())

    def test_corrupt_payload_aborts_before_prompt_and_sd_mutation(self):
        folder = self.old_install()
        (self.project / 'dist/sdcard/navidrome/lib/libnavidrome_ui.so').write_bytes(b'corrupt')
        with patch('builtins.input', side_effect=AssertionError('credentials requested before payload validation')), \
             patch.object(setup.getpass, 'getpass', side_effect=AssertionError('password requested before validation')):
            # run_setup patches prompts, so invoke main directly here.
            with patch('sys.argv', ['configure.py', '--sd', str(self.sd), '--offline']), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                setup.main()
        self.assertEqual(result.exception.code, 1)
        self.assertEqual((folder / 'config.json').read_bytes(), b'{"old":"configuration"}\n')
        self.assertEqual((folder / 'enable').read_text(), 'old-marker\n')

    def test_interrupted_install_disables_extension_and_preserves_old_config(self):
        folder = self.old_install()
        real_write = setup.atomic_write; writes = []
        def interrupted(path, content, mode=0o600):
            self.assertFalse((folder / 'enable').exists())
            if writes: raise OSError('simulated interrupted copy')
            real_write(path, content, mode); writes.append(path)
        with patch.object(setup, 'atomic_write', side_effect=interrupted):
            code, out, err = self.run_setup('--offline')
        self.assertEqual(code, 1)
        self.assertFalse((folder / 'enable').exists())
        self.assertEqual((folder / 'config.json').read_bytes(), b'{"old":"configuration"}\n')

    def test_disable_requires_no_payload_authentication_or_credentials(self):
        folder = self.old_install()
        with patch.object(setup, 'payload_sources', side_effect=AssertionError('payload read')), \
             patch.object(setup, 'verify_login', side_effect=AssertionError('authentication attempted')), \
             patch('builtins.input', side_effect=AssertionError('username requested')), \
             patch.object(setup.getpass, 'getpass', side_effect=AssertionError('password requested')), \
             patch('sys.argv', ['configure.py', '--sd', str(self.sd), '--disable']), \
             contextlib.redirect_stdout(io.StringIO()):
            setup.main()
        self.assertFalse((folder / 'enable').exists())
        self.assertEqual((folder / 'config.json').read_bytes(), b'{"old":"configuration"}\n')
        self.assertEqual(self.requests, [])

    def test_stage_only_copies_payload_without_credentials_or_config_changes(self):
        folder = self.old_install()
        with patch.object(setup, 'verify_login', side_effect=AssertionError('authentication attempted')), \
             patch('builtins.input', side_effect=AssertionError('username requested')), \
             patch.object(setup.getpass, 'getpass', side_effect=AssertionError('password requested')), \
             patch('sys.argv', ['configure.py', '--sd', str(self.sd), '--stage-only']), \
             contextlib.redirect_stdout(io.StringIO()):
            setup.main()
        self.assertFalse((folder / 'enable').exists())
        self.assertEqual((folder / 'config.json').read_bytes(), b'{"old":"configuration"}\n')
        self.assertFalse((folder / 'diagnostics-enable').exists())
        for name, content, mode in self.payload:
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), hashlib.sha256(content).hexdigest())
        self.assertFalse((self.sd / 'update.bin').exists())
        self.assertEqual(self.requests, [])

    def test_diagnostics_only_preserves_config_without_requesting_credentials(self):
        folder = self.old_install()
        with patch.object(setup, 'verify_login', side_effect=AssertionError('authentication attempted')), \
             patch('builtins.input', side_effect=AssertionError('username requested')), \
             patch.object(setup.getpass, 'getpass', side_effect=AssertionError('password requested')), \
             patch('sys.argv', ['configure.py', '--sd', str(self.sd), '--diagnostics-only']), \
             contextlib.redirect_stdout(io.StringIO()):
            setup.main()
        self.assertFalse((folder / 'enable').exists())
        self.assertTrue((folder / 'diagnostics-enable').is_file())
        self.assertTrue((folder / 'collect-diagnostics.sh').is_file())
        self.assertFalse((folder / 'bin').exists())
        self.assertEqual((folder / 'config.json').read_bytes(), b'{"old":"configuration"}\n')
        self.assertEqual(self.requests, [])

    def test_feature_diagnostics_is_explicit_opt_in(self):
        code, out, err = self.run_setup('--diagnostics')
        self.assertEqual(code, 0, err)
        folder = self.sd / 'navidrome'
        self.assertTrue((folder / 'diagnostics-enable').is_file())
        self.assertTrue((folder / 'enable').is_file())
        self.assertFalse((folder / 'diagnostics').exists(), 'setup must not pretend it collected device evidence')


class BuildGuardsTest(unittest.TestCase):
    def test_optimized_python_cannot_skip_compatibility_assertions(self):
        for name in ('audit_elf.py', 'patch_go_nan2008.py'):
            with self.subTest(script=name):
                result = subprocess.run([sys.executable, '-O', str(ROOT / 'scripts' / name)],
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('requires Python without -O', result.stderr)


if __name__ == '__main__': unittest.main()
