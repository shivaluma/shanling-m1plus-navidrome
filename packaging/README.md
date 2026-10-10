# Firmware packaging

`rebuild.py` combines the original pinned V2.1 SquashFS nodes with an explicit overlay. It never communicates with or flashes a player. The original kernel is copied unchanged.

Follow [the build guide](../docs/build.md) to supply your firmware, prepare `.work/analysis-v2.1` and build pinned SquashFS tools. The analysis extraction is not a rebuild source: it omits symlinks/special files and does not preserve all metadata. The repacker reads nodes directly from the original image.

```sh
python packaging/rebuild.py --overlay dist/rootfs \
  --output dist/firmware-V2.1-ND-dev2 --version V2.1-ND-dev2
```

The output directory must not exist. The tool rejects an original hash mismatch, inode type changes, partial/inconsistent hardlink replacement, unsupported original metadata and rootfs growth. Existing paths keep type, mode, uid/gid and mtime. Every rebuilt inode is read through the independent parser and checked by path/content hash, link target and hardlink equivalence; root metadata and compression settings are checked too.

The default size limit is the original 55,738,368-byte rootfs. `--allow-growth` requires an explicit independently verified `--max-rootfs-size`; it is not used by the reference build. The final overlay changes only `etc/init.d/S90play` and adds `usr/bin/launch-navidrome`.

The version must begin with `V2.1-ND-` and differ from V2.1. This addresses the known same-version UI guard; it does not establish acceptance of a development suffix. The default remains the early dev1 label for backward compatibility; commands here explicitly select dev2.

Outputs include the experimental update container, `rootfs.squashfs`, complete expected/actual inode inventories, filesystem validation, tool/command provenance and an experimental-status notice. The container inspector verifies member layout, payload MD5, uImage CRCs and SquashFS bounds. The source hash is checked again after packaging.

## Tests

```sh
python -m unittest discover -s packaging -p 'test_rebuild.py' -v
```

Ten negative/metadata tests check corrupt bytes, permissions/ownership, timestamps, symlinks, hardlinks, path presence, root metadata and overlay rules. The [retained metadata roundtrip](../docs/validation/metadata-roundtrip.json) records a full original-filesystem fixture run. Test images are excluded from publication.

## Stock-payload restore draft

```sh
python packaging/make_restore.py --output dist/restore-V2.1-ND-restore1
```

The original kernel/rootfs bytes and MD5 lines are retained; only the manifest version becomes `V2.1-ND-restore1` to avoid the stock application's same-version rejection. This is an offline-validated draft requiring a working updater. Acceptance/restored boot are unverified, and it cannot recover an unbootable device.
