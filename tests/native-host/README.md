# Native host integration harness

This compiles the actual `native/nd_ui.c`, `native/startup.c` (`ND_HOST_TEST`), and vendored cJSON into a dynamic library, then loads it with `dlopen` into a harness that exports the recovered toolkit and playback ABI. The firmware executables are not run. A separate HTTP fixture serves normalized backend responses on loopback port 17831. It uses no user server or credentials.

The harness supplies opaque widgets, hierarchy, names, text, enabled/visible state, events, edit factory, slide indicator, timers, and a worker-safe idle queue. Idle callbacks and timers run only while the main GUI thread pumps events. Every toolkit and player operation asserts the GUI thread. Destroyed widgets remain tombstones, so subsequent use fails immediately. Playlist and deque mocks deep-copy strings independently; the test checks metadata after the plugin releases its temporary queue and JSON.

## Reproduce

From the project root, with port 17831 available:

```sh
python3 tests/native-host/run.py
```

The default host sanitizer is UndefinedBehaviorSanitizer. On a working AddressSanitizer host:

```sh
ND_SANITIZERS=address,undefined python3 tests/native-host/run.py
```

The verified Linux arm64 run uses the local `m1plus-qemu:local` Debian bookworm base image and installs host compiler, sanitizer runtime, libcurl headers, and Python. It does not run the MIPS firmware or MIPS plugin:

```sh
docker build -t m1plus-native-asan:local -f tests/native-host/Dockerfile .
docker run --rm -v "$PWD:/work" m1plus-native-asan:local
```

## Observed results (2026-10-10)

Both Darwin arm64 UBSAN and Linux arm64 ASAN+UBSAN passed 13 integration scenarios with 30 worker idle posts. No enabled sanitizer reported an error. Exact output is saved in `results-darwin.txt` and `results-linux.txt`.

- Actual plugin symbol resolution, one home page injection, and slide indicator count updated from two stock slides to three, without duplicate insertion after timer dispatch.
- Artists asynchronously delivered to GUI; local artist Next/Previous paging.
- Artist → album → selected track playback: six queue entries, selected index 1, zero seek, URL capability shape, title/album/artist/track/disc/source kind. All queue IDs are negative, exclude -1, and are distinct within the chunk.
- Unicode search (`宇多田 & café`), encoded request query, remote offset 4, search kind tabs, and Back restoring the album page.
- Playlists → playlist tracks → playback.
- Empty results and disabled paging.
- Malformed JSON syntax and valid JSON with invalid schema both show errors; Retry recovers.
- HTTP 503, Retry, alphabetical album remote paging.
- Host widget geometry SVG capture.
- Close after the fixture confirms an HTTP request is in flight: late result discarded and clean reopen.
- Close after prepare is confirmed in flight: no stale playback starts.
- Foreign credential-bearing stream URL rejected; rows remain usable for successful subsequent prepare.

The invalid URL contains a synthetic fixture value only. The request trace in `requests.json` contains fixture IDs, search text, and pagination, with no user secrets.

AddressSanitizer on the macOS 27.0.1 / Apple clang 17 host hung before `main`; two bounded attempts timed out and the owned processes were stopped. A process sample showed ASAN shadow initialization re-entering allocator initialization through dyld shared-cache enumeration (`AsanInitInternal → InitializeShadowMemory → MemoryRangeIsAvailable → get_dyld_hdr → dyld_shared_cache_iterate_text_swift → _Block_copy → malloc → AsanInitFromRtl → StaticSpinMutex`). Therefore Darwin ASAN is not claimed; Linux ASAN+UBSAN provides the sanitizer evidence. Leak detection is disabled deliberately because fake widgets are retained as tombstones and the production plugin has a process-lifetime worker. Bounds, use-after-free, and undefined-behavior checks remain enabled and fail immediately.

## Limits

`host-mock.svg` records actual widget coordinates and text from the plugin through a simple harness renderer. It is explicitly labeled **HOST MOCK GEOMETRY — NOT DEVICE RENDER**. It does not validate the device font, AWTK styling, touch behavior, slide navigation, keyboard, framebuffer, audio engine, network stack, or firmware boot. ABI recovery and MIPS linkage are assessed separately. This test proves the actual native control flow against the fake toolkit and fixture, not successful hardware integration.
