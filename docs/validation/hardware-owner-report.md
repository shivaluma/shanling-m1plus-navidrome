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

## Remaining checks

- Search and native T9 input, long names and Unicode rendering.
- Playlist and multi-track queue transitions, seek and hardware controls.
- Sleep/resume, Wi-Fi loss/reconnection and longer playback sessions.
- Device resource usage, partition geometry, fallback behavior and physical recovery.

These checks have no individual hardware results yet. They are follow-up validation for broader support; the initial on-device browsing and audible streaming objective has an owner-reported successful result.
