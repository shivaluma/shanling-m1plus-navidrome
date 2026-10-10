# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An experimental, reverse-engineered Navidrome (Subsonic) client for the **Shanling M1 Plus, firmware V2.1 only** (input SHA-256 `1ccb4667…f925`). It has three runtime pieces: a native C/AWTK UI injected into the stock `demo` executable, a Go loopback proxy, and a guarded init-script launcher. Packaging tooling rebuilds the firmware update container. Proprietary firmware, extracted libraries, toolchains, private config and build outputs live in git-ignored `inputs/`, `.work/`, `tools/`, `dist/` and are never committed.

## Commands

Firmware-independent checks (these are what CI runs, `.github/workflows/ci.yml`; Python 3.13, Go 1.24.5):

```sh
python -m pip install -r requirements-dev.txt        # into .venv
python scripts/check_public_tree.py                  # rejects tracked secrets, personal paths, build artifacts
python scripts/check_docs.py                         # verifies local Markdown links
(cd backend && go test -race ./... && go vet ./...)
python -m unittest discover -s tests -p 'test_*.py' -v
python -m unittest discover -s packaging -p 'test_rebuild.py' -v
ND_SANITIZERS=address,undefined python tests/native-host/run.py   # needs clang + libcurl headers; macOS reference uses UBSAN only
```

Single tests:

```sh
(cd backend && go test -race -run TestName ./...)
python -m unittest tests.test_startup.StartupTest.test_disabled_starts_stock_without_service -v
```

Firmware-dependent build (needs the user's own V2.1 `update.bin`; see `docs/build.md`):

```sh
python scripts/prepare_firmware.py --original /abs/path/update.bin   # -> inputs/, .work/analysis-v2.1/
python scripts/bootstrap_dependencies.py --toolchain                 # CA bundle + GCC MIPS cross-toolchain into tools/
python packaging/bootstrap_tools.py                                  # pinned squashfs-tools
python scripts/build.py                                              # -> dist/sdcard, dist/rootfs (+ merged font), dist/elf-audit.json
                                                                     #    --font-weight regular | --stock-font
python packaging/rebuild.py --overlay dist/rootfs --output dist/firmware-V2.1-ND-devN --version V2.1-ND-devN
```

UI preview (approximate, local only; needs Pillow): `ND_PREVIEW_DIR=/tmp/nd python tests/native-host/run.py && python tests/native-host/preview.py /tmp/nd --font dist/rootfs/release/assets/default/raw/fonts/default.ttf`. Regenerate `backend/nfc_table.go` with `python scripts/gen_nfc_table.py`.

MIPS emulation (Docker): `docker build -t m1plus-qemu:local -f tests/Dockerfile.mips . && python tests/mips-runtime/run.py`.

Never run the build/audit scripts with `python -O` / `PYTHONOPTIMIZE`. `audit_elf.py` and `patch_go_nan2008.py` check `__debug__` and refuse to run under it.

## Architecture

Runtime flow: `etc/init.d/S90play` (overlay) → `scripts/launch-navidrome.sh` (installed as `usr/bin/launch-navidrome`) → checks SD-card enable marker/config/payload → starts the Go proxy and the stock `demo` app with `LD_PRELOAD` of the native plugin. Any missing file, early proxy exit, or nonzero extended-app exit falls back to the plain stock application. The launcher has an `ND_HOST_TEST=1` mode with `ND_TEST_*` path overrides, which `tests/test_startup.py` uses to exercise the real script on the host.

- **`native/startup.c`** interposes `__libc_start_main`, wraps stock `main`, strips `LD_PRELOAD` for children, and posts UI installation via `idle_queue` once the AWTK main loop is up. Only `__libc_start_main` and `nd_ui_start` may be exported.
- **`native/nd_ui.c`** resolves exact V2.1 symbols at runtime with `dlsym` (`LOAD` = required, `OPTIONAL` = degrade gracefully); AWTK objects stay opaque. The UI mimics stock pages by reusing firmware theme styles and images *by name* (`list_item_style`, `navbar_btnreturn_style`, `icon_artist`, …) via `widget_use_style`/`widget_set_prop_str`, creating widgets through `widget_factory_create_widget` (`list_view`, `scroll_view`, `hscroll_label`, `image`, `list_item`, `edit`). Stock geometry/style names come from decoding `.work/analysis-v2.1/.../assets/default/raw/{ui,styles}/*.bin`; see `docs/research/native-integration-evidence.md`. Network calls run on a single serialized worker thread; results are posted back to the GUI thread and discarded by generation checks after navigation/close; row click contexts are tied to `list_serial`, not the network generation. List rows are `button`s, not `list_item`s (the firmware's list_item shows its pressed state only after a 30 ms timer). Taps never wait on `busy`; newer requests supersede older ones. `navigate()` stores the current JSON and scroll offset in `history[depth].cache/yoffset` so `go_back()` is instant. Playback builds the recovered 88-byte O32 `stSongInfo`, calls stock queue functions (deep-copied strings, deterministic negative IDs, ≤128-track chunk), then opens `playing_page` like the stock back-to-player button. JSON responses are capped at 2 MiB. `ND_PAGE` (default 50) is lowered to 4 in the host harness.
- **`native/nd_abi.h`** is the single place for firmware-specific recovered ABI declarations — keep new ABI knowledge there and keep toolkit types opaque.
- **`backend/main.go`** is a single-file, standard-library-only Go service bound to `127.0.0.1:17831`. It translates a small JSON API (`/v1/*`, documented in `backend/README.md`) into Subsonic calls over verified HTTPS, rejects foreign Host/Origin, refuses cross-origin redirects, and never echoes upstream messages or authenticated URLs. `POST /v1/prepare` mints a random 128-bit loopback capability (12h, max 256) that the stock player streams from via `/stream/<cap>` with Range/If-Range passthrough. Built as static `CGO_ENABLED=0 GOMIPS=softfloat`; `scripts/patch_go_nan2008.py` only flips ELF `e_flags` to NaN2008.
- **Host native harness** (`tests/native-host/`) compiles the real `nd_ui.c` with `-DND_HOST_TEST -DND_PAGE=4 -DND_POLL_SECONDS=1` (excludes the libc hook) against a C mock that exports fake toolkit/player symbols (and records string props/styles), plus a Python HTTP fixture with failure modes (delay/malformed/schema/error/empty). Any new firmware symbol the plugin uses must also be exported by `harness.c`.
- **Offline downloads** (`backend/offline.go`): a single worker goroutine downloads album/playlist jobs via Subsonic `download` into `offline_dir` (default `/mnt/mmc/Navidrome`), with `.part` + rename, Range resume, retries, free-space reserve and a manifest (`.navidrome-offline.json`) that defines which files the feature owns — removal must only ever delete manifest-owned files. The native UI polls `/v1/downloads` (`M_POLL` jobs never set `busy`) and never triggers a stock library scan: the stock folder scan drops `songtable` (see research evidence); it only opens `systemset/updatemusic_page` via `navigator_to`.
- **UI font** (`scripts/build_font.py`): the firmware renders its single `default.ttf` (YaHei+Arial, 256 upem) with stb_truetype — no shaping/kerning. The script merges vendored Be Vietnam Pro outlines (pinned SHA-256) for Latin/Vietnamese into the user's stock font, keeps stock metrics and other scripts, offsets combining marks, drops unread tables, and writes it into the rootfs overlay. Output must be reproducible (`recalcTimestamp=False`) and never committed. The backend's `nfc()` (`backend/nfc.go`) composes NFD display strings because the device cannot position combining marks.
- **Packaging** (`packaging/rebuild.py`) reads original SquashFS nodes directly (via `dissect.squashfs`), applies only the overlay, preserves metadata/symlinks/hardlinks, keeps the kernel byte-identical, rejects rootfs growth by default, and requires a `V2.1-ND-*` version distinct from V2.1. The `.work/analysis-v2.1` extraction omits symlinks/special files and must **never** be used as a rootfs source. Output directories must not already exist.
- **`scripts/build.py`** verifies pinned SHA-256s of vendored cJSON, the CA bundle, the firmware input and the stock `demo` binary before building; links the plugin against the *original firmware* glibc 2.28/libcurl 7.73.0 (not the toolchain libc); `scripts/audit_elf.py` checks imports/ABI.
- **`scripts/configure.py`** prepares the SD card (`/mnt/mmc/navidrome/` on device); credentials are prompted interactively, never passed as arguments.
- **`analysis/`** holds static research tools (ELF inventory, capstone disassembly); outputs are ignored.

## Conventions

- Python: 4-space indent, stdlib + pinned `requirements-dev.txt` only. Go: gofmt, standard library only. C: match existing style (C99, `-fvisibility=hidden`).
- Changes to native ownership/cancellation need the sanitizer harness; packaging changes need metadata and negative tests in `packaging/test_rebuild.py`.
- In docs and PRs, keep host, emulation and physical-device evidence clearly separated; passing tests do not establish device compatibility. Validation reports live in `docs/validation/`.
- Never commit or attach `config.json`, credentials, stream capability URLs, personal library metadata, or proprietary firmware bytes; `check_public_tree.py` enforces part of this (including no absolute `/Users/...` or `/home/...` paths in tracked files).
- `README.vi.md` is a Vietnamese mirror of the README; update it when user-facing README content changes.
