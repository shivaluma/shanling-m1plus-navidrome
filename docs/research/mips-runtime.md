# Actual MIPS runtime verification

The built MIPS binaries execute under QEMU 7.2.22 with CPU `P5600`. The actual Go backend successfully performs verified HTTPS Subsonic browsing and byte streaming. The actual native plugin loads with the supplied firmware's loader and libraries, fails closed when the expected Shanling GUI API is absent, and intercepts `__libc_start_main` correctly in the minimal MIPS harness. These are emulator/userland results, not proof of M1 Plus hardware behavior.

## Reproduce

The Docker daemon and local `m1plus-qemu:local` image are prerequisites. `tests/Dockerfile.mips` documents the base QEMU image; `tests/mips-runtime/Dockerfile` ensures Python 3 and OpenSSL are available inside the local derived image. No global Python/QEMU installation is performed.

```sh
python3 tests/mips-runtime/run.py
```

The runner builds `m1plus-runtime-probe:local`, executes temporary containers with `--network none` and disabled core dumps, and uses only container loopback networking. It mounts the project for binary input and test evidence output. The disposable upstream HTTPS server runs inside the container. No connection to `music.example.org` or another real server is made. Fixture credentials, private key, CA certificate, configuration and process logs exist only in a temporary container directory and are deleted; authenticated URLs are never logged.

Evidence:

- [results.json](../validation/mips-runtime.json): exact artifact hashes, QEMU version/CPU, checks, protocol counts, and limitations.
- [run.log](../validation/mips-runtime.json): concise test outcomes.
- [native-run.log](../validation/mips-runtime.json): original-userland dynamic load and fail-closed result.
- [native-preload-run.log](../validation/mips-runtime.json): Linux-specific startup-hook result.
- [native-build.json](../validation/mips-runtime.json): custom harness hash and original library restoration counts.

## Backend NaN-personality declaration

The Go build originally emitted a static MIPS32r1 soft-float ELF with flags `0x50001004`. A disposable executable copy reproduces `ELF binary's NaN mode not supported by CPU` on QEMU P5600. The retained original under `dist/unadjusted` has no execute bit because it is a binary backup; the probe copies it into a temporary directory and enables execution there, leaving that artifact unchanged.

`scripts/patch_go_nan2008.py` deliberately changes only ELF `e_flags` bytes 36–39 to `0x50001404`, declaring NaN2008 while retaining the same machine code. It refuses unrelated ELF flags, dynamic/interpreted binaries, non-MIPS/o32 binaries, non-soft-float ABI, or missing `CGO_ENABLED=0` / `GOMIPS=softfloat` build settings. The local Go 1.24.5 runtime source `$GOROOT/src/runtime/softfloat64.go` defines software quiet NaNs by setting the highest mantissa bit (`nan32`/`nan64`), consistent with IEEE2008 encoding. With static CGO disabled there are no C floating-point ABI calls to interlink.

The probe verifies the before/after SHA-256 against `dist/backend-nan2008-adjustment.json` and compares every byte outside the flags field. The adjusted binary's `-h` exits zero, then the same binary runs the complete service test. This is a constrained metadata declaration backed by source/build evidence and runtime tests; it is not a rule that arbitrary MIPS binaries can be fixed by changing flags. It matches the stock application's declaration and the tested P5600 policy. The supplied kernel's actual runtime CPU feature/policy values are still unknown; see [kernel-abi-and-memory-evidence.md](kernel-abi-and-memory-evidence.md).

## Actual service behavior exercised

The fixture generates a one-day RSA certificate with IP SAN `127.0.0.1`. The backend trusts it through `ca_file`, uses TLS at least 1.2, and authenticates using a fresh random salt and correct Subsonic MD5 token for every upstream request. The fixture rejects reused salts, plaintext-password query parameters, incorrect protocol metadata, lost server base paths, or changed Unicode identifiers.

Verified flow:

1. Unicode artist list → selected artist → albums → selected album → normalized tracks.
2. Unicode search across artist/album/song categories with pagination; album pagination and playlist browsing.
3. Track preparation into an opaque loopback stream capability, without a song identifier or credentials in its URL.
4. Full 1,048,580-byte payload SHA/byte equality; Range `206` byte equality with forwarded `If-Range` and `Content-Range`; HEAD with the original content length and no body; `416` with upstream error body suppressed.
5. Invalid local inputs, extra JSON fields/trailing objects, missing/unknown capabilities, unexpected Host and cross-origin requests are rejected.
6. Malformed upstream JSON, numeric Subsonic authentication error, HTTP error and JSON audio-error documents fail closed; upstream messages containing disposable secrets do not appear in local responses.
7. The same self-signed upstream is rejected when `ca_file` is omitted. A hostname mismatch is rejected even with `ca_file` configured. These negative cases demonstrate that the positive TLS case did not disable trust or hostname verification.
8. The service terminates cleanly on SIGTERM without printing fixture credentials.

The transport payload starts with `fLaC` but deliberately contains generated test bytes. It is **not decoder-valid audio**. These checks prove proxy transport and seeking headers, not FLAC decoding, the stock MCL queue ABI, audio output, DAC operation or sample-rate behavior.

## Native dynamic-load and startup-hook scope

`build_native_probe.py` compiles a minimal MIPS o32/FP64/NaN2008 test executable against the original firmware libc/libdl/libpthread. `start.S` independently implements the ELF o32 startup argument contract and calls the original `__libc_start_main`; it avoids newer cross-toolchain CRT requirements. The [glibc 2.28 MIPS startup source](https://github.com/bminor/glibc/blob/glibc-2.28/sysdeps/mips/start.S) documents the register/stack contract used for this harness.

The test sysroot restores 117 original `.so` files, validating every copied SHA-256 against the firmware manifest, and restores 134 original library symlinks. This directory is for runtime probing only and is never a rootfs rebuild source. QEMU uses the original `/lib/ld-linux-mipsn8.so.1` and these libraries through `-L` and guest `LD_LIBRARY_PATH`.

The first probe loads the actual plugin with `dlopen(RTLD_NOW)`, resolves `nd_ui_start`, calls it without any exported Shanling GUI functions, and verifies return `-1` plus the specific missing `main_loop` message. Therefore original dynamic-library symbol/version resolution succeeds and the missing-ABI branch fails closed before creating a worker or touching UI.

The second probe passes guest `LD_PRELOAD` through QEMU `-E`. The plugin's Linux-specific `__libc_start_main` hook runs, reaches the harness's real `main`, and removes `LD_PRELOAD` before that function observes the environment. The same missing-ABI result follows. This exercises behavior excluded by the host-only mock build.

The plugin is designed to remain resident for the stock application lifetime. The final harness flushes output and uses `_exit(0)` after its assertions. A preliminary custom-CRT experiment that called `dlclose` and returned normally produced a bus error during teardown after the positive assertions; that teardown failure was not resolved or attributed to a specific component. Accordingly, neither unload nor normal libc/loader exit is claimed as verified. The resident load/start/fail-closed checks are the verified scope.

The harness does not emulate the GUI, touchscreen, framebuffer, playback queues or audio hardware. Host GUI mocks provide separate behavioral coverage; neither test establishes the native device UI's binary ABI at execution time.

## Remaining hardware requirements

QEMU user-mode translates syscalls to a Linux container kernel. It does not boot the supplied vendor kernel, emulate XBurst2 hardware, determine live kernel NaN policy, verify player Wi-Fi/TLS clock/CA behavior, measure device RAM pressure, or exercise flashing/recovery. Actual Navidrome credentials were not used and authenticated live-server behavior remains unverified. Final firmware acceptance, stock-app startup, the native Navidrome screen and audio playback on the player still need device evidence.
