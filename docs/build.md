# Build from source

## Supported reference environment

The reference build used macOS arm64, Python 3.13, Go 1.24.5, GCC 15.2.0 and SquashFS tools 4.7.5. Native host tests also run on Linux. Automatic cross-toolchain bootstrap supports macOS arm64 and x86_64; the x86_64 archive and full Linux cross-build have not been validated here.

The native plugin links against **original firmware glibc 2.28/libcurl 7.73.0**. The cross-toolchain's libc is not shipped or substituted for those libraries.

## 1. Install host prerequisites

On macOS with Homebrew:

```sh
brew install go python@3.13 lzo lz4 xz zstd bdw-gc guile
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Clang/Xcode command line tools, Git, make and patch are needed. `requirements-dev.txt` pins the parser and disassembler versions used by this project.

## 2. Supply your own V2.1 firmware

Obtain the M1 Plus V2.1 update from [Shanling](https://en.shanling.com/download/118), unzip it and prepare the local workspace:

```sh
python scripts/prepare_firmware.py --original /absolute/path/to/update.bin
```

Supported input SHA-256:

```text
1ccb4667e3e9bdf9a35be4dc58d09b898ca53c5fd58396b7667fb91fe895f925
```

The command inspects the update container, copies the matching input to ignored `inputs/update.bin` and extracts regular files/link metadata into `.work/analysis-v2.1/`. It does not run firmware code. A mismatched input is rejected. A partial extraction must be inspected before retrying.

## 3. Bootstrap pinned dependencies

```sh
python scripts/bootstrap_dependencies.py --toolchain
python packaging/bootstrap_tools.py
```

The first command obtains the dated CA bundle and GCC cross-toolchain with fixed SHA-256 checks. Toolchains stay under ignored `tools/`. To reuse a matching downloaded archive, add `--toolchain-archive /path/to/archive.tar.gz`.

cJSON and curl headers/licenses are included as source. The CA bundle is fetched at build time. SquashFS source and the macOS stat-field patch are pinned; the patch applies only on macOS.

For another compatible toolchain, set `CROSS_COMPILE` to its full executable prefix and `MIPS_SYSROOT` to its sysroot. It must support O32/MIPS32r2, FP64 and NaN2008; import audits still use the original firmware libraries. Such a toolchain is not covered by the reference-build claim.

## 4. Build and audit

```sh
python scripts/build.py
python packaging/rebuild.py --overlay dist/rootfs \
  --output dist/firmware-V2.1-ND-dev2 --version V2.1-ND-dev2
```

Choose a new output directory for each rebuild. The commands produce:

| Output | Purpose |
| --- | --- |
| `dist/sdcard/navidrome/` | Backend, native plugin, CA bundle and diagnostics collector |
| `dist/rootfs/` | Minimal guarded startup overlay |
| `dist/elf-audit.json` | Imports, library versions and MIPS ABI checks |
| `dist/build-report.json` | Checksums used by the SD setup helper |
| `dist/firmware-V2.1-ND-dev2/validation.json` | Full filesystem/container validation |
| `dist/firmware-V2.1-ND-dev2/update-…-EXPERIMENTAL.bin` | Experimental update container |

The backend is a static `CGO_ENABLED=0`, `GOMIPS=softfloat` Go executable. A constrained audit adjusts only ELF `e_flags` to declare NaN2008; it does not change machine code or make arbitrary MIPS binaries compatible. See [MIPS evidence](research/mips-runtime.md).

Do not run build audits with Python `-O` or `PYTHONOPTIMIZE`; the scripts reject it. A successful build establishes the documented offline checks, not hardware bootability.

## Optional research outputs

```sh
python analysis/analyze.py
python analysis/disassemble.py main application_init
```

These generate ignored ELF inventories, symbols, strings and bounded disassembly. Expanded kernel evidence requires the original expanded kernel payload from a separate analysis step; the repository contains the retained bounded evidence, not that proprietary payload.

See [packaging details](../packaging/README.md), [testing](testing.md) and [device installation](installation.md).
