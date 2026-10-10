#!/usr/bin/env python3
"""Inspect pinned local firmware and create an ignored analysis workspace."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = '1ccb4667e3e9bdf9a35be4dc58d09b898ca53c5fd58396b7667fb91fe895f925'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, default=ROOT / 'inputs/update.bin')
    args = parser.parse_args()
    source = args.original.resolve()
    if not source.is_file():
        raise SystemExit('Provide your own Shanling M1 Plus V2.1 update.bin with --original')
    if hashlib.sha256(source.read_bytes()).hexdigest() != EXPECTED:
        raise SystemExit('Firmware does not match the supported V2.1 SHA-256')
    inspector = load('inspect_update', ROOT / 'analysis/inspect_update.py')
    inspector.inspect(source)
    target = ROOT / 'inputs/update.bin'
    target.parent.mkdir(exist_ok=True)
    if source != target.resolve():
        if target.exists():
            raise SystemExit('inputs/update.bin already exists; inspect it before replacing it')
        shutil.copyfile(source, target)
    workspace = ROOT / '.work/analysis-v2.1'
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'analysis/inspect_update.py', workspace / 'inspect_update.py')
    package = workspace / 'package'
    package.mkdir(exist_ok=True)
    with tarfile.open(target) as archive:
        for name in ('recovery-update/rootfs.squashfs', 'recovery-update/xImage'):
            destination = package / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(name) as stream, destination.open('wb') as output:
                shutil.copyfileobj(stream, output)
    analyzer = load('analyze_firmware', ROOT / 'analysis/analyze.py')
    if not (workspace / 'rootfs-manifest.json').exists():
        if (workspace / 'rootfs').exists():
            raise SystemExit('Partial analysis workspace exists; inspect .work before retrying')
        analyzer.extract()
    print(json.dumps({'original_sha256': EXPECTED, 'workspace': str(workspace),
                      'scope': 'regular-file extraction and link inventory; never a rebuild source'}))


if __name__ == '__main__':
    main()
