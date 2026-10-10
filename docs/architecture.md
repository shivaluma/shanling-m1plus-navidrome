# Architecture

## Native interface

`native/startup.c` interposes `__libc_start_main`, wraps the stock main function, removes `LD_PRELOAD` for child processes and waits for the exported AWTK main loop. Installation is posted through `idle_queue`; native widget and player operations run on the GUI thread.

`native/nd_ui.c` resolves the exact V2.1 symbols dynamically. Toolkit objects remain opaque. The extension adds a home slide with four stock-style tiles (Artists, Albums, Playlists, Search) and a native window that mirrors the stock pages: a 50 px navigation bar (`navbar_btnreturn_style`, a 22 px left title, `search_style` and `navbar_btnbackplayer_style`), a `list_view`/`scroll_view`/`scroll_bar_m` list with 60 px `list_item_style` rows (stock round icons, white 20 px title, grey detail line, right arrow), a "Play all" header for albums and playlists, and orange accent tabs for search results. Lists render 50 rows at a time with Previous/Show more rows; status, spinner (`imge_wait*`), Retry and a transient toast sit above the list. Back at the root closes the window, like stock pages. Styles are taken from the firmware theme by name, with inline fallbacks only if a style is missing. Network requests run in a serialized worker. Generation checks discard results after navigation or closure; row callbacks are tied to the list that created them, so playback does not rebuild or rescroll the list.

After queueing, the extension makes the same `navigator_switch_to_with_context("playing_page", {NULL, 0, 255, 2}, 0)` request as the stock "back to player" button, so the stock Now Playing page opens over the Navidrome window. This and `widget_layout` are optional symbols: if either is missing, only that convenience is lost.

Playback uses the recovered 88-byte O32 `stSongInfo` layout and the stock queue functions. Strings are deep copied; deterministic negative IDs avoid local-library IDs. Album/playlist queues are limited to a 128-track chunk containing the selected track; search queues the current result page. This recovered ABI is pinned to one firmware executable and remains subject to hardware testing.

## Loopback proxy

The standard-library Go service binds only `127.0.0.1:17831` and rejects foreign Host/Origin headers. It translates the native JSON API into Subsonic calls over verified HTTPS. The native worker accepts at most 2 MiB of JSON; upstream API responses are capped at 8 MiB. Large artist indexes need further work.

Authentication uses a fresh salt and the Subsonic MD5 token protocol. The password stays in local configuration and process memory. Cross-origin redirects are refused. Errors omit arbitrary upstream messages and authenticated URLs.

Preparing a track creates a random 128-bit loopback bearer capability with a 12-hour lifetime, up to 256 active capabilities. The stock player receives that local URL. The proxy streams original media incrementally, forwarding range semantics; it does not transcode or scrobble. Capabilities are temporary secrets and should not be logged.

See [the backend API](../backend/README.md).

## Offline downloads

Album and playlist headers carry a download button (stock `download_more`; `cancel` while running; `mulselected` once on the card, `delete_more` after one tap to arm removal). Saved songs show the stock orange check. A Downloads page (home tile) lists jobs with progress, free space and an **Update library** button. While a job runs, the plugin polls `GET /v1/downloads` every two seconds on the same serialized worker; polls never set the busy state, and any user request supersedes them. In-place updates keep the Downloads list scrolled; when a job finishes, the album/playlist reloads once to refresh its checks.

The backend writes files to `/mnt/mmc/Navidrome` (see [backend](../backend/README.md#offline-downloads)). The stock library only sees them after a scan. Reverse engineering showed that the stock "scan specific folders" path (`scanSpecFolder` `0x4e9e90`) calls `toolsInitDatabase`, which executes `drop table songtable`, so scanning only the download folder would empty the rest of the library index. The extension therefore never starts a scan; **Update library** opens the stock `systemset/updatemusic_page` with exported `navigator_to` (`0x4d1d80`) so the user runs the normal stock scan.

## UI font

The stock application loads one font, `assets/default/raw/fonts/default.ttf` (Microsoft YaHei + Arial at 256 units/em), through AWTK's stb_truetype loader (`font_loader_stb`), which does no shaping or kerning. It lacks 49 Vietnamese letters such as ơ, ư and ạ. `scripts/build_font.py` replaces every code point covered by Be Vietnam Pro (459) with decomposed outlines scaled to 256 units/em and keeps all other glyphs and the stock ascent/descent, so stock layouts keep their baseline. Glyphs that a stock code point outside Be Vietnam Pro shares are not overwritten. Zero-width combining marks are moved over the preceding glyph for unshaped rendering, and the backend composes decomposed (NFD) metadata to NFC with a generated table (`backend/nfc_table.go`, from `scripts/gen_nfc_table.py`). Glyph names and OpenType layout tables, which stb_truetype never reads, are dropped. The result is 9,344,124 bytes (stock 9,794,280). Stacked capital diacritics extend above the stock ascent, so very tight stock labels may clip their top pixels.

## Startup and storage

The rootfs overlay changes `etc/init.d/S90play` to call `usr/bin/launch-navidrome`. The launcher checks SD enable/config/payload files, passes the CA bundle explicitly and limits the Go runtime with `GOMEMLIMIT=24MiB`, `GOGC=50` and `GOMAXPROCS=2`. These settings do not prove actual device memory requirements.

Missing files or an early service exit start the stock application. A nonzero extended-application exit also restarts the stock application. The launcher does not monitor all later service failures or recover bootloader/kernel failures.

An independent diagnostic marker enables a bounded SD snapshot of kernel, CPU/ISA, memory, MTD partitions, selected mount flags and initial launch state. It does not collect configuration, environment, library metadata or application logs.

## Packaging boundary

The repacker reads the original SquashFS nodes directly. It preserves unchanged content and metadata, symlinks and hardlink equivalence, retains the kernel and rejects rootfs growth by default. The analysis extraction deliberately omits live symlinks and special files and must never be used as a replacement rootfs tree.

[Native ABI evidence](research/native-integration-evidence.md) · [Kernel evidence](research/kernel-abi-and-memory-evidence.md) · [Capacity evidence](research/flash-capacity-evidence.md)
