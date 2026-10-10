#!/usr/bin/env python3
"""Build MIPS artifacts against the supplied firmware, then stage guarded overlay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / '.work/analysis-v2.1'
FW = ANALYSIS / 'rootfs'
CROSS = os.environ.get('CROSS_COMPILE', str(ROOT / 'tools/toolchain/mipsel-unknown-linux-gnu/bin/mipsel-unknown-linux-gnu-'))
PINNED = {
    'vendor/cJSON.c': '298581a04a36c0165da4b0aade235c23088cb2faa58651d720ea2f3706ed0b0d',
    'vendor/cJSON.h': '25b0145150d500498e4d209cec69c18c42cf818bffcc54690be3b895a2a16dee',
    'vendor/ca/cacert.pem': 'a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505',
    'inputs/update.bin': '1ccb4667e3e9bdf9a35be4dc58d09b898ca53c5fd58396b7667fb91fe895f925',
    '.work/analysis-v2.1/rootfs/release/bin/demo': '29e23119bc2a0f153f3ea0ba942a39754d1aab0879c1040fae2269df8bd9b3f1',
}

def run(args, **kw):
    subprocess.run([str(a) for a in args], check=True, **kw)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--skip-backend', action='store_true', help='Use already-built dist/navidrome-service')
    ap.add_argument('--font-weight', choices=('medium', 'regular'), default='medium',
                    help='Be Vietnam Pro weight merged into the UI font')
    ap.add_argument('--stock-font', action='store_true', help='Keep the stock UI font (no Vietnamese)')
    args = ap.parse_args()
    for rel, expected in PINNED.items():
        if sha(ROOT / rel) != expected:
            raise SystemExit('Pinned input mismatch: ' + rel)
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    # Reconstruct only linker lookup symlinks; this directory is NOT a rootfs.
    linkdir = dist / 'link-libs'
    linkdir.mkdir(exist_ok=True)
    manifest = json.loads((ANALYSIS / 'rootfs-manifest.json').read_text())
    for item in manifest:
        path = item['path']
        if item['type'] == 'file' and (path.startswith('lib/') or path.startswith('usr/lib/')):
            if '.so' in Path(path).name:
                link = linkdir / Path(path).name
                if not link.exists():
                    link.symlink_to(FW / path)
    for item in manifest:
        if item['type'] == 'symlink' and item['path'].startswith(('lib/', 'usr/lib/')):
            name = Path(item['path']).name
            target = Path(item['target']).name
            if '.so' in name and (linkdir / target).exists() and not (linkdir / name).exists():
                (linkdir / name).symlink_to(target)
    if not args.skip_backend:
        go = shutil.which('go') or '/opt/homebrew/bin/go'
        env = dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH='mipsle', GOMIPS='softfloat')
        run([go, 'build', '-trimpath', '-ldflags=-s -w', '-o', dist / 'navidrome-service', '.'],
            cwd=ROOT / 'backend', env=env)
    # glibc's generated stubs header lists only unavailable functions, no ABI types.
    # The cross sysroot omits its NaN2008 variant; the same o32 feature list is
    # sufficient because we link and audit against the actual firmware libraries.
    shim = dist / 'build-include/gnu'
    shim.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(os.environ.get('MIPS_SYSROOT', str(ROOT / 'tools/toolchain/mipsel-unknown-linux-gnu/mipsel-unknown-linux-gnu/sysroot'))) / 'usr/include/gnu/stubs-o32_hard.h',
                    shim / 'stubs-o32_hard_2008.h')
    sources = sorted((ROOT / 'native').glob('*.c'))
    if len(sources) < 2:
        raise SystemExit('Native implementation is not ready')
    plugin = dist / 'libnavidrome_ui.so'
    flags = ['-std=gnu99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-fvisibility=hidden',
             '-mips32r2', '-mabi=32', '-mfp64', '-mnan=2008', '-shared', '-nostdlib',
             '-I' + str(dist / 'build-include'), '-I' + str(ROOT / 'vendor/curl/include'),
             '-I' + str(ROOT / 'vendor'), '-I' + str(ROOT / 'native'),
             '-Wl,-soname,libnavidrome_ui.so', '-Wl,-Bsymbolic', '-Wl,-z,defs',
             '-Wl,-z,relro', '-Wl,-z,now', '-Wl,--hash-style=both',
             '-Wl,-rpath-link,' + str(linkdir), '-L' + str(linkdir)]
    run([str(CROSS) + 'gcc', *flags, *sources, ROOT / 'vendor/cJSON.c',
         '-Wl,--no-as-needed', '-lc', '-ldl', '-lpthread', '-lm', '-lcurl', '-lgcc_s', '-o', plugin])
    run([str(CROSS) + 'strip', '--strip-unneeded', plugin])
    run([sys.executable, ROOT / 'scripts/patch_go_nan2008.py', dist / 'navidrome-service'])
    run([sys.executable, ROOT / 'scripts/audit_elf.py', plugin, dist / 'navidrome-service'])
    overlay = dist / 'rootfs'
    sd = dist / 'sdcard/navidrome'
    # A minimal rootfs hook keeps the update within the original image length.
    # All extension binaries and trust roots live on the removable SD card.
    if overlay.exists():
        shutil.rmtree(overlay)
    for base, rel, src, mode in [
        (sd, 'bin/navidrome-service', dist / 'navidrome-service', 0o755),
        (sd, 'lib/libnavidrome_ui.so', plugin, 0o644),
        (sd, 'collect-diagnostics.sh', ROOT / 'scripts/collect-diagnostics.sh', 0o644),
        (sd, 'ca-certificates.crt', ROOT / 'vendor/ca/cacert.pem', 0o644),
        (overlay, 'usr/bin/launch-navidrome', ROOT / 'scripts/launch-navidrome.sh', 0o755),
    ]:
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(mode)
    init = (FW / 'etc/init.d/S90play').read_bytes()
    old, new = b'/release/bin/demo &', b'/usr/bin/launch-navidrome &'
    if init.count(old) != 1:
        raise SystemExit('Unexpected stock S90play contents')
    dest = overlay / 'etc/init.d/S90play'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(init.replace(old, new))
    dest.chmod(0o775)  # original metadata, also preserved by packaging for replacements
    if not args.stock_font:
        # Replaces the system UI font in place: Vietnamese also renders in stock pages.
        run([sys.executable, ROOT / 'scripts/build_font.py', '--weight', args.font_weight,
             '--output', overlay / 'release/assets/default/raw/fonts/default.ttf'])
    report = {'source_sha256': PINNED['inputs/update.bin'], 'target': 'MIPS little-endian o32',
              'sd_payload_files': {str(p.relative_to(sd)): {'size': p.stat().st_size, 'sha256': sha(p)}
                                   for p in sorted(sd.rglob('*')) if p.is_file()},
              'files': {str(p.relative_to(overlay)): {'size': p.stat().st_size, 'sha256': sha(p)}
                        for p in sorted(overlay.rglob('*')) if p.is_file()}}
    (dist / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Built and audited MIPS artifacts; minimal rootfs overlay at', overlay)
    print('SD payload at', sd, '(configuration and enable marker are generated locally)')
    print('This confirms build compatibility checks, not hardware boot or audio playback.')

if __name__ == '__main__':
    main()
