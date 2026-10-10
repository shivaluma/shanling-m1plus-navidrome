#!/usr/bin/env python3
"""Check local Markdown file links in tracked documentation."""
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
errors = []
for raw in tracked:
    if not raw or not raw.endswith(b'.md'):
        continue
    path = ROOT / raw.decode()
    content = re.sub(r'```.*?```', '', path.read_text(), flags=re.S)
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
        target = target.strip('<>')
        if urlsplit(target).scheme or target.startswith('#'):
            continue
        destination = (path.parent / unquote(target.split('#')[0])).resolve()
        if not destination.exists() or not destination.is_relative_to(ROOT):
            errors.append(str(path.relative_to(ROOT)) + ': ' + target)
if errors:
    raise SystemExit('\n'.join(errors))
print('Local Markdown links passed.')
