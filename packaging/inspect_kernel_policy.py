#!/usr/bin/env python3
"""Recover bounded NaN-policy code evidence from the supplied expanded kernel."""
import argparse
import hashlib
import json
import pathlib
import re
import struct
import sys

BASE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(BASE.parent/'.work/analysis-v2.1/tools/python'))
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN


def inspect(path,out):
    data=path.read_bytes()
    expected='b85c34385bbe57b5a0b422a6d541a3cfff5108f63ca8918180924ec5ed0fe97f'
    if hashlib.sha256(data).hexdigest()!=expected:
        raise ValueError('Kernel differs from pinned analyzed V2.1 expanded payload')
    base=0x80010000
    label_offset=0xb6b92b
    if data[label_offset:label_offset+8]!=b'ieee754\0':
        raise ValueError('Early parameter string changed')
    entry_offset=0xb746ac
    entry=struct.unpack_from('<3I',data,entry_offset)
    if entry!=(base+label_offset,0x80b4a990,1):
        raise ValueError('Early parameter entry changed')
    spans=[('elf_nan_gate',0x800169c4,0x800169fc),
           ('elf_legacy_nan_gate',0x80016b74,0x80016b98),
           ('elf_error_returns',0x80016c1c,0x80016c3c),
           ('ieee754_setup',0x80b4a990,0x80b4aa78),
           ('nan_policy_update',0x80025a54,0x80025b08)]
    md=Cs(CS_ARCH_MIPS,CS_MODE_MIPS32|CS_MODE_LITTLE_ENDIAN)
    disassembly={}
    for name,start,end in spans:
        lines=[f'0x{i.address:08x}  {i.mnemonic:8s} {i.op_str}' for i in md.disasm(data[start-base:end-base],start)]
        disassembly[name]=dict(address_start=hex(start),address_end_exclusive=hex(end),
                               file_offset=hex(start-base),lines=lines)
    scans={}
    for needle in (b'IKCFG_ST',b'IKCFG_ED',b'ieee754=',b'mtdparts=',b'memsize'):
        scans[needle.decode()]=[hex(m.start()) for m in re.finditer(re.escape(needle),data)]
    strings={}
    for offset in (0x9f3a98,0x9f15c0,0x9f15c8,0x9f15d0):
        strings[hex(offset)]=data[offset:data.index(b'\0',offset)].decode('ascii')
    result=dict(source=str(path.resolve()),sha256=expected,size=len(data),
                flat_kernel_load_base_inferred=hex(base),base_evidence='Only tested base for which ieee754 string pointer matches early-parameter table; entry points to executable setup code matching four supported mode strings.',
                early_param_entry=dict(file_offset=hex(entry_offset),string_pointer=hex(entry[0]),setup_pointer=hex(entry[1]),early=entry[2]),
                policy_enable_addresses=dict(legacy='0x80b930d1',nan2008='0x80b930d0'),
                policy_mode_storage='0x80b93608',mode_storage_beyond_flat_image_end=0x80b93608>=base+len(data),
                marker_scans=scans,mode_strings=strings,disassembly=disassembly,
                unresolved=['live policy enable byte values','bootloader-supplied ieee754 mode','CPU probed NaN feature bits','physical and free RAM','stock vendor kernel source/config identity'])
    out.write_text(json.dumps(result,indent=2)+'\n')
    assembly=out.with_suffix('.asm')
    assembly.write_text('\n\n'.join(name+'\n'+'\n'.join(value['lines']) for name,value in disassembly.items())+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='disassembly'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('kernel',type=pathlib.Path);p.add_argument('--out',type=pathlib.Path,required=True);args=p.parse_args();inspect(args.kernel,args.out)
