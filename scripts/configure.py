#!/usr/bin/env python3
"""Configure the SD card locally; credentials are never command-line arguments."""
import argparse
import errno
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
import tempfile
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request
from urllib.error import HTTPError, URLError

DEFAULT_SERVER = ''
USER_AGENT = 'ShanlingNavidromeSetup/0.1'


def validate_server(value):
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        raise ValueError('Invalid server URL') from None
    if (u.scheme not in ('http', 'https') or not u.hostname or u.username is not None
            or u.password is not None or u.query or u.fragment or any(ord(c) < 33 for c in value)):
        raise ValueError('Use an HTTP(S) server URL without credentials, query or fragment')
    return value.rstrip('/') + '/'


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        a, b = urlsplit(req.full_url), urlsplit(newurl)
        if (a.scheme, a.hostname, a.port) != (b.scheme, b.hostname, b.port):
            raise ValueError('Server redirected authentication to another origin')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify_login(config):
    salt = secrets.token_hex(16)
    token = hashlib.md5((config['password'] + salt).encode('utf-8')).hexdigest()
    query = urlencode({'u': config['username'], 't': token, 's': salt, 'v': '1.16.1',
                       'c': 'ShanlingNavidromeSetup', 'f': 'json'})
    # The setup host has a maintained system trust store; use the shipped bundle too.
    ca = Path(__file__).resolve().parents[1] / 'vendor/ca/cacert.pem'
    ctx = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ctx), SameOriginRedirect())
    try:
        request = Request(config['server_url'] + 'rest/ping.view?' + query,
                          headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
        with opener.open(request, timeout=20) as r:
            raw = r.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Server response too large')
        envelope = json.loads(raw)
        result = envelope.get('subsonic-response') if isinstance(envelope, dict) else None
        if not isinstance(result, dict):
            raise ValueError('Server did not return a Subsonic response')
        if result.get('status') != 'ok':
            detail = result.get('error')
            code = detail.get('code') if isinstance(detail, dict) else None
            # Upstream strings may contain secrets. Only a numeric error code is safe.
            safe_code = str(code) if type(code) is int and 0 <= code <= 9999 else 'unknown'
            raise ValueError('Navidrome rejected login (Subsonic code %s)' % safe_code)
    except HTTPError as e:
        # Never echo exception text, response body or authenticated URL.
        status = e.code
        e.close()
        raise ValueError('Server returned HTTP %d; check API access, authentication and proxy rules' % status) from None
    except (URLError, OSError):
        raise ValueError('Could not verify login; check network, URL, TLS and credentials') from None
    except (KeyError, json.JSONDecodeError):
        raise ValueError('Server did not return a Subsonic response') from None


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        try:
            os.fchmod(fd, mode)
        except OSError as e:
            if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
        with os.fdopen(fd, 'wb') as f:
            f.write(content.encode('utf-8') if isinstance(content, str) else content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def payload_sources():
    project = Path(__file__).resolve().parents[1]
    dist = project / 'dist'
    try:
        report = json.loads((dist / 'build-report.json').read_text())['sd_payload_files']
    except (OSError, KeyError, json.JSONDecodeError):
        raise ValueError('Build report is missing; build this project before preparing the SD card') from None
    sources = []
    expected = ('bin/navidrome-service', 'lib/libnavidrome_ui.so', 'ca-certificates.crt', 'collect-diagnostics.sh')
    for name in expected:
        src = dist / 'sdcard/navidrome' / name
        try:
            content = src.read_bytes()
            expected_hash = report[name]['sha256']
        except (OSError, KeyError):
            raise ValueError('SD payload is incomplete; rebuild the project') from None
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError('SD payload checksum mismatch; rebuild the project')
        sources.append((name, content, 0o755 if name.startswith('bin/') else 0o644))
    return sources

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sd', required=True, type=Path, help='Mounted SD-card root directory')
    ap.add_argument('--server', default=DEFAULT_SERVER)
    ap.add_argument('--allow-http', action='store_true', help='Explicitly allow an unencrypted local server')
    ap.add_argument('--offline', action='store_true', help='Write without checking login')
    ap.add_argument('--stage-only', action='store_true', help='Copy verified payload with feature disabled; no account or firmware changes')
    ap.add_argument('--diagnostics', action='store_true', help='Opt in to a hardware boot snapshot on the SD card')
    ap.add_argument('--diagnostics-only', action='store_true', help='Prepare diagnostics with extension disabled, without requesting credentials')
    ap.add_argument('--disable', action='store_true', help='Remove enable marker; keep configuration')
    args = ap.parse_args()
    if args.stage_only and (args.disable or args.diagnostics or args.diagnostics_only):
        ap.error('--stage-only cannot be combined with disable or diagnostics setup')
    if args.disable and (args.diagnostics or args.diagnostics_only):
        ap.error('--disable cannot be combined with diagnostics setup')
    if not args.sd.is_dir():
        ap.error('--sd must be an existing mounted SD-card directory')
    folder = args.sd / 'navidrome'
    if args.disable:
        (folder / 'enable').unlink(missing_ok=True)
        print('Navidrome disabled. Reboot the player to use its stock application startup.')
        return
    try:
        if args.stage_only:
            sources = payload_sources()
            folder.mkdir(mode=0o700, exist_ok=True)
            (folder / 'enable').unlink(missing_ok=True)
            for name, content, mode in sources:
                dest = folder / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(dest, content, mode)
            print('Verified payload staged. Extension disabled; existing account configuration preserved.')
            print('No password requested, no firmware copied, no device flashed.')
            return
        if args.diagnostics_only:
            sources = payload_sources()
            folder.mkdir(mode=0o700, exist_ok=True)
            (folder / 'enable').unlink(missing_ok=True)
            name, content, mode = next(s for s in sources if s[0] == 'collect-diagnostics.sh')
            atomic_write(folder / name, content, mode)
            atomic_write(folder / 'diagnostics-enable', 'boot-snapshot-v1\n')
            print('Diagnostics prepared; extension disabled; existing account configuration preserved.')
            print('Requires the experimental firmware hook. No firmware was flashed.')
            print('After boot, inspect navidrome/diagnostics/boot.txt. No config, passwords, or app logs are collected.')
            return
        sources = payload_sources()
        server = validate_server(args.server or input('Navidrome server URL (HTTPS): ').strip())
        if server.startswith('http:') and not args.allow_http:
            raise ValueError('HTTP requires --allow-http')
        username = input('Navidrome username: ').strip()
        password = getpass.getpass('Navidrome password (hidden): ')
        if not username or not password:
            raise ValueError('Username and password are required')
        cfg = {'server_url': server, 'username': username, 'password': password,
               'allow_http': args.allow_http}
        if not args.offline:
            verify_login(cfg)
        folder.mkdir(mode=0o700, exist_ok=True)
        # Disable first; an interrupted install must boot the stock application.
        (folder / 'enable').unlink(missing_ok=True)
        for name, content, mode in sources:
            dest = folder / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(dest, content, mode)
        atomic_write(folder / 'config.json', json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
        if args.diagnostics:
            atomic_write(folder / 'diagnostics-enable', 'boot-snapshot-v1\n')
        atomic_write(folder / 'enable', 'native-v1\n')
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as e:
        print(str(e) or 'Setup cancelled', file=sys.stderr)
        raise SystemExit(1)
    print('Verified extension payload copied to the SD card.')
    print('Configuration written to navidrome/config.json; feature enabled for the next boot.')
    print('The SD card contains your password. Keep it private; FAT/exFAT cannot enforce Unix permissions.')
    print('Offline setup: login has not been checked.' if args.offline else 'Navidrome login verified over the configured connection.')


if __name__ == '__main__':
    main()
