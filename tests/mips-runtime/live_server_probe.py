#!/usr/bin/env python3
"""Explicit live-server probe. Uses mounted SD config; emits no library metadata.

Run only with user authorization for their configured Navidrome server.
Unlike probe.py, this makes authenticated external requests and reads up to
8 KiB of one real track without saving media or modifying the library.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:17831'
CARD = Path('/sd/navidrome')
checks = []
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def require(condition, label):
    if not condition:
        raise RuntimeError(label)


def passed(label):
    checks.append(label)
    print('PASS ' + label, file=sys.stderr, flush=True)


def api(path, params=None, body=None):
    if params:
        path += '?' + urllib.parse.urlencode(params)
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=payload,
                                 headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=30) as resp:
        require(resp.status == 200, 'local API status')
        raw = resp.read(2 * 1024 * 1024 + 1)
    require(len(raw) <= 2 * 1024 * 1024, 'native UI response size bound')
    return json.loads(raw)


def main():
    cfg = CARD / 'config.json'
    require(cfg.is_file() and (CARD / 'enable').is_file(), 'SD configured and enabled')
    binary = CARD / 'bin/navidrome-service'
    env = dict(os.environ, SSL_CERT_FILE=str(CARD / 'ca-certificates.crt'),
               GOMEMLIMIT='24MiB', GOGC='50', GOMAXPROCS='2')
    process = subprocess.Popen(['qemu-mipsel', '-cpu', 'P5600', str(binary),
                                '-config', str(cfg), '-ui-dir', ''], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            require(process.poll() is None, 'MIPS backend stays running')
            try:
                status = api('/v1/status')
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        else:
            raise RuntimeError('MIPS backend startup timed out')
        require(status.get('configured') and not status.get('allow_http'), 'HTTPS configured')
        passed('actual SD MIPS backend starts with TLS CA and runtime limits')
        artists = api('/v1/artists')['artists']
        require(artists, 'server library has artists')
        passed('authenticated artist list')
        artist = artists[0]
        artist_view = api('/v1/artist', {'id': artist['id']})
        require(artist_view['artist']['id'] == artist['id'], 'artist detail identity')
        passed('artist to album browsing')
        albums = api('/v1/albums', {'offset': 0, 'size': 4})['albums']
        require(albums, 'server library has albums')
        passed('album list pagination')
        album = api('/v1/album', {'id': albums[0]['id']})
        require(album['tracks'], 'selected album has tracks')
        passed('album to track browsing')
        search = api('/v1/search', {'q': artist['name'], 'offset': 0, 'size': 4})
        require(any(search.get(k) for k in ('artists', 'albums', 'tracks')), 'search returns matches')
        passed('search using real library text')
        playlists = api('/v1/playlists')['playlists']
        passed('playlist list')
        if playlists:
            playlist = api('/v1/playlist', {'id': playlists[0]['id']})
            require(playlist['playlist']['id'] == playlists[0]['id'], 'playlist detail identity')
            passed('playlist detail')
        track = album['tracks'][0]
        capability = api('/v1/prepare', body={'id': track['id']})['url']
        parsed = urllib.parse.urlsplit(capability)
        require(parsed.scheme == 'http' and parsed.netloc == '127.0.0.1:17831'
                and not parsed.query and parsed.path.startswith('/stream/'), 'local capability only')
        passed('playback capability without upstream credentials')
        head = urllib.request.Request(capability, method='HEAD')
        with opener.open(head, timeout=30) as resp:
            require(resp.status == 200, 'stream HEAD status')
            content_type = resp.headers.get('Content-Type', '').split(';')[0]
            require(content_type.startswith('audio/') or content_type == 'application/octet-stream',
                    'stream is audio')
        passed('real audio HEAD through MIPS proxy')
        lengths = []
        for first, last in ((0, 4095), (4096, 8191)):
            request = urllib.request.Request(capability, headers={'Range': f'bytes={first}-{last}'})
            with opener.open(request, timeout=30) as resp:
                require(resp.status == 206, 'stream supports byte seek')
                require(resp.headers.get('Content-Range', '').startswith(f'bytes {first}-{last}/'),
                        'stream Content-Range matches requested bytes')
                raw = resp.read(4097)
                require(len(raw) == 4096, 'bounded real audio range length')
            lengths.append(len(raw))
        passed('two real audio byte ranges for playback and seek')
        return {'recorded_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'passed': True, 'checks': checks,
                'backend_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'library_counts': {'artists': len(artists), 'albums_in_page': len(albums),
                                   'tracks_in_selected_album': len(album['tracks']),
                                   'playlists': len(playlists)},
                'audio_content_type': content_type, 'stream_bytes_read': sum(lengths),
                'scope': 'Actual SD MIPS backend under QEMU P5600 using authenticated live server. '
                         'No hardware boot, native UI rendering, audio decoding or DAC verification.',
                'sensitive_data_retained': False}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


try:
    result = main()
except Exception as exc:
    # HTTPError text contains a capability URL; never print it or response bodies.
    result = {'passed': False, 'checks': checks, 'failure_class': type(exc).__name__}
    if isinstance(exc, urllib.error.HTTPError):
        result['http_status'] = exc.code
        exc.close()
    elif type(exc) is RuntimeError:
        result['failure'] = str(exc)  # Only fixed labels generated above.
print(json.dumps(result, indent=2))
sys.exit(0 if result['passed'] else 1)
