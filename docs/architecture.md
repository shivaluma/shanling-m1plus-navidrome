# Architecture

## Native interface

`native/startup.c` interposes `__libc_start_main`, wraps the stock main function, removes `LD_PRELOAD` for child processes and waits for the exported AWTK main loop. Installation is posted through `idle_queue`; native widget and player operations run on the GUI thread.

`native/nd_ui.c` resolves the exact V2.1 symbols dynamically. Toolkit objects remain opaque. The extension adds a home page and a native window with artist/album/playlist views, category search, paging, navigation and retry states. Network requests run in a serialized worker. Generation checks discard results after navigation or closure.

Playback uses the recovered 88-byte O32 `stSongInfo` layout and the stock queue functions. Strings are deep copied; deterministic negative IDs avoid local-library IDs. Album/playlist queues are limited to a 128-track chunk containing the selected track; search queues the current result page. This recovered ABI is pinned to one firmware executable and remains subject to hardware testing.

## Loopback proxy

The standard-library Go service binds only `127.0.0.1:17831` and rejects foreign Host/Origin headers. It translates the native JSON API into Subsonic calls over verified HTTPS. The native worker accepts at most 2 MiB of JSON; upstream API responses are capped at 8 MiB. Large artist indexes need further work.

Authentication uses a fresh salt and the Subsonic MD5 token protocol. The password stays in local configuration and process memory. Cross-origin redirects are refused. Errors omit arbitrary upstream messages and authenticated URLs.

Preparing a track creates a random 128-bit loopback bearer capability with a 12-hour lifetime, up to 256 active capabilities. The stock player receives that local URL. The proxy streams original media incrementally, forwarding range semantics; it does not transcode or scrobble. Capabilities are temporary secrets and should not be logged.

See [the backend API](../backend/README.md).

## Startup and storage

The rootfs overlay changes `etc/init.d/S90play` to call `usr/bin/launch-navidrome`. The launcher checks SD enable/config/payload files, passes the CA bundle explicitly and limits the Go runtime with `GOMEMLIMIT=24MiB`, `GOGC=50` and `GOMAXPROCS=2`. These settings do not prove actual device memory requirements.

Missing files or an early service exit start the stock application. A nonzero extended-application exit also restarts the stock application. The launcher does not monitor all later service failures or recover bootloader/kernel failures.

An independent diagnostic marker enables a bounded SD snapshot of kernel, CPU/ISA, memory, MTD partitions, selected mount flags and initial launch state. It does not collect configuration, environment, library metadata or application logs.

## Packaging boundary

The repacker reads the original SquashFS nodes directly. It preserves unchanged content and metadata, symlinks and hardlink equivalence, retains the kernel and rejects rootfs growth by default. The analysis extraction deliberately omits live symlinks and special files and must never be used as a replacement rootfs tree.

[Native ABI evidence](research/native-integration-evidence.md) · [Kernel evidence](research/kernel-abi-and-memory-evidence.md) · [Capacity evidence](research/flash-capacity-evidence.md)
