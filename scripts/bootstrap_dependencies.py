#!/usr/bin/env python3
"""Fetch hash-pinned build inputs; firmware must be supplied separately."""
import argparse
import hashlib
from pathlib import Path
import platform
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CA = ('https://curl.se/ca/cacert-2026-09-25.pem',
      'a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505')
TOOLCHAINS = {
    'arm64': ('mipsel-unknown-linux-gnu-aarch64-darwin.tar.gz',
              'e3134121655af3995be0c59751b52cfd7a153c6874f745d2bdeca01b4a15d33e'),
    'x86_64': ('mipsel-unknown-linux-gnu-x86_64-darwin.tar.gz',
               '1bb23136e142f9900a4532a8ec59bb46a0b9240f687fe4dac0e5b29097181b1f'),
}


def fetch(url, path, digest):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.download')
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'M1PlusNavidromeBuild/0.1'})
            with urllib.request.urlopen(request, timeout=60) as source, temporary.open('wb') as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    output.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                raise ValueError('Downloaded input checksum mismatch')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Existing input checksum mismatch: ' + path.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', action='store_true', help='Also fetch GCC 15.2.0 for macOS')
    parser.add_argument('--toolchain-archive', type=Path, help='Use a local, hash-verified toolchain archive')
    args = parser.parse_args()
    fetch(CA[0], ROOT / 'vendor/ca/cacert.pem', CA[1])
    print('Pinned CA bundle verified')
    if not args.toolchain:
        return
    if platform.system() != 'Darwin' or platform.machine() not in TOOLCHAINS:
        raise SystemExit('Automatic toolchain bootstrap supports macOS arm64/x86_64; configure CROSS_COMPILE and MIPS_SYSROOT on other hosts')
    name, digest = TOOLCHAINS[platform.machine()]
    archive = args.toolchain_archive or ROOT / 'tools/downloads' / name
    if args.toolchain_archive:
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise SystemExit('Local toolchain archive checksum mismatch')
    else:
        fetch('https://github.com/messense/homebrew-macos-cross-toolchains/releases/download/v15.2.0/' + name,
              archive, digest)
    destination = ROOT / 'tools/toolchain'
    if destination.exists():
        raise SystemExit('tools/toolchain already exists; inspect it before replacing it')
    destination.mkdir(parents=True)
    with tarfile.open(archive) as source:
        source.extractall(destination, filter='data')
    print('Pinned GCC toolchain extracted locally')


if __name__ == '__main__':
    main()
