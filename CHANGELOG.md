# Changelog

## Unreleased — stock-style interface and Vietnamese font (`V2.1-ND-dev3`)

- Native interface rebuilt from stock pages: 2×2 home tiles, navigation bar with return/search/back-to-player icons, scrolling 60 px rows with stock icons and format badges, "Play all" headers, accent search tabs, spinner, Retry and toast.
- Lists load 50 rows at a time; playback no longer rebuilds or rescrolls the list and opens the stock Now Playing page.
- Be Vietnam Pro merged into the stock YaHei/Arial system font (`scripts/build_font.py`, `--font-weight`, `--stock-font`); CJK and other scripts kept.
- Backend composes decomposed (NFD) display metadata to NFC for the unshaped device renderer.
- Offline approximate renderer for harness widget trees (`tests/native-host/preview.py`).
- Offline downloads: album/playlist download button, Downloads page and home tiles; backend queue writing original files (or optional transcodes) to `/mnt/mmc/Navidrome` with FAT32-safe names, `.part` resume, retries, free-space reserve, covers, `.m3u8` playlists, ownership-aware removal and a restart-safe manifest. The UI opens the stock "Update music library" page, because the stock folder-only scan drops the song table.
- Boot diagnostics also report SD writability and free space.
- Responsiveness: rows are instant-press buttons (stock `list_item` delays its pressed state by 30 ms), icon buttons and tiles show a pressed tint, taps supersede running requests instead of being ignored, Back redraws the cached list at its scroll position without a request, and a queue chunk is prepared with one batched `/v1/prepare` request.
- Host, sanitizer, MIPS, real-Navidrome and packaging evidence only; no device result yet for this interface, font or downloads.

## Experimental source publication

- Native artist/album/playlist browsing, category search and stock T9 input.
- Recovered O32 playback queue integration with bounded chunks and deep-copied metadata.
- Verified-HTTPS Subsonic backend with temporary loopback streaming capabilities and range support.
- Guarded SD-enabled startup integration, local account setup and optional boot snapshots.
- Metadata-preserving SquashFS packaging with no-growth default and a stock-payload restore draft.
- Host/sanitizer, local Navidrome, MIPS fixture and authenticated live-server evidence.
- Public build workspace, contributor documentation and firmware-independent CI.
- First M1 Plus owner report: successful firmware update, working Navidrome browsing and audible playback (2026-10-10).

No prebuilt firmware release is distributed. The development manifest label `V2.1-ND-dev2` remains experimental; one successful owner report does not establish compatibility across other players or firmware versions.
