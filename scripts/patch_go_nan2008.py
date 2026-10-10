#!/usr/bin/env python3
"""Declare NaN2008 for this static softfloat Go ELF; preserve machine code."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

if not __debug__:
    raise SystemExit('Compatibility audit requires Python without -O or PYTHONOPTIMIZE')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.work/analysis-v2.1/tools/python'))
from elftools.elf.elffile import ELFFile


def main():
    path = Path(sys.argv[1])
    raw = path.read_bytes()
    with path.open('rb') as f:
        elf = ELFFile(f)
        assert elf.elfclass == 32 and elf.little_endian and elf['e_machine'] == 'EM_MIPS'
        assert elf['e_type'] == 'ET_EXEC' and elf.get_section_by_name('.dynamic') is None
        assert not any(s['p_type'] == 'PT_INTERP' for s in elf.iter_segments())
        abi = elf.get_section_by_name('.MIPS.abiflags').data()
        assert abi[7] == 3, 'Only static softfloat is eligible; never patch hardfloat binaries'
        assert b'GOMIPS=softfloat' in raw and b'CGO_ENABLED=0' in raw, 'Go build settings must agree'
        flags = elf['e_flags']
    assert flags in (0x50001004, 0x50001404), 'Unexpected Go MIPS ELF flags'
    report_path = path.parent / 'backend-nan2008-adjustment.json'
    if flags & 0x400:
        print('Backend already declares NaN2008')
        return
    before = path.parent / 'unadjusted/navidrome-service'
    before.parent.mkdir(exist_ok=True)
    shutil.copyfile(path, before)
    adjusted = bytearray(raw)
    struct.pack_into('<I', adjusted, 36, flags | 0x400)
    assert raw[:36] == adjusted[:36] and raw[40:] == adjusted[40:]
    path.write_bytes(adjusted)
    path.chmod(0o755)
    report = {'original_sha256': hashlib.sha256(raw).hexdigest(),
              'adjusted_sha256': hashlib.sha256(adjusted).hexdigest(),
              'old_flags': hex(flags), 'new_flags': hex(flags | 0x400),
              'only_changed_range': [36, 40], 'all_machine_code_unchanged': True,
              'reason': 'Declare NaN2008 to match stock userland and P5600 execution policy; Go softfloat uses integer-based IEEE754 arithmetic with matching quiet-NaN representation, no C ABI floating-point calls.',
              'evidence': ['docs/flash-capacity-evidence.md', 'docs/mips-runtime.md']}
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print('Declared NaN2008 for static softfloat backend; only ELF e_flags changed')

if __name__ == '__main__':
    main()
