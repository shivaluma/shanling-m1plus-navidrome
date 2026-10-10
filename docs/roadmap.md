# Roadmap

## Next: hardware evidence

- Confirm updater acceptance and boot on the pinned V2.1 target.
- Capture CPU/ISA, NaN policy clues, RAM and actual MTD partition sizes.
- Verify home integration, native layout/T9 keyboard and error states.
- Verify decoded audio, DAC output, seek, multi-track queues and hardware controls.
- Exercise sleep/resume and Wi-Fi loss/reconnection.
- Establish a physical recovery method before claiming recoverability.

## Follow-up improvements

- Measure artist-index/large-library memory usage and pagination limits.
- Localize interface messages after native behavior is verified.
- Improve service health reporting and expired-capability queue behavior.
- Expand compatible-host/toolchain coverage with reproducible build records.

## Scope decisions

Current work targets Navidrome over the Subsonic API. Jellyfin, offline cache, transcoding, scrobbling and a replacement player are not scheduled. Firmware-version support must be added through independent ABI evidence and tests, not model-name similarity.
