# Testing

## Firmware-independent checks

Python 3.13, Go, Clang and libcurl development headers are used by the reference host checks.

```sh
(cd backend && go test -race ./... && go vet ./...)
python -m unittest discover -s tests -p 'test_*.py' -v
python -m unittest discover -s packaging -p 'test_rebuild.py' -v
python tests/native-host/run.py
```

`tests/test_font.py` checks the font merge against a synthetic 256 units/em stand-in font: Vietnamese coverage, preserved CJK/Cyrillic glyphs and metrics, shared-glyph protection, combining-mark placement, dropped tables and reproducible output.

The configuration suite uses a built/audited payload when available; CI uses explicitly non-executable fixture bytes to exercise local HTTP authentication, checksum/copy ordering, failure preservation and marker handling. This does not replace MIPS binary validation. The native harness uses the real extension code with exported toolkit/player mocks and disposable loopback HTTP fixtures.

On Ubuntu, install `clang libcurl4-openssl-dev` for the native harness. Set `ND_SANITIZERS=address,undefined` for ASAN+UBSAN; macOS reference results use UBSAN. Native mock images are generated test layouts, not device screenshots. With `ND_PREVIEW_DIR` set, the harness also exports widget trees; `tests/native-host/preview.py` (needs Pillow) draws them with your extracted stock theme, icons and a chosen font for an approximate local preview. Previews contain proprietary firmware images and must stay local.

## MIPS emulation

After the [MIPS build](build.md), with Docker available:

```sh
docker build -t m1plus-qemu:local -f tests/Dockerfile.mips .
python tests/mips-runtime/run.py
```

The fixture runner uses isolated containers with `--network none`. It tests the actual MIPS backend, verified TLS, browse/search/playlist, bounded/ranged streams and an offline album download to a folder (bytes, cover, manifest, `statfs` and removal); the native probe uses original firmware libraries for RTLD_NOW loading and startup interception. Its custom CRT exits with `_exit`; normal unloading and teardown are not established by that test.

The reference `mips-runtime-probe` image derives from the locally built `m1plus-qemu:local` image. QEMU user-mode does not boot the vendor kernel or emulate XBurst2 peripherals, framebuffer, DAC or device memory pressure.

## Optional real server probe

`tests/mips-runtime/live_server_probe.py` is explicitly for a user-authorized configured server. It expects read-only mounts `/work` and `/sd/navidrome` inside the runtime-probe container, makes authenticated browse/search calls and reads two 4 KiB ranges of one track without saving media. It is never run by CI. Do not publish private configuration or raw metadata; retain only aggregate results.

## Optional local Navidrome integration

`backend/e2e_navidrome.py` also downloads an album and a playlist through the real server, checks the saved FLAC bytes, `.m3u8` and removal, then restarts Navidrome with `EnableDownloads = false` and expects a reported failure. It uses a separately checksum-verified official Navidrome 0.64.2 Darwin arm64 executable (release asset SHA-256 `8b0a7798001453719ad50c12f7025a6044546c4a3568efe51feb296681f29956`), ffmpeg and temporary generated FLAC media/accounts. See `python backend/e2e_navidrome.py --help`. The retained reference report is under `docs/validation/`.

[Validation scope](validation.md) · [Hardware checklist](installation.md#verify-on-the-device)
