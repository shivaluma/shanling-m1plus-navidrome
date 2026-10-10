# Roadmap

## Next: hardware evidence

- Retain diagnostics and firmware identification for the first owner-reported successful update, Navidrome browsing and audible playback; see the [hardware report](validation/hardware-owner-report.md).
- Capture CPU/ISA, NaN policy clues, RAM and actual MTD partition sizes.
- Verify home integration, native layout/T9 keyboard and error states.
- Verify the stock-style lists, the merged Vietnamese font across stock pages and the Now Playing switch.
- Verify offline downloads on the card: write speed, Wi-Fi loss/sleep mid-download and the stock scan of `Navidrome/`.
- Verify decoded audio, DAC output, seek, multi-track queues and hardware controls.
- Exercise sleep/resume and Wi-Fi loss/reconnection.
- Establish a physical recovery method before claiming recoverability.

## Follow-up improvements

- Measure artist-index/large-library memory usage and pagination limits.
- Localize interface messages (Vietnamese first) after native behavior is verified.
- Album artwork from Navidrome in list rows.
- Improve service health reporting and expired-capability queue behavior.
- Expand compatible-host/toolchain coverage with reproducible build records.

## Scope decisions

Current work targets Navidrome over the Subsonic API. Offline downloads save files for the stock library and player; Jellyfin, streaming transcoding, scrobbling and a replacement player are not scheduled. Starting a library scan automatically stays out of scope while the only targeted stock scan drops the song table. Firmware-version support must be added through independent ABI evidence and tests, not model-name similarity.
