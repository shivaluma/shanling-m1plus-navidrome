#!/usr/bin/env python3
"""Offline, experimental M1 Plus firmware repacker. Never flashes a device."""
import argparse
import collections
import copy
import hashlib
import importlib.util
import io
import json
import pathlib
import stat
import subprocess
import sys
import tarfile
import tempfile

BASE = pathlib.Path(__file__).resolve().parent
PROJECT = BASE.parent
DEFAULT_ANALYSIS = PROJECT / '.work/analysis-v2.1'
ORIGINAL_SHA256 = '1ccb4667e3e9bdf9a35be4dc58d09b898ca53c5fd58396b7667fb91fe895f925'
ROOTFS_MEMBER = 'recovery-update/rootfs.squashfs'
KERNEL_MEMBER = 'recovery-update/xImage'
INFO_MEMBER = 'firmware_v20.info'


def digest_stream(stream):
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(chunk)
    return h.hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return digest_stream(stream)


def inventory(fs):
    records, nodes = {}, {}
    def visit(node, path):
        if path != '.' and (pathlib.PurePosixPath(path).is_absolute() or '..' in pathlib.PurePosixPath(path).parts):
            raise ValueError('Unsafe original path: ' + path)
        row = dict(mode=stat.S_IMODE(node.mode), uid=node.uid, gid=node.gid,
                   mtime=int(node.mtime.timestamp()), type=node.type, inode=node.inode_number)
        if node.is_file():
            row['size'] = node.size
            with node.open() as stream:
                row['sha256'] = digest_stream(stream)
        elif node.is_symlink():
            row['target'] = node.link
        elif node.is_device():
            row['rdev'] = node.header.rdev
        elif not (node.is_dir() or node.is_fifo()):
            raise ValueError('Unsupported original inode type: ' + path)
        nodes[path], records[path] = node, row
        if node.is_dir():
            for child in sorted(node.iterdir(), key=lambda n: n.name):
                if child.name not in ('.', '..'):
                    visit(child, child.name if path == '.' else path + '/' + child.name)
    visit(fs.root, '.')
    return records, nodes


def link_groups(records):
    groups = collections.defaultdict(list)
    for path, row in records.items():
        if row['type'] != stat.S_IFDIR:
            groups[row['inode']].append(path)
    return sorted(sorted(paths) for paths in groups.values() if len(paths) > 1)


def overlays(root, originals, epoch):
    result, sources = {}, {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Overlay root must be a real directory')
    def visit(path):
        rel = path.relative_to(root).as_posix()
        host = path.lstat()
        kind = stat.S_IFMT(host.st_mode)
        if kind not in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK):
            raise ValueError('Overlay special files are not supported: ' + rel)
        old = originals.get(rel)
        if old and kind != old['type']:
            raise ValueError('Overlay cannot change existing inode type: ' + rel)
        if old and kind == stat.S_IFDIR:
            row = copy.deepcopy(old)
        elif old:
            row = copy.deepcopy(old)
            if kind == stat.S_IFREG:
                row.update(size=host.st_size, sha256=file_digest(path))
            else:
                row['target'] = path.readlink().as_posix()
        else:
            shared_library = '.so' in path.name and (path.name.endswith('.so') or '.so.' in path.name)
            mode = 0o755 if kind == stat.S_IFDIR or (kind == stat.S_IFREG and host.st_mode & 0o111 and not shared_library) else 0o644
            if kind == stat.S_IFLNK:
                mode = 0o777
            row = dict(mode=mode, uid=0, gid=0, mtime=epoch, type=kind, inode='overlay:' + rel)
            if kind == stat.S_IFREG:
                row.update(size=host.st_size, sha256=file_digest(path))
            elif kind == stat.S_IFLNK:
                row['target'] = path.readlink().as_posix()
        result[rel], sources[rel] = row, path
        if kind == stat.S_IFDIR:
            for child in sorted(path.iterdir()):
                visit(child)
    for child in sorted(root.iterdir()):
        visit(child)
    # Never silently split an original hardlink group by replacing only one name.
    for paths in link_groups(originals):
        changed = [p for p in paths if p in result and result[p] != originals[p]]
        if changed:
            if len(changed) != len(paths) or len({json.dumps({k:v for k,v in result[p].items() if k != 'inode'}, sort_keys=True) for p in paths}) != 1:
                raise ValueError('Overlay must replace every hardlink name consistently: ' + ', '.join(paths))
    return result, sources


def write_root_tar(destination, expected, nodes, sources):
    links = {}
    with tarfile.open(destination, 'w', format=tarfile.GNU_FORMAT) as archive:
        for path, row in sorted(expected.items()):
            if path == '.':
                continue  # Root attributes are passed explicitly to sqfstar.
            entry = tarfile.TarInfo(path)
            entry.mode, entry.uid, entry.gid, entry.mtime = row['mode'], row['uid'], row['gid'], row['mtime']
            kind = row['type']
            if kind != stat.S_IFDIR and row['inode'] in links:
                entry.type, entry.linkname = tarfile.LNKTYPE, links[row['inode']]
                archive.addfile(entry)
                continue
            if kind != stat.S_IFDIR:
                links[row['inode']] = path
            if kind == stat.S_IFDIR:
                entry.type = tarfile.DIRTYPE
            elif kind == stat.S_IFLNK:
                entry.type, entry.linkname = tarfile.SYMTYPE, row['target']
            elif kind == stat.S_IFREG:
                entry.type, entry.size = tarfile.REGTYPE, row['size']
                stream = sources[path].open('rb') if path in sources else nodes[path].open()
                with stream:
                    archive.addfile(entry, stream)
                continue
            elif kind == stat.S_IFIFO:
                entry.type = tarfile.FIFOTYPE
            elif kind in (stat.S_IFCHR, stat.S_IFBLK):
                entry.type = tarfile.CHRTYPE if kind == stat.S_IFCHR else tarfile.BLKTYPE
                # Linux encoded dev_t, independent of the macOS host encoding.
                dev = row['rdev']
                entry.devmajor = (dev >> 8) & 0xfff
                entry.devminor = (dev & 0xff) | ((dev >> 12) & 0xfff00)
            else:
                raise ValueError('Unsupported tar inode type: ' + path)
            archive.addfile(entry)


def validate(originals, expected, actual):
    missing, extra = sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))
    mismatches = {}
    for path in sorted(set(expected) & set(actual)):
        before = {k:v for k,v in expected[path].items() if k != 'inode'}
        after = {k:v for k,v in actual[path].items() if k != 'inode'}
        if before != after:
            mismatches[path] = dict(expected=before, actual=after)
    expected_links, actual_links = link_groups(expected), link_groups(actual)
    removed_original = sorted(set(originals) - set(actual))
    result = dict(passed=not (missing or extra or mismatches or removed_original or expected_links != actual_links),
                  expected_paths=len(expected), actual_paths=len(actual), removed_original_paths=removed_original,
                  missing_paths=missing, unexpected_paths=extra, mismatches=mismatches,
                  hardlink_groups=len(actual_links), hardlink_equivalence_preserved=expected_links == actual_links,
                  root_metadata_preserved=all(originals['.'][k] == actual.get('.', {}).get(k) for k in ('mode','uid','gid','mtime','type')),
                  files_hashed=sum(row['type'] == stat.S_IFREG for row in actual.values()),
                  symlinks_checked=sum(row['type'] == stat.S_IFLNK for row in actual.values()))
    return result


def rebuild(args):
    sys.path.insert(0, str(args.analysis / 'tools/python'))
    from dissect.squashfs import SquashFS
    inspector_spec = importlib.util.spec_from_file_location('inspect_update', args.analysis / 'inspect_update.py')
    inspector = importlib.util.module_from_spec(inspector_spec)
    inspector_spec.loader.exec_module(inspector)
    original_hash = file_digest(args.original)
    if original_hash != ORIGINAL_SHA256:
        raise ValueError('Original firmware SHA-256 differs from pinned V2.1 input')
    source_container = inspector.inspect(args.original)
    original_rootfs_size = source_container['payloads'][ROOTFS_MEMBER]['size']
    size_limit = args.max_rootfs_size if args.max_rootfs_size is not None else original_rootfs_size
    if size_limit <= 0 or (size_limit > original_rootfs_size and not args.allow_growth):
        raise ValueError('Rootfs growth requires --allow-growth and an explicit verified --max-rootfs-size')
    if args.allow_growth and args.max_rootfs_size is None:
        raise ValueError('--allow-growth requires an explicit verified --max-rootfs-size')
    if args.version == source_container['version'] or not args.version.startswith('V2.1-ND-'):
        raise ValueError('Use a distinct V2.1-ND- development version')
    if args.output.exists():
        raise ValueError('Output already exists; choose a new output path')
    args.output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='repack-', dir=args.output) as temporary:
        temp = pathlib.Path(temporary)
        with tarfile.open(args.original) as original_tar:
            original_members = {item.name: item for item in original_tar}
            kernel = original_tar.extractfile(KERNEL_MEMBER).read()
            original_fs_path = temp / 'original.squashfs'
            with original_fs_path.open('wb') as out, original_tar.extractfile(ROOTFS_MEMBER) as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                    out.write(chunk)
        with original_fs_path.open('rb') as stream:
            fs = SquashFS(stream)
            if fs.sb.xattr_id_table_start != -1:
                raise ValueError('Original contains xattrs: refuse to drop unimplemented metadata')
            originals, nodes = inventory(fs)
            overlay, sources = overlays(args.overlay, originals, fs.sb.mkfs_time)
            expected = copy.deepcopy(originals)
            expected.update(overlay)
            root_tar = temp / 'rootfs.tar'
            write_root_tar(root_tar, expected, nodes, sources)
            root = originals['.']
            command = [str(args.sqfstar), '-comp', 'lzo', '-b', '131072', '-processors', '2',
                       '-mem', '256M', '-no-progress', '-numeric-owner', '-no-xattrs',
                       '-mkfs-time', str(fs.sb.mkfs_time), '-root-mode', format(root['mode'], 'o'),
                       '-root-uid', str(root['uid']), '-root-gid', str(root['gid']),
                       '-root-time', str(root['mtime']), str(args.output / 'rootfs.squashfs')]
            with root_tar.open('rb') as tar_input, (args.output / 'squashfs-build.log').open('w') as log:
                subprocess.run(command, stdin=tar_input, stdout=log, stderr=subprocess.STDOUT, check=True)
        rebuilt_path = args.output / 'rootfs.squashfs'
        with rebuilt_path.open('rb') as stream:
            rebuilt_fs = SquashFS(stream)
            actual, unused = inventory(rebuilt_fs)
            validation = validate(originals, expected, actual)
            validation['compression_matches'] = rebuilt_fs.sb.compression == 3 and rebuilt_fs.block_size == 131072
            validation['passed'] &= validation['compression_matches']
            validation['image_size'] = rebuilt_path.stat().st_size
            validation['original_image_size'] = original_rootfs_size
            validation['size_limit'] = size_limit
            validation['within_size_limit'] = validation['image_size'] <= size_limit
            validation['no_growth_vs_original'] = validation['image_size'] <= original_rootfs_size
            validation['passed'] &= validation['within_size_limit']
        (args.output / 'expected-filesystem.json').write_text(json.dumps(expected, indent=2, sort_keys=True) + '\n')
        (args.output / 'actual-filesystem.json').write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n')
        (args.output / 'filesystem-validation.json').write_text(json.dumps(validation, indent=2) + '\n')
        if not validation['passed']:
            raise ValueError('Rebuilt filesystem validation failed; package not created')
        rootfs = rebuilt_path.read_bytes()
        info = ('Shanling M1 Plus\n' + args.version + '\n' + hashlib.md5(kernel).hexdigest() + '  ' + KERNEL_MEMBER + '\n' + hashlib.md5(rootfs).hexdigest() + '  ' + ROOTFS_MEMBER + '\n').encode()
        package = args.output / ('update-' + args.version + '-EXPERIMENTAL.bin')
        with tarfile.open(package, 'w', format=tarfile.GNU_FORMAT) as archive:
            for name, member in original_members.items():
                member = copy.copy(member)
                if member.isfile():
                    data = {INFO_MEMBER:info, KERNEL_MEMBER:kernel, ROOTFS_MEMBER:rootfs}[name]
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                else:
                    archive.addfile(member)
        checked = inspector.inspect(package)
        if checked['payloads'][KERNEL_MEMBER]['sha256'] != source_container['payloads'][KERNEL_MEMBER]['sha256']:
            raise ValueError('Kernel changed unexpectedly')
        if file_digest(args.original) != original_hash:
            raise ValueError('Original input changed while repacking')
        changed = sorted(path for path in originals if path in overlay and originals[path] != expected[path])
        result = dict(status='experimental-offline-validated', original_sha256=original_hash, original_unchanged=True,
                      kernel_unchanged=True, overlay_directory=str(args.overlay), added_paths=sorted(set(expected)-set(originals)),
                      changed_paths=changed, filesystem=validation, package=checked,
                      build=dict(sqfstar=str(args.sqfstar), sqfstar_sha256=file_digest(args.sqfstar), command=command,
                                 upstream_version='4.7.5', source_sha256='547b7b7f4d2e44bf91b6fc554664850c69563701deab9fd9cd7e21f694c88ea6',
                                 darwin_patch_sha256='3f3f568514c57fd50f508fef67e0e293a9668067801f42d4471b429a79bd1575'),
                      unknowns=['hardware flash capacity', 'recovery update acceptance of development version', 'device boot and native UI behavior', 'on-device streaming'])
        (args.output / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
        (args.output / 'EXPERIMENTAL-NOT-DEVICE-VERIFIED.txt').write_text('Offline container and filesystem consistency have been validated. Hardware capacity, updater acceptance, bootability and streaming on the M1 Plus have not been verified. This command does not install or flash the package.\n')
        print(json.dumps(dict(package=str(package), validation=str(args.output/'validation.json'), passed=True), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=pathlib.Path, default=PROJECT / 'inputs/update.bin')
    parser.add_argument('--analysis', type=pathlib.Path, default=DEFAULT_ANALYSIS)
    parser.add_argument('--overlay', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    parser.add_argument('--version', default='V2.1-ND-dev1')
    parser.add_argument('--max-rootfs-size', type=int, help='Explicit byte limit; defaults to original image length')
    parser.add_argument('--allow-growth', action='store_true', help='Allow an explicit larger byte limit only after capacity is independently verified')
    parser.add_argument('--sqfstar', type=pathlib.Path, default=BASE / 'tools/squashfs-tools-4.7.5/squashfs-tools/sqfstar')
    args = parser.parse_args()
    for name in ('original','analysis','overlay','output'):
        setattr(args, name, getattr(args, name).resolve())
    args.sqfstar = args.sqfstar.absolute()
    rebuild(args)

if __name__ == '__main__':
    main()
