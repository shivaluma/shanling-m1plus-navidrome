# Installation and device validation

**Experimental: one owner reports successful flashing, Navidrome browsing and audible playback on an M1 Plus.** See the [hardware report](validation/hardware-owner-report.md) for scope and remaining checks. A working recovery method has not been established. The restore draft requires a functioning updater and does not recover a player that cannot boot.

## Prepare the SD payload

After building, mount the player's card using USB Storage or a card reader:

```sh
python scripts/configure.py --sd /path/to/mounted-card --stage-only
```

This verifies/copies four payload files and removes the enable marker. It does not request credentials, overwrite account configuration or copy firmware.

To configure your own server and enable the extension:

```sh
python scripts/configure.py --sd /path/to/mounted-card \
  --server https://music.example.org/
```

Replace the example URL. Username and password are entered locally; the password is hidden. HTTPS login is verified before configuration is written and `enable` is created last. An interrupted copy leaves the extension disabled. The helper identifies itself as `ShanlingNavidromeSetup`; some proxies reject Python's default User-Agent.

The card stores the password in `navidrome/config.json`. FAT/exFAT cannot enforce Unix permissions. Keep the card and configuration private; never attach configuration to an issue. HTTP requires explicit `--allow-http` and is intended only for deliberately unencrypted test setups.

## Optional boot snapshot

Add `--diagnostics` during account setup, or prepare diagnostics with the extension disabled:

```sh
python scripts/configure.py --sd /path/to/mounted-card --diagnostics-only
```

A separate `diagnostics-enable` marker opts in. A running dev2 hook writes `navidrome/diagnostics/boot.txt`. Stock firmware does not execute this collector. Remove `diagnostics-enable` to stop collection. Read the snapshot before sharing it; it contains hardware/partition details, not credentials or application logs.

## Manual firmware update

Build and inspect the experimental container and its `validation.json` first. If you choose to test it, copy it to the card root as `update.bin`, verify its SHA-256 after copying and eject the card safely. The tools never initiate flashing.

[Shanling's M1 Plus instructions](https://en.shanling.com/download/118) call for a fully charged player and **System settings → System Update → TF card update**, followed by waiting for the restart with the card present. Those vendor instructions describe the updater workflow; they do not validate this modified package.

The reference commands use `V2.1-ND-dev3` for the stock-style interface and Vietnamese font (`V2.1-ND-dev2` was the first owner-tested build). About may still display V2.1 because the stock application and its embedded version are unchanged. A successful update message alone does not prove the extension started.

## Verify on the device

Record these observations individually:

1. Updater acceptance and a normal reboot into Shanling's application.
2. A Navidrome home page with four tiles, stock-looking lists that scroll, and a working search keyboard.
3. Vietnamese text (for example "Nghệ sĩ Sơn Tùng") in both Navidrome lists and the stock library/Now Playing pages, with CJK titles still readable.
4. Selecting a song opens the stock Now Playing page, and its Back returns to the Navidrome list.
5. Wi-Fi and a correct device clock for verified HTTPS.
6. Artist → album → track browsing and a successful search.
7. Actual decoded sound through the intended output, pause/resume and seek.
8. Multi-track next/previous, queue behavior, sleep/resume and reconnect after Wi-Fi loss.
9. After reconnecting USB Storage, the boot snapshot's kernel/CPU/RAM/MTD and initial backend state.

Use the [hardware issue template](../.github/ISSUE_TEMPLATE/hardware-validation.yml). A snapshot can prove only its reported boot facts; it cannot prove GUI rendering or sound.

## Disable and troubleshoot

```sh
python scripts/configure.py --sd /path/to/mounted-card --disable
```

This removes `enable` and retains account configuration. The next boot uses the stock application path. It requires the updated kernel/init to boot; it is not bootloader recovery. The SD card must remain present while the extension is in use. A `noexec` mount, missing CA, bad clock, ABI rejection or startup failure may prevent operation.

For an updater rejection, retain the exact message and package hash. Do not change hashes or manifest labels at random to force an update. For service/UI failures, report the last completed step and an inspected diagnostic snapshot, without credentials, authenticated URLs or stream capabilities.
