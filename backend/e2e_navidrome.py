#!/usr/bin/env python3
"""Exercise the real local Navidrome release with disposable media and credentials.

Requires host Go, ffmpeg, and a separately SHA-256-verified official Navidrome
0.64.2 Darwin arm64 executable. Never contacts the user's server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def request(url, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    hdr = dict(headers or {})
    if data is not None:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        # No URL/token, payload, or upstream message in test failures.
        raise RuntimeError("HTTP failure code " + str(err.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("HTTP connection failed") from None


def wait_ready(url, process, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError("local service exited before readiness")
        try:
            request(url)
            return
        except RuntimeError:
            time.sleep(.25)
    raise RuntimeError("local service readiness timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--navidrome", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    binary = Path(args.navidrome).resolve()
    version = subprocess.check_output([str(binary), "--version"], text=True).strip()
    if "0.64.2" not in version:
        raise RuntimeError("expected pinned Navidrome 0.64.2")
    here = Path(__file__).resolve().parent
    processes = []
    results = {"navidrome_version": version, "checks": []}
    with tempfile.TemporaryDirectory(prefix="m1plus-real-navidrome-") as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        music, data = root / "music", root / "data"
        music.mkdir(); data.mkdir()
        sample = music / "Fixture.flac"
        subprocess.run([
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=2:sample_rate=44100", "-c:a", "flac",
            "-metadata", "artist=宇多田 Test", "-metadata", "album=Requiem café",
            "-metadata", "title=光 & Search", "-metadata", "track=1", "-metadata",
            "disc=2", str(sample)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sample_bytes = sample.read_bytes()
        results["fixture"] = {"format": "FLAC", "bytes": len(sample_bytes),
                              "sha256": hashlib.sha256(sample_bytes).hexdigest()}
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        upstream = "http://127.0.0.1:" + str(port)
        config = root / "navidrome.toml"
        config.write_text("\n".join([
            'Address = "127.0.0.1"', "Port = " + str(port),
            "MusicFolder = " + json.dumps(str(music)), "DataFolder = " + json.dumps(str(data)),
            'LogLevel = "error"', 'ScanSchedule = "0"', 'EnableExternalServices = false',
            'EnableGravatar = false', 'EnableArtworkPrecache = false',
            "FFmpegPath = " + json.dumps(shutil.which("ffmpeg")), ""])
        )
        admin_user, admin_pass = "e2e-local-" + secrets.token_hex(4), secrets.token_hex(24)
        try:
            # Discard server output entirely: no auth bodies/tokens persisted in logs.
            nav = subprocess.Popen([str(binary), "--configfile", str(config), "--nobanner"],
                                   cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(nav)
            wait_ready(upstream + "/app/", nav)
            status, _, _ = request(upstream + "/auth/createAdmin", "POST",
                                   {"username": admin_user, "password": admin_pass})
            assert status == 200, "initial admin creation failed"
            results["checks"].append("official /auth/createAdmin initial local admin: PASS")

            def subsonic(endpoint, params=None):
                salt = secrets.token_hex(16)
                q = dict(params or {})
                q.update(u=admin_user, t=hashlib.md5((admin_pass + salt).encode()).hexdigest(),
                         s=salt, v="1.16.1", c="ShanlingNavidromeE2E", f="json")
                _, _, body = request(upstream + "/rest/" + endpoint + ".view?" + urllib.parse.urlencode(q))
                value = json.loads(body)["subsonic-response"]
                assert value["status"] == "ok", "real server rejected Subsonic request"
                return value

            subsonic("startScan")
            for _ in range(120):
                found = subsonic("search3", {"query": "光", "songCount": 10}).get("searchResult3", {}).get("song", [])
                if found:
                    break
                time.sleep(.25)
            assert found, "fixture was not indexed within 30 seconds"
            results["checks"].append("real media scan and Unicode metadata index: PASS")
            helper = root / "backend-host"
            subprocess.run([shutil.which("go"), "build", "-trimpath", "-o", str(helper), "."],
                           cwd=here, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            backend_config = root / "backend.json"
            backend_config.write_text(json.dumps({"server_url": upstream, "username": admin_user,
                                                 "password": admin_pass, "allow_http": True}))
            os.chmod(backend_config, 0o600)
            # Require the fixed backend port to be free; never reuse an existing service.
            probe = socket.socket()
            try:
                probe.bind(("127.0.0.1",17831))
            finally:
                probe.close()
            backend = subprocess.Popen([str(helper), "-config", str(backend_config), "-ui-dir", ""],
                                       cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(backend)
            base = "http://127.0.0.1:17831"
            wait_ready(base + "/v1/status", backend)

            def api(route):
                status, _, body = request(base + route)
                assert status == 200, "backend API failed"
                return json.loads(body)

            artist = next(a for a in api("/v1/artists")["artists"] if a["name"] == "宇多田 Test")
            detail = api("/v1/artist?id=" + urllib.parse.quote(artist["id"]))
            album = next(a for a in detail["albums"] if a["name"] == "Requiem café")
            assert api("/v1/albums?offset=0&size=1")["albums"][0]["id"] == album["id"]
            assert api("/v1/albums?offset=1&size=1")["albums"] == []
            tracks = api("/v1/album?id=" + urllib.parse.quote(album["id"]))["tracks"]
            song = next(t for t in tracks if t["title"] == "光 & Search")
            assert song["disc"] == 2 and song["track"] == 1
            search = api("/v1/search?q=" + urllib.parse.quote("光") + "&offset=0&size=1")
            assert search["tracks"][0]["id"] == song["id"]
            assert api("/v1/search?q=" + urllib.parse.quote("光") + "&offset=1&size=1")["tracks"] == []
            results["checks"].append("artists → artist albums → paged albums → album tracks: PASS")
            results["checks"].append("search3 Unicode query, track/disc normalization and paging: PASS")
            pl = subsonic("createPlaylist", {"name": "E2E Fixture", "songId": song["id"]})["playlist"]
            assert any(p["id"] == pl["id"] for p in api("/v1/playlists")["playlists"])
            assert api("/v1/playlist?id=" + urllib.parse.quote(pl["id"]))["tracks"][0]["id"] == song["id"]
            results["checks"].append("real playlist listing and playlist tracks: PASS")
            status, _, body = request(base + "/v1/prepare", "POST", {"id": song["id"]})
            stream = json.loads(body)["url"]
            assert status == 200 and urllib.parse.urlsplit(stream).netloc == "127.0.0.1:17831"
            status, headers, audio = request(stream)
            assert status == 200 and audio == sample_bytes, "full stream differs from original FLAC"
            etag = headers.get("ETag") or headers.get("Etag")
            range_headers = {"Range": "bytes=17-100"}
            if etag:
                range_headers["If-Range"] = etag
            status, headers, audio = request(stream, headers=range_headers)
            assert status == 206 and audio == sample_bytes[17:101], "range bytes mismatch"
            assert headers.get("Content-Range") == "bytes 17-100/" + str(len(sample_bytes))
            status, headers, audio = request(stream, "HEAD", headers=range_headers)
            assert status == 206 and audio == b"", "HEAD/range failed"
            assert headers.get("Content-Length") == "84"
            results["checks"].append("capability prepare + complete original FLAC byte equality: PASS")
            results["checks"].append("real GET Range + If-Range and HEAD Range: PASS")
            results["credentials"] = "ephemeral local-only; not retained or logged"
            results["device_execution"] = False
        finally:
            for proc in reversed(processes):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=5)
    Path(args.report).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
