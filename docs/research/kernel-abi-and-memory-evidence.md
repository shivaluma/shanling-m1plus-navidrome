# M1 Plus kernel NaN ABI policy and memory evidence

The supplied kernel **does check NaN personality before the soft-float ABI logic**. The original static Go backend lacked `EF_MIPS_NAN2008`; it could therefore be rejected with `ENOEXEC` if the live kernel disables legacy-NaN executables. A soft-float ABI does not bypass that gate. The final backend deliberately declares NaN2008 after static soft-float/build-setting checks, preserving every byte outside ELF `e_flags`; its original is retained. QEMU P5600 rejects the original and executes the adjusted binary. This proves emulator compatibility, not the actual player kernel policy. The native UI library and stock Shanling application both advertise NaN2008/FP64 and match one another at this level.

## Current binaries

The read-only historical snapshot in [current-elf-abi.json](../validation/reference-elf-abi.json) records hashes and `.MIPS.abiflags` **before** the backend declaration adjustment. The first row below describes that retained original; the final backend flags are `0x50001404` (NaN2008 bit set). Current hashes, proof of byte-range preservation, and execution results are in [results.json](../validation/mips-runtime.json) and [mips-runtime.md](mips-runtime.md).

| Binary | ELF flags | ISA | FP ABI | NaN2008 bit |
| --- | --- | --- | --- | --- |
| Original, now `dist/unadjusted/navidrome-service` | `0x50001004` | MIPS32r1 | 3 (soft float) | Clear |
| `dist/libnavidrome_ui.so` | `0x70001407` | MIPS32r2 | 6 (FP64) | Set |
| Original `/release/bin/demo` | `0x70001405` | MIPS32r2 | 6 (FP64) | Set |

The backend has no ELF interpreter; the stock application has one. Static linking removes an interpreter-matching requirement but does not remove the kernel's NaN-personality check. The 24 non-NaN2008 ELF objects in the original filesystem are kernel modules, not user executables, so their presence does not prove legacy user-executable acceptance.

## Supplied-kernel code evidence

The expanded V2.1 kernel SHA-256 is `b85c34385bbe57b5a0b422a6d541a3cfff5108f63ca8918180924ec5ed0fe97f`. Bounded extraction is reproducible with:

```sh
python3 packaging/inspect_kernel_policy.py \
  '.work/analysis-v2.1/kernel-unpacked.bin' \
  --out packaging/docs/kernel-abi-evidence.json
```

The script pins the kernel hash, verifies an early-parameter table entry, disassembles selected spans, and saves both [JSON](../../packaging/docs/kernel-abi-evidence.json) and [assembly](../../packaging/docs/kernel-abi-evidence.asm). Virtual addresses use inferred flat-kernel load base `0x80010000`. This base makes the unique `ieee754` early-parameter string pointer resolve to its observed bytes and the associated function pointer resolve to coherent code; file offsets are also recorded to keep the evidence independent of that mapping inference.

The kernel's `arch_check_elf` candidate has the following observed sequence:

- `0x800169c4`: reads ELF class. The ELF32 branch selects `e_flags` at ELF32 header offset `0x24`; ELF64 uses offset `0x30`.
- `0x800169d8`: masks `e_flags` with `0x400` (`EF_MIPS_NAN2008`).
- If set, `0x800169e8` loads the enable byte at `0x80b930d0`; if zero, execution branches to rejection.
- If clear, `0x80016b74` loads the enable byte at `0x80b930d1`; if zero, execution branches to the same rejection.
- `0x80016c34` returns `-8`, the MIPS/Linux `ENOEXEC` value.
- The FP ABI mode checks appear after the NaN branch succeeds. They do not exempt ABI 3 from the initial branch.

This matches the ordering in [upstream Linux 5.10 `arch/mips/kernel/elf.c`](https://github.com/torvalds/linux/blob/v5.10/arch/mips/kernel/elf.c#L123-L167), where NaN personality is checked before FP mode selection. The supplied vendor code was inspected directly; upstream source supplies names and semantics, not proof that the vendor source tree is identical.

The early-parameter table at expanded-kernel file offset `0xb746ac` contains `{0x80b7b92b, 0x80b4a990, 1}`. The first pointer resolves to `ieee754`; the second points to a parser that recognizes `strict`, `legacy`, `2008`, and `relaxed`, stores mode values zero through three, then calls the NaN policy update candidate at `0x80025a54`.

In its zero-mode branch, the policy updater copies runtime CPU feature bits 6 and 7 from a CPU-data word at `0x80af1eb4` to the legacy and NaN2008 enable bytes. Its relaxed-mode branch sets both bytes to one. This is consistent with [upstream Linux 5.10 `fpu-probe.c`](https://github.com/torvalds/linux/blob/v5.10/arch/mips/kernel/fpu-probe.c#L133-L230), which initializes the mode to strict and allows boot arguments to select policy. The observed mode-storage address `0x80b93608` lies beyond the end of the flat image, consistent with zero-initialized BSS. **The expected strict default is an inference; live boot arguments, runtime CPU probing, and the final policy bytes were not observed.**

## Configuration and RAM search boundary

Whole-image byte scans found no `IKCFG_ST` or `IKCFG_ED` markers in either the supplied `xImage` or the expanded kernel, so an embedded IKCONFIG gzip was not recovered. This does not prove every possible vendor configuration carrier is absent; it rules out the standard marker-bound config in these images.

The parsed embedded device tree has no memory node or `/chosen` bootargs. Whole expanded-kernel scans found no `memsize` string and no concrete embedded `ieee754=` assignment. The kernel contains generic early-parameter names `mem`, `memmap`, and `ieee754`, and dynamic RAM-detection messages; these do not specify physical RAM or available RAM. The expected bootloader may inject memory/bootargs at runtime, but that behavior was not observed.

Physical RAM, usable RAM after reservations, free RAM at player startup, and peak backend memory consumption on the device are therefore unverified. They must not be inferred from the kernel image size, the MIPS ISA generation, the rootfs partition template, or host test memory usage.

## Remaining verification

A device capture of `/proc/cmdline`, `/proc/cpuinfo`, `/proc/meminfo`, kernel boot logs, and a harmless backend execution/self-test would resolve much of this gap. A kernel NaN-policy probe must exercise the actual kernel, not a host QEMU userspace implementation. A vendor-matched kernel configuration/source or a fuller validated disassembly could strengthen static conclusions but would still not reveal runtime CPU feature values and bootloader arguments by itself.

Until verified on hardware, backend startup should report execution failure and preserve the stock player's boot path. The final change is an explicit NaN-personality metadata declaration for a verified static `CGO_ENABLED=0`, `GOMIPS=softfloat` Go binary, not a claim that arbitrary MIPS binaries can be repaired by changing flags. The local Go 1.24.5 runtime `softfloat64.go` defines quiet NaNs by setting the highest mantissa bit, consistent with IEEE2008 encoding. The adjustment checks the ELF and build settings, retains the original, changes only bytes 36–39, and verifies all machine code unchanged. This addresses the observed P5600 rejection; live player execution remains unverified.
