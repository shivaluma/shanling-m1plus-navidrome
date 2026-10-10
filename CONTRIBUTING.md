# Contributing

Thank you for helping make native Navidrome support reproducible on the M1 Plus.

Start with [architecture](docs/architecture.md), [build](docs/build.md) and [validation](docs/validation.md). The most useful reports include the exact firmware hash, build environment, completed test steps and a clear separation of static, emulated and physical-device evidence.

## Changes

1. Open an issue for ABI, packaging or scope changes before undertaking a large implementation.
2. Keep firmware-specific ABI declarations in `native/nd_abi.h`; keep opaque toolkit objects opaque.
3. Run appropriate host tests. Changes to native ownership/cancellation need the sanitizer harness; changes to packaging need metadata and negative tests.
4. Describe the concrete before/after behavior and validation limits in the pull request.

Use four-space Python indentation, gofmt for Go and the existing C style. Prefer small changes with evidence over speculative compatibility patches. Python compatibility audits must not be run with optimization enabled.

## Reports and publication

Use the hardware-validation issue template for device results. Never attach `config.json`, passwords, auth tokens, loopback stream capabilities, personal library metadata or proprietary firmware images/libraries. Inspect diagnostics before sharing them. Generated test screenshots must be labeled as mocks.

CI runs firmware-independent tests. Build and runtime checks requiring your own firmware stay local. A green workflow does not certify a modified image for installation.

Project-authored contributions are submitted under the MIT license. Preserve upstream notices for third-party code.
