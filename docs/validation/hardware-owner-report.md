# M1 Plus hardware owner report

Date: **2026-10-10**. Device: **Shanling M1 Plus**.

The device owner reported that they had flashed the prepared firmware and it was working. When asked which functions were working, they confirmed **Navidrome browsing and music playback with audible output**.

| Observation | Evidence |
| --- | --- |
| Firmware update and subsequent startup | Owner reports successful flashing and operation |
| On-device Navidrome browsing | Explicit owner confirmation |
| Music playback with audible output | Explicit owner confirmation |

The development package prepared for this test was **V2.1-ND-dev2**, based on stock V2.1. Its reference SHA-256 is `0bfde116da76f7d433c383c62e226ddaab05f2abb907bf1216f96a9033fea322`. This identifies the staged package; the installed image has not been read back or independently hashed. See the [packaging report](reference-firmware-validation.json).

This dev2 result is user-reported from one player. No boot snapshot, device log, photo or recording accompanied that first report. The server identity, credentials and music metadata are omitted.

## V2.1-ND-dev3 and later SD payloads

The owner flashed **V2.1-ND-dev3** and reported that the merged Vietnamese system font works. Two owner photos show the extension running on the player:

| Photo | Observed on the device | Payload |
| --- | --- | --- |
| [Home page](../images/device-home.jpg) | Added home page with "NAVIDROME" caption and six tiles (Artists, Albums, Playlists, Search, Downloads, Update library), the slide indicator with four pages and the stock now-playing bar. | Offline-download SD payload (copied 2026-10-10, 23:13 local) |
| [Search](../images/device-search.jpg) | Search screen with the stock search box and placeholder text, accent Songs tab, Albums/Artists tabs and the empty-search message. | dev3 SD payload |

The photos establish rendering of these screens on the player; they do not by themselves verify downloads, library updates or the touch-responsiveness changes, which were not on the card when they were taken. In the search photo the white label on the active orange tab is hard to read; this may be camera exposure and should be checked on the screen.

The boot snapshot from that card (dev3 backend) reported kernel 5.10.21+, Ingenic X2000 with two XBurst II cores (MIPS32r2/r5, MSA), 117,884 kB RAM with 93,992 kB available, no NaN boot override, `vfat` SD mount without `noexec`, and `backend_initial_state=alive`. The card had 53 GB free.

## Next device test

On 2026-10-10 the **V2.1-ND-dev3** package (SHA-256 `b2eba661c6f745ec9631887bd50775db6f9f3fec690c5772d0d90fd7ea108fda`, [packaging report](reference-firmware-validation-dev3.json)) and the matching SD payload were copied to the owner's card, keeping the existing account configuration. It adds the stock-style interface and the merged Vietnamese system font. It has since been flashed (see above). The offline-download payload was copied to the card later the same evening. The responsiveness payload (instant-press rows, instant Back, batched preparation) has not been copied or tested yet.

## Remaining checks

- Search and native T9 input, long names and Unicode rendering.
- The `V2.1-ND-dev3` stock-style interface, the merged Vietnamese system font and the Now Playing switch (not part of this report).
- Playlist and multi-track queue transitions, seek and hardware controls.
- Sleep/resume, Wi-Fi loss/reconnection and longer playback sessions.
- Device resource usage, partition geometry, fallback behavior and physical recovery.

These checks have no individual hardware results yet. They are follow-up validation for broader support; the initial on-device browsing and audible streaming objective has an owner-reported successful result.
