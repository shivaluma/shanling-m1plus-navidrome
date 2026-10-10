# M1 Plus V2.1 flash-capacity evidence

The supplied firmware does **not** establish the active rootfs partition capacity. A valid embedded device tree contains concrete NOR/NAND partition templates, but its SFC controller sets `ingenic,use_ofpart_info` to zero. Ingenic documents this setting as taking the MTD partition information supplied by the burner rather than selecting the device-tree partition table. The active flash parameter block and recovery image were not supplied. **Do not treat the template's 247 MiB NAND rootfs as verified capacity.**

## Reproducible local evidence

`packaging/inspect_flash.py` scans the complete expanded kernel for FDT magic, validates version/header/structure bounds, parses all node/property tokens, and saves raw hexadecimal properties with decoded cells/strings. It also performs a bounded whole-image search for boot-argument strings containing `mtdparts=`, `root=/dev/`, `ubi.mtd=`, and `rootfstype=`.

```sh
python3 packaging/inspect_flash.py \
  '.work/analysis-v2.1/kernel-unpacked.bin' \
  --out packaging/docs/kernel-dtb-evidence.json
```

The derived kernel is 12,051,904 bytes, SHA-256 `b85c34385bbe57b5a0b422a6d541a3cfff5108f63ca8918180924ec5ed0fe97f`. It came from the supplied V2.1 `xImage`, not a downloaded kernel. One valid FDT v17 appears at expanded-kernel byte offset `0x00b6cba0`, length 30,354 bytes, SHA-256 `bdb15604265d87a6c2016d56a11993ca0eb1f78825c60e55802679d90870c409`, with 292 nodes. Full node data is in [kernel-dtb-evidence.json](../../packaging/docs/kernel-dtb-evidence.json).

The `/ahb2/sfc@0x13440000` node is enabled (`status = "okay"`), compatible with `ingenic,x2000-sfc`, and contains:

| Property | Observed raw value | Meaning established here |
| --- | --- | --- |
| `ingenic,use_ofpart_info` | One byte `00` | DT partition selection is not enabled by this property. |
| `ingenic,spiflash_param_offset` | Four bytes `00000000` | A default parameter location is requested; this is not a supplied parameter block. |
| NOR/NAND `#address-cells`, `#size-cells` | One each | Each partition `reg` decodes as a 32-bit offset followed by a 32-bit length. |

The `norflash@0/partitions` children decode as follows. These offsets are relative to the flash device, not update-container offsets.

| Label | Flash offset | Length | Length in MiB | Exclusive end |
| --- | ---: | ---: | ---: | ---: |
| `uboot` | `0x00000000` | `0x00040000` | 0.25 | `0x00040000` |
| `kernel` | `0x00040000` | `0x00300000` | 3 | `0x00340000` |
| `rootfs` | `0x00360000` | `0x00ca0000` | 12.625 | `0x01000000` |

The `nandflash@1/partitions` children decode as follows.

| Label | Flash offset | Length | Length in MiB | Exclusive end |
| --- | ---: | ---: | ---: | ---: |
| `uboot` | `0x00000000` | `0x00100000` | 1 | `0x00100000` |
| `kernel` | `0x00100000` | `0x00800000` | 8 | `0x00900000` |
| `rootfs` | `0x00900000` | `0x0f700000` | 247 | `0x10000000` |

The NOR template cannot contain the supplied vendor payloads: the original kernel is 5,410,880 bytes and the original rootfs is 55,738,368 bytes. The NAND template is numerically large enough, but this does not show that it is active. The documented generic Ingenic example has the same NAND partition layout, further supporting treatment as a template rather than evidence of this player's live geometry.

## Why the active partition table is still unknown

The [Ingenic SFC documentation](https://gitee.com/ingenic-dev/ingenic-linux-docs/blob/ingenic-master/zh-cn/X26XX/x2670-halley/kernel/SFC%E6%8E%A7%E5%88%B6%E5%99%A8.md) explains that default MTD partition information comes from the burner, setting `ingenic,use_ofpart_info` to one selects device-tree partitions, and a zero flash-parameter offset uses default location `0x5800`. This is primary documentation for the same driver/property family on a different board generation; it corroborates interpretation of the observed property, not the physical geometry of the M1 Plus.

The supplied kernel itself contains corresponding driver strings at these expanded-kernel offsets:

| Offset | String |
| --- | --- |
| `0x00a7f938` | `ingenic,spiflash_param_offset` |
| `0x00a7f97c` | `ingenic,use_ofpart_info` |
| `0x00a8003c` | `NOTICE: this flash haven't param, magic_num:%x` |
| `0x00a8006c` | `read partition from flash failed!` |
| `0x00a80090` | `read flash partition failed!` |
| `0x00a800bc` | `read nand partition failed!` |
| `0x00a8089c` | `Use burner params.` |

These strings demonstrate code support for externally supplied partition parameters; they are not a recording of which branch executed on the device.

There is no `/chosen` node carrying bootargs in the parsed FDT. Whole expanded-kernel printable-string search found no `mtdparts=`, `root=/dev/`, or `ubi.mtd=` argument. The sole matching item is the generic string `rootfstype=` at `0x00b6b88f`, which provides no partition size. Bootloader-supplied runtime arguments remain outside the available evidence.

The original `/etc/init.d/S10mount_ubifs` invokes `mount_ubifs.sh userdata /mnt/data/`. The original `/usr/bin/mount_ubifs.sh` resolves the named partition using `/proc/mtd` before attaching UBI. Neither DT template contains `userdata`; the firmware therefore expects information absent from these templates. This is additional reason to obtain the active table rather than infer capacity from the templates. No device script was executed during this investigation.

## Evidence needed to close the capacity gate

A read-only `/proc/mtd` capture from this player would establish named MTD partition lengths and erase sizes. Matching `/sys/class/mtd/mtd*/size`, `/sys/class/mtd/mtd*/name`, and `/proc/cmdline` would corroborate the table and root mapping. Boot logs, a supplied flash dump containing the actual Ingenic partition parameter block, or a vendor board-specific flashing configuration could provide additional static evidence. A rootfs image's compressed length alone cannot provide this information. Bad-block handling and updater behavior still require recovery/device evidence even after partition lengths are known.

The final repacker enforces a different, evidence-based constraint: the rebuilt rootfs image must be no larger than the original 55,738,368-byte rootfs. The delivered rootfs overlay includes only a guarded launcher and the S90play reference; backend/plugin/CA assets stay on the SD card. This avoids requiring enlarged rootfs capacity. It does not establish the active partition geometry, update acceptance or bootability. A future explicit growth allowance requires a separately supplied verified size limit; it is not used for the final package.

## S90play overlay and hardlink audit

The original SquashFS `/etc/init.d/S90play` has inode 108, mode `0100775` (permissions `0775`), uid/gid zero, mtime 1,709,365,818, size 410 bytes. A complete traversal found **one path** with inode 108, so replacing this path cannot silently split an original hardlink group.

`rebuild.py` preserves its metadata while changing only the requested file bytes. The exact-current metadata fixture changes S90play by adding a comment; the complete rebuilt filesystem audit confirms mode/uid/gid/mtime preservation and all 208 original hardlink groups. See [metadata-roundtrip-final/validation.json](../validation/metadata-roundtrip.json).

For any other existing hardlinked path, the overlay builder rejects partial or inconsistent replacement of the group. Consistent replacement keeps the original group identifier, emits the first member as data and subsequent names as TAR hardlinks, then verifies the full path equivalence groups in the rebuilt SquashFS. The negative tests include hardlink splitting, merging, and incomplete overwrite; inode renumbering alone is allowed.
