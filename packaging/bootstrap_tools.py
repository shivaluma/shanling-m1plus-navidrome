#!/usr/bin/env python3
"""Build pinned upstream SquashFS tools locally; no global install."""
import hashlib
import pathlib
import platform
import shutil
import subprocess
import tarfile
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent
TOOLS = BASE / 'tools'
SOURCE = TOOLS / 'squashfs-tools-4.7.5'
DOWNLOADS = [
    ('squashfs-tools-4.7.5.tar.gz', 'https://github.com/plougher/squashfs-tools/archive/refs/tags/4.7.5.tar.gz', '547b7b7f4d2e44bf91b6fc554664850c69563701deab9fd9cd7e21f694c88ea6'),
    ('darwin.patch', 'https://github.com/plougher/squashfs-tools/commit/f88f4a659d6ab432a57e90fe2f6191149c6b343f.patch?full_index=1', '3f3f568514c57fd50f508fef67e0e293a9668067801f42d4471b429a79bd1575'),
]

def main():
    TOOLS.mkdir(exist_ok=True)
    for name, url, expected in DOWNLOADS:
        path = TOOLS / name
        if not path.exists():
            with urllib.request.urlopen(url) as response, path.open('wb') as output:
                shutil.copyfileobj(response, output)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit('Downloaded source checksum mismatch: ' + name)
    if not SOURCE.exists():
        with tarfile.open(TOOLS / DOWNLOADS[0][0]) as archive:
            # Pinned source archive, still reject links/paths escaping its directory.
            for item in archive:
                path = pathlib.PurePosixPath(item.name)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != SOURCE.name or item.issym() or item.islnk():
                    raise SystemExit('Unsafe source archive member: ' + item.name)
            archive.extractall(TOOLS, filter='data')
    marker = SOURCE / '.darwin-patch-applied'
    if platform.system() == 'Darwin' and not marker.exists():
        subprocess.run(['patch', '-p1', '-i', str(TOOLS / 'darwin.patch')], cwd=SOURCE, check=True)
        marker.write_text(DOWNLOADS[1][2] + '\n')
    flags = ['EXTRA_CFLAGS=-std=gnu99']
    if platform.system() == 'Darwin':
        prefix = subprocess.check_output(['brew', '--prefix'], text=True).strip()
        flags = ['EXTRA_CFLAGS=-std=gnu99 -I' + prefix + '/include',
                 'EXTRA_LDFLAGS=-L' + prefix + '/lib']
    command = ['make', '-j4', *flags, 'LZO_SUPPORT=1', 'LZ4_SUPPORT=1',
               'XZ_SUPPORT=1', 'LZMA_XZ_SUPPORT=1', 'ZSTD_SUPPORT=1', 'XATTR_SUPPORT=1']
    subprocess.run(command, cwd=SOURCE / 'squashfs-tools', check=True)
    subprocess.run([str(SOURCE / 'squashfs-tools/sqfstar'), '-version'], check=True)

if __name__ == '__main__':
    main()
