# Validation status

Reference observations were recorded on **2026-10-10**. The device owner has now reported a successful update, working Navidrome browsing and audible playback on an M1 Plus. This is a first hardware result for an experimental implementation, with the scope recorded in the [owner report](validation/hardware-owner-report.md).

| Requirement | Evidence | What remains unproven |
| --- | --- | --- |
| Subsonic browsing, auth and errors | Go unit/race tests; real local Navidrome FLAC integration | Behavior of other server implementations |
| Native navigation and queue ownership | 22 host scenarios with UBSAN/ASAN; Unicode, cancellation, deep copy, queue, back-to-player, instant Back, superseding taps and download-state checks | Stock-style list rendering, scrolling, keyboard and Now Playing switch on the player |
| Offline downloads | 11 Go tests (resume, retry, cancel, full card, refused downloads, name safety, ownership, restart); real Navidrome download/playlist/removal/`EnableDownloads=false`; MIPS QEMU download; 5 host UI scenarios | Wi-Fi/sleep during long downloads, FAT32 write speed and the stock scan picking up the folder on the player |
| Vietnamese UI font | Merge unit tests; stb_truetype 1.26 host render of every Vietnamese letter; packaged font hash checked after repacking | Rendering and memory use of the replaced font on the player |
| Executable acceptance in emulation | QEMU P5600 MIPS backend and original-library plugin/startup probes | Live kernel/CPU policy and memory use |
| Live server transport | 10 authenticated checks using the actual SD MIPS binary, audio/mp4 HEAD and two byte ranges | Decoding, audible sound, DAC and hardware controls |
| Packaging integrity | Complete inode/content/link audit and unchanged kernel | Updater acceptance, signatures, flash behavior and boot |
| Fallback/diagnostics | Host launcher and diagnostic tests | All hardware failure modes and recovery |
| M1 Plus device | Owner reports successful flash, Navidrome browsing and audible playback | Search/T9, seek, queues, controls, sleep/resume and resource measurements have no individual device results yet |

## Retained reports

- [M1 Plus owner report](validation/hardware-owner-report.md): successful update, browsing and audible playback; self-reported, without device logs or a post-flash image hash.
- [Local Navidrome E2E](validation/local-navidrome-e2e.json): generated valid FLAC and byte equality, paging/search/playlists, range semantics, and offline downloads including a server with downloads disabled.
- [MIPS fixture runtime](validation/mips-runtime.json): 19 backend checks, including batched preparation and an offline download, and original-userland plugin loading.
- [Authenticated live MIPS probe](validation/live-server-mips.json): aggregate evidence only. Playlist detail was not exercised live because the account had no playlists; local fixtures cover it.
- [Reference dev2 packaging](validation/reference-firmware-validation.json): 3,741 paths, 3,016 regular-file hashes, 494 symlinks and 208 hardlink groups.
- [Reference dev3 packaging](validation/reference-firmware-validation-dev3.json): the same full audit with the merged UI font replacing `default.ttf`; rootfs 55,476,224 bytes (stock 55,738,368), package SHA-256 `b2eba661c6f745ec9631887bd50775db6f9f3fec690c5772d0d90fd7ea108fda`.

Personal filesystem paths and server identity were removed from public reports. Firmware payloads, real credentials, song metadata and audio are not included. Reports describe the reference run; contributors should produce their own current results.

The reference rootfs is **55,701,504 bytes**, compared with stock **55,738,368 bytes**. This removes a need for a larger rootfs image; it does not establish live partition capacity or bootability. The reference container hash is retained for traceability, not as a downloadable or hardware-approved release.

## Remaining device validation

The initial on-device browsing and streaming objective has an owner-reported successful result. Broader device support still needs specific checks of search/T9 input, queue transitions, seek, hardware controls, sleep/resume, Wi-Fi reconnection and memory use. A working physical recovery procedure is also not established. Preserve the distinction between owner observations, retained device diagnostics and host/emulation tests.
