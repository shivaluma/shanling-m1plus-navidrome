#!/usr/bin/env python3
"""Bounded static DTB/bootargs evidence extraction; never executes firmware."""
import argparse
import hashlib
import json
import pathlib
import re
import struct


def fdt(data):
    if len(data) < 40:
        raise ValueError('Short FDT header')
    magic, total, off_struct, off_strings, off_mem, version, compatible, cpu, strings_size, struct_size = struct.unpack_from('>10I', data)
    if magic != 0xd00dfeed or not 17 <= version <= 17 or compatible > version:
        raise ValueError('Not supported FDT v17')
    if total > len(data) or off_struct + struct_size > total or off_strings + strings_size > total or off_mem >= total:
        raise ValueError('FDT header bounds invalid')
    strings = data[off_strings:off_strings+strings_size]
    end = off_struct + struct_size
    at, stack, nodes = off_struct, [], {}
    while at + 4 <= end:
        token = struct.unpack_from('>I', data, at)[0]
        at += 4
        if token == 1:
            name_end = data.index(b'\0', at, end)
            name = data[at:name_end].decode('utf-8')
            at = (name_end + 4) & ~3
            path = ('/'.join(stack + [name])).replace('//','/') or '/'
            stack.append(name)
            nodes[path] = {}
        elif token == 2:
            if not stack:
                raise ValueError('FDT stack underflow')
            stack.pop()
        elif token == 3:
            length, name_off = struct.unpack_from('>II', data, at)
            at += 8
            if at + length > end or name_off >= len(strings):
                raise ValueError('FDT property out of bounds')
            prop = strings[name_off:strings.index(b'\0',name_off)].decode('ascii')
            raw = data[at:at+length]
            at = (at + length + 3) & ~3
            path = '/'.join(stack) or '/'
            nodes[path][prop] = raw
        elif token == 4:
            pass
        elif token == 9:
            if stack:
                raise ValueError('FDT unclosed node')
            return total, nodes
        else:
            raise ValueError('Invalid FDT token: '+str(token))
    raise ValueError('FDT missing end')


def render(raw):
    result = dict(hex=raw.hex())
    if raw and len(raw)%4 == 0:
        result['cells'] = list(struct.unpack('>'+str(len(raw)//4)+'I', raw))
    if raw.endswith(b'\0') and all(ch in b'\0\t\n\r' or 32 <= ch <= 126 for ch in raw):
        result['strings'] = raw.rstrip(b'\0').decode('ascii').split('\0')
    return result


def inspect(path):
    data = path.read_bytes()
    result = dict(source=str(path.resolve()), source_size=len(data), source_sha256=hashlib.sha256(data).hexdigest(), dtbs=[])
    start = 0
    while True:
        offset = data.find(b'\xd0\x0d\xfe\xed', start)
        if offset < 0:
            break
        start = offset + 4
        try:
            total, nodes = fdt(data[offset:])
        except (ValueError, struct.error, UnicodeError):
            continue
        record = dict(offset=offset, offset_hex=hex(offset), size=total,
                      sha256=hashlib.sha256(data[offset:offset+total]).hexdigest(),
                      nodes={path:{k:render(v) for k,v in props.items()} for path,props in nodes.items()})
        result['dtbs'].append(record)
    result['boot_argument_strings'] = [dict(offset=m.start(), offset_hex=hex(m.start()), text=m.group().decode('ascii'))
        for m in re.finditer(rb'[\x20-\x7e]{5,}', data) if any(term in m.group() for term in (b'mtdparts=',b'root=/dev/',b'ubi.mtd=',b'rootfstype='))]
    return result

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=pathlib.Path)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args=parser.parse_args()
    result=inspect(args.kernel)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print('Valid DTBs:',len(result['dtbs']))
    for dtb in result['dtbs']:
        print('DTB',dtb['offset_hex'],'size',dtb['size'],'nodes',len(dtb['nodes']))
        for path,props in dtb['nodes'].items():
            if any(term in path for term in ('chosen','sfc','partition','rootfs','kernel@')):
                print(path,json.dumps(props))
    print('Boot argument strings',result['boot_argument_strings'])
