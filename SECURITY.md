# Security

## Scope

This is experimental firmware integration. The supported research target is the pinned M1 Plus V2.1 image; no production or hardware-security support guarantee is made.

The extension stores a Navidrome password on the SD card, uses verified HTTPS upstream and exposes a loopback-only local proxy. FAT/exFAT cannot enforce file permissions. Prepared stream URLs are temporary bearer capabilities. Do not share credentials, configuration, authenticated URLs or capabilities in issues or logs.

## Reporting

For credential exposure, authentication bypass or unsafe update behavior, report privately through GitHub's **Security → Report a vulnerability** feature when available. If private reporting is unavailable, open an issue requesting a private contact channel without exploit details or secrets.

Include the affected commit/firmware hash, a minimal reproduction using disposable credentials and the expected/observed behavior. For a device failure, specify the last successful boot/update step; avoid repeated unverified flashing attempts.
