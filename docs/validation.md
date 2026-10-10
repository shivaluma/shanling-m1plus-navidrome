# Validation status

Reference observations were recorded on **2026-10-10**. The source is experimental; no M1 Plus hardware result has been supplied.

| Requirement | Evidence | What remains unproven |
| --- | --- | --- |
| Subsonic browsing, auth and errors | Go unit/race tests; real local Navidrome FLAC integration | Behavior of other server implementations |
| Native navigation and queue ownership | 13 host scenarios with UBSAN/ASAN; Unicode, cancellation, deep copy and queue checks | Actual toolkit ABI/rendering, font and keyboard on the player |
| Executable acceptance in emulation | QEMU P5600 MIPS backend and original-library plugin/startup probes | Live kernel/CPU policy and memory use |
| Live server transport | 10 authenticated checks using the actual SD MIPS binary, audio/mp4 HEAD and two byte ranges | Decoding, audible sound, DAC and hardware controls |
| Packaging integrity | Complete inode/content/link audit and unchanged kernel | Updater acceptance, signatures, flash behavior and boot |
| Fallback/diagnostics | Host launcher and diagnostic tests | All hardware failure modes and recovery |

## Retained reports

- [Local Navidrome E2E](validation/local-navidrome-e2e.json): generated valid FLAC and byte equality, paging/search/playlists and range semantics.
- [MIPS fixture runtime](validation/mips-runtime.json): 17 backend checks and original-userland plugin loading.
- [Authenticated live MIPS probe](validation/live-server-mips.json): aggregate evidence only. Playlist detail was not exercised live because the account had no playlists; local fixtures cover it.
- [Reference dev2 packaging](validation/reference-firmware-validation.json): 3,741 paths, 3,016 regular-file hashes, 494 symlinks and 208 hardlink groups.

Personal filesystem paths and server identity were removed from public reports. Firmware payloads, real credentials, song metadata and audio are not included. Reports describe the reference run; contributors should produce their own current results.

The reference rootfs is **55,701,504 bytes**, compared with stock **55,738,368 bytes**. This removes a need for a larger rootfs image; it does not establish live partition capacity or bootability. The reference container hash is retained for traceability, not as a downloadable or hardware-approved release.

## Completion criteria

Native on-device support is incomplete until the exact player/firmware demonstrates updater acceptance, boot, readable interface and keyboard, authenticated browsing/search, decoded audio, seek/queue controls and sleep/resume behavior. A working recovery procedure is also not established. Software tests must not be described as successful hardware testing.
