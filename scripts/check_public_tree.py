#!/usr/bin/env python3
"""Check tracked publication content; never scans local ignored configuration."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
patterns = [re.compile(rb'/Users/[A-Za-z0-9_.-]+/'),
            re.compile(rb'/home/[A-Za-z0-9_.-]+/'),
            re.compile(rb'gh[pousr]_[A-Za-z0-9]{20,}'),
            re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
            re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
errors = []
for raw in paths:
    if not raw:
        continue
    name = raw.decode()
    path = ROOT / name
    if path.is_symlink():
        errors.append(name + ': tracked symlink requires review')
        continue
    if name.startswith(('inputs/', '.work/', 'dist/', 'tools/', 'packaging/tools/')):
        errors.append(name + ': local build input/output')
    if path.name in ('config.json', 'config.local.json', '.env') or path.suffix in ('.bin', '.squashfs', '.dylib', '.so'):
        errors.append(name + ': private/generated artifact')
    if path.stat().st_size > 2 * 1024 * 1024:
        errors.append(name + ': unexpectedly large tracked file')
    data = path.read_bytes()
    if any(pattern.search(data) for pattern in patterns):
        errors.append(name + ': possible personal path or secret; inspect locally')
if errors:
    raise SystemExit('\n'.join(errors))
print('Tracked publication checks passed (heuristic; not a complete secret audit).')
