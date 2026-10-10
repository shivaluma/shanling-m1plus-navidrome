#!/usr/bin/env python3
"""Fail build on architecture, imported symbol or glibc-version mismatches."""
import hashlib
import json
from pathlib import Path
import struct
import sys

if not __debug__:
    raise SystemExit('Compatibility audit requires Python without -O or PYTHONOPTIMIZE')

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / '.work/analysis-v2.1'
sys.path.insert(0, str(ANALYSIS / 'tools/python'))
from elftools.elf.elffile import ELFFile


def elf_info(path):
    with Path(path).open('rb') as f:
        elf = ELFFile(f)
        versions = {}
        vneed = elf.get_section_by_name('.gnu.version_r')
        if vneed:
            for version, auxs in vneed.iter_versions():
                for aux in auxs:
                    versions[aux['vna_other'] & 0x7fff] = (version.name, aux.name)
        vdef = elf.get_section_by_name('.gnu.version_d')
        if vdef:
            for version, auxs in vdef.iter_versions():
                auxs = list(auxs)
                versions[version['vd_ndx'] & 0x7fff] = ('', auxs[0].name)
        ver = elf.get_section_by_name('.gnu.version')
        dyn = elf.get_section_by_name('.dynsym')
        imports, exports = [], []
        if dyn:
            for i, sym in enumerate(dyn.iter_symbols()):
                if not sym.name:
                    continue
                index = ver.get_symbol(i)['ndx'] if ver else 0
                if isinstance(index, int):
                    index &= 0x7fff
                library, version = versions.get(index, ('', ''))
                entry = {'name': sym.name, 'version': version, 'library': library,
                         'weak': sym['st_info']['bind'] == 'STB_WEAK'}
                if sym['st_shndx'] == 'SHN_UNDEF':
                    imports.append(entry)
                else:
                    exports.append(entry)
        needed, soname, search_paths = [], '', []
        dynamic = elf.get_section_by_name('.dynamic')
        if dynamic:
            for t in dynamic.iter_tags():
                if t.entry.d_tag == 'DT_NEEDED':
                    needed.append(t.needed)
                if t.entry.d_tag == 'DT_SONAME':
                    soname = t.soname
                if t.entry.d_tag in ('DT_RPATH', 'DT_RUNPATH'):
                    search_paths.append(t.rpath if t.entry.d_tag == 'DT_RPATH' else t.runpath)
        abi = elf.get_section_by_name('.MIPS.abiflags')
        raw = abi.data() if abi else b''
        return {'path': str(path), 'class': elf.elfclass, 'little_endian': elf.little_endian,
                'machine': elf['e_machine'], 'type': elf['e_type'], 'flags': elf['e_flags'],
                'isa_level': raw[2] if raw else None, 'isa_rev': raw[3] if raw else None,
                'fp_abi': raw[7] if raw else None, 'needed': needed, 'soname': soname,
                'has_interpreter': any(s['p_type'] == 'PT_INTERP' for s in elf.iter_segments()),
                'has_dynamic': dynamic is not None, 'search_paths': search_paths, 'imports': imports, 'exports': exports,
                'size': Path(path).stat().st_size,
                'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def main():
    plugin, backend = (Path(x) for x in sys.argv[1:])
    p, b = elf_info(plugin), elf_info(backend)
    for info in (p, b):
        assert info['class'] == 32 and info['little_endian'] and info['machine'] == 'EM_MIPS'
    assert p['type'] == 'ET_DYN' and not p['has_interpreter']
    assert not p['search_paths'], 'Host paths must not be embedded in the new plugin'
    assert p['isa_level'] == 32 and p['isa_rev'] == 2 and p['fp_abi'] == 6
    assert p['flags'] & 0x400, 'Native plugin must match the firmware NaN2008 ABI'
    assert b['type'] == 'ET_EXEC' and not b['has_dynamic'] and not b['has_interpreter']
    assert b['fp_abi'] == 3, 'Backend must be statically linked softfloat'
    assert b['flags'] & 0x400, 'Backend must declare NaN2008 for the stock kernel policy'
    providers = {}
    for folder in (ANALYSIS / 'rootfs/lib', ANALYSIS / 'rootfs/usr/lib'):
        for path in folder.iterdir():
            if '.so' not in path.name or not path.is_file() or path.read_bytes()[:4] != b'\x7fELF':
                continue
            info = elf_info(path)
            if info['soname']:
                providers[info['soname']] = info
    for needed in p['needed']:
        assert needed in providers, 'Library absent from original firmware: ' + needed
    exported = {(entry['name'], entry['version']) for library in p['needed']
                for entry in providers[library]['exports']}
    unversioned = {name for name, version in exported}
    unresolved = []
    for entry in p['imports']:
        if entry['weak']:
            continue
        if entry['version']:
            okay = (entry['name'], entry['version']) in exported
        else:
            okay = entry['name'] in unversioned
        if not okay:
            unresolved.append(entry)
    assert not unresolved, 'Unresolved against original libraries: ' + repr(unresolved)
    names = {e['name'] for e in p['exports']}
    assert '__libc_start_main' in names and 'nd_ui_start' in names
    assert not any(n.startswith('cJSON_') for n in names), 'Vendored cJSON must not interpose stock code'
    report = {'native': p, 'backend': b, 'original_libraries_resolve_all_native_imports': True,
              'limitations': ['Static symbol compatibility does not prove runtime ABI of recovered toolkit APIs.',
                              'Go softfloat executable kernel acceptance and device memory use need hardware tests.']}
    target = ROOT / 'dist/elf-audit.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print('ELF audit passed:', len(p['imports']), 'imports resolved against original firmware libraries')

if __name__ == '__main__':
    main()
