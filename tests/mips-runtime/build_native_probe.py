#!/usr/bin/env python3
"""Link a minimal MIPS harness to original firmware libc and prepare local sysroot."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess

ROOT=pathlib.Path(__file__).resolve().parents[2]
HERE=pathlib.Path(__file__).resolve().parent
ANALYSIS=ROOT/'.work/analysis-v2.1'
CROSS=os.environ.get('CROSS_COMPILE', str(ROOT/'tools/toolchain/mipsel-unknown-linux-gnu/bin/mipsel-unknown-linux-gnu-'))
LINK=ROOT/'dist/link-libs'
OUT=HERE/'native-probe'
subprocess.run([str(CROSS)+'gcc','-std=gnu99','-O2','-Wall','-Wextra','-Werror','-fPIC','-mips32r2','-mfp64','-mnan=2008','-mabi=32','-no-pie','-nostdlib','-Wl,-e,_start','-Wl,--dynamic-linker=/lib/ld-linux-mipsn8.so.1','-Wl,--hash-style=both','-I'+str(ROOT/'dist/build-include'),'-L'+str(LINK),'-Wl,-rpath-link,'+str(LINK),str(HERE/'start.S'),str(HERE/'native_probe.c'),'-Wl,--no-as-needed','-lc','-ldl','-lpthread','-o',str(OUT)],check=True)
sysroot=HERE/'sysroot'
if sysroot.exists():shutil.rmtree(sysroot)
manifest=json.loads((ANALYSIS/'rootfs-manifest.json').read_text())
items={row['path']:row for row in manifest}
selected=[row for row in manifest if row['path'].startswith(('lib/','usr/lib/')) and '.so' in pathlib.PurePosixPath(row['path']).name]
for row in selected:
    if row['type']!='file':continue
    source=ANALYSIS/'rootfs'/row['path'];dest=sysroot/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Original library changed: '+row['path'])
    dest.write_bytes(raw);dest.chmod(int(row['mode'],8)&0o7777)
for row in selected:
    if row['type']!='symlink':continue
    dest=sysroot/row['path'];dest.parent.mkdir(parents=True,exist_ok=True);dest.symlink_to(row['target'])
report=dict(harness_sha256=hashlib.sha256(OUT.read_bytes()).hexdigest(),original_library_files_copied=sum(row['type']=='file' for row in selected),original_library_symlinks=sum(row['type']=='symlink' for row in selected),scope='Test sysroot only; never a firmware rebuild input')
(HERE/'native-build.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
