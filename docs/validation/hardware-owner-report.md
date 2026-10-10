# M1 Plus hardware owner report

Date: **2026-10-10**. Device: **Shanling M1 Plus**.

The device owner reported that they had flashed the prepared firmware and it was working. When asked which functions were working, they confirmed **Navidrome browsing and music playback with audible output**.

| Observation | Evidence |
| --- | --- |
| Firmware update and subsequent startup | Owner reports successful flashing and operation |
| On-device Navidrome browsing | Explicit owner confirmation |
| Music playback with audible output | Explicit owner confirmation |

The development package prepared for this test was **V2.1-ND-dev2**, based on stock V2.1. Its reference SHA-256 is `0bfde116da76f7d433c383c62e226ddaab05f2abb907bf1216f96a9033fea322`. This identifies the staged package; the installed image has not been read back or independently hashed. See the [packaging report](reference-firmware-validation.json).

This is a user-reported result from one player. No boot snapshot, device log, photo or recording accompanied the report. The server identity, credentials and music metadata are omitted.

## Next device test

On 2026-10-10 the **V2.1-ND-dev3** package (SHA-256 `b2eba661c6f745ec9631887bd50775db6f9f3fec690c5772d0d90fd7ea108fda`, [packaging report](reference-firmware-validation-dev3.json)) and the matching SD payload were copied to the owner's card, keeping the existing account configuration. It adds the stock-style interface and the merged Vietnamese system font. It had not been flashed when this was recorded, so there is no dev3 device result yet. The later offline-download SD payload (plugin and backend only; the dev3 firmware is unchanged) has not been copied to the card or tested on the player yet.

## Remaining checks

- Search and native T9 input, long names and Unicode rendering.
- The `V2.1-ND-dev3` stock-style interface, the merged Vietnamese system font and the Now Playing switch (not part of this report).
- Playlist and multi-track queue transitions, seek and hardware controls.
- Sleep/resume, Wi-Fi loss/reconnection and longer playback sessions.
- Device resource usage, partition geometry, fallback behavior and physical recovery.

These checks have no individual hardware results yet. They are follow-up validation for broader support; the initial on-device browsing and audible streaming objective has an owner-reported successful result.
